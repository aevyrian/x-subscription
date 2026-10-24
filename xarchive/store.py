import json
import os
import re
import sqlite3
import time
import uuid
import calendar
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

RESERVED = set('home explore search settings i notifications messages compose intent login logout signup tos privacy jobs about download'.split())
ACTIVE = ('queued', 'running', 'retry')
SCHEDULES = ('once', 'daily', 'weekly', 'monthly')
LOCAL_TIME = timezone(timedelta(hours=8))


def next_update(schedule, timestamp=None):
    if schedule not in SCHEDULES:
        raise ValueError('更新频率只支持一次性、每天、每周或每月')
    if schedule == 'once':
        return 0
    current = datetime.fromtimestamp(time.time() if timestamp is None else timestamp, LOCAL_TIME)
    if schedule == 'monthly':
        year, month = current.year + (current.month == 12), current.month % 12 + 1
        return current.replace(year=year, month=month,
                               day=min(current.day, calendar.monthrange(year, month)[1])).timestamp()
    return (current + timedelta(days=1 if schedule == 'daily' else 7)).timestamp()


def target(value):
    """Normalize the same X objects for the website and ScriptCat."""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('请输入 X 博主或帖子链接')
    value = value.strip()
    if re.fullmatch(r'@?[A-Za-z0-9_]{1,15}', value):
        value = 'https://x.com/' + value.lstrip('@')
    try:
        u = urlsplit(value)
        p = u.path.strip('/').split('/')
        if (u.scheme not in ('http', 'https') or u.hostname not in
                ('x.com', 'www.x.com', 'twitter.com', 'www.twitter.com', 'mobile.twitter.com')
                or u.username or u.password or u.port not in (None, 80, 443)
                or not re.fullmatch(r'[A-Za-z0-9_]{1,15}', p[0]) or p[0].lower() in RESERVED):
            raise ValueError()
        user = p[0].lower()
        if len(p) == 1 or (len(p) == 2 and p[1] in ('media', 'tweets', 'timeline', 'with_replies')):
            return dict(user=user, post_id=None, url=f'https://x.com/{user}/timeline')
        if (len(p) in (3, 5) and p[1] == 'status' and re.fullmatch(r'[0-9]{1,25}', p[2])
                and (len(p) == 3 or (p[3] in ('photo', 'video') and p[4].isdigit()))):
            return dict(user=user, post_id=p[2], url=f'https://x.com/{user}/status/{p[2]}')
    except (ValueError, IndexError):
        pass
    raise ValueError('仅支持 X / Twitter 博主主页或帖子链接')


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.config = self.root / 'config'
        self.downloads = self.root / 'downloads'
        self.legacy = Path(os.environ['XARCHIVE_LEGACY_MEDIA']).resolve() if os.environ.get('XARCHIVE_LEGACY_MEDIA') else None
        for p in (self.config, self.downloads):
            p.mkdir(parents=True, exist_ok=True)
        self.path = self.config / 'archive.sqlite3'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS bloggers (
                    user TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, user TEXT NOT NULL, post_id TEXT, url TEXT NOT NULL,
                    limit_count INTEGER NOT NULL, source TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3, next_run REAL NOT NULL,
                    created REAL NOT NULL, started REAL, finished REAL,
                    progress INTEGER NOT NULL DEFAULT 0, new_posts INTEGER NOT NULL DEFAULT 0,
                    new_media INTEGER NOT NULL DEFAULT 0, error TEXT);
                CREATE INDEX IF NOT EXISTS tasks_queue ON tasks(status,next_run,created);
                CREATE UNIQUE INDEX IF NOT EXISTS tasks_active ON tasks(url)
                    WHERE status IN ('queued','running','retry');
                CREATE TABLE IF NOT EXISTS subscriptions (
                    user TEXT PRIMARY KEY REFERENCES bloggers(user), enabled INTEGER NOT NULL DEFAULT 1,
                    interval_minutes INTEGER NOT NULL DEFAULT 43200, limit_count INTEGER NOT NULL DEFAULT 0,
                    schedule TEXT NOT NULL DEFAULT 'monthly',
                    next_run REAL NOT NULL, last_task TEXT);
                CREATE TABLE IF NOT EXISTS posts (
                    id TEXT PRIMARY KEY, user TEXT NOT NULL, display_name TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL DEFAULT '', posted_at TEXT NOT NULL DEFAULT '', url TEXT NOT NULL,
                    favorite INTEGER NOT NULL DEFAULT 0, tags TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '', added REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS posts_user ON posts(user);
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY, post_id TEXT NOT NULL REFERENCES posts(id),
                    path TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, size INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS media_post ON media(post_id,kind);
                CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS clients (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, digest TEXT NOT NULL UNIQUE, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS shares (
                    post_id TEXT PRIMARY KEY REFERENCES posts(id), token TEXT NOT NULL UNIQUE, created REAL NOT NULL);
            ''')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(subscriptions)')}
            if 'schedule' not in columns:
                db.execute("ALTER TABLE subscriptions ADD COLUMN schedule TEXT NOT NULL DEFAULT 'monthly'")
                for row in db.execute('SELECT user,interval_minutes FROM subscriptions').fetchall():
                    cadence = {1440:'daily', 10080:'weekly'}.get(row['interval_minutes'], 'monthly')
                    db.execute('UPDATE subscriptions SET schedule=?,limit_count=0,next_run=? WHERE user=?',
                               (cadence, next_update(cadence), row['user']))
            if 'scanned_posts' not in {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}:
                db.execute('ALTER TABLE tasks ADD COLUMN scanned_posts INTEGER NOT NULL DEFAULT 0')
            blogger_columns = {r['name'] for r in db.execute('PRAGMA table_info(bloggers)')}
            for name, declaration in (('avatar_url', "TEXT NOT NULL DEFAULT ''"), ('featured', 'INTEGER NOT NULL DEFAULT 0')):
                if name not in blogger_columns:
                    db.execute(f'ALTER TABLE bloggers ADD COLUMN {name} {declaration}')
            if not db.execute("SELECT 1 FROM settings WHERE key='all_downloads_v2'").fetchone():
                db.execute("INSERT INTO settings VALUES ('all_downloads_v2','true')")
                db.execute("INSERT INTO settings VALUES ('timeout_minutes','0') ON CONFLICT(key) DO UPDATE SET value='0'")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def query(self, sql, args=()):
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def execute(self, sql, args=()):
        with self.connect() as db:
            return db.execute(sql, args).rowcount

    def setting(self, key, default=None):
        rows = self.query('SELECT value FROM settings WHERE key=?', (key,))
        return json.loads(rows[0]['value']) if rows else default

    def set_setting(self, key, value):
        self.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                     (key, json.dumps(value)))

    def add_blogger(self, user, db):
        db.execute('INSERT OR IGNORE INTO bloggers(user,created) VALUES (?,?)', (user, time.time()))

    def submit(self, value, limit=0, source='website', db=None):
        parsed = target(value)
        if type(limit) is not int or not 0 <= limit <= 10000:
            raise ValueError('无效的下载范围')
        if db is None:
            with self.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                return self.submit(value, limit, source, connection)
        existing = db.execute("SELECT * FROM tasks WHERE user=? AND post_id IS ? AND status IN ('queued','running','retry')",
                              (parsed['user'], parsed['post_id'])).fetchone()
        if existing:
            return dict(existing), True
        self.add_blogger(parsed['user'], db)
        identifier, now = uuid.uuid4().hex, time.time()
        db.execute('''INSERT INTO tasks(id,user,post_id,url,limit_count,source,next_run,created)
                      VALUES (?,?,?,?,?,?,?,?)''',
                   (identifier, parsed['user'], parsed['post_id'], parsed['url'], limit, source, now, now))
        return dict(db.execute('SELECT * FROM tasks WHERE id=?', (identifier,)).fetchone()), False

    def subscribe(self, value, schedule='monthly'):
        parsed = target(value)
        if parsed['post_id']:
            raise ValueError('订阅需要博主主页')
        due = next_update(schedule)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            task, duplicate = self.submit(parsed['url'], 0, 'subscription', db)
            db.execute('''INSERT INTO subscriptions(user,enabled,limit_count,next_run,last_task,schedule)
                          VALUES (?,?,?,?,?,?) ON CONFLICT(user) DO UPDATE SET enabled=excluded.enabled,
                          limit_count=0,next_run=excluded.next_run,last_task=excluded.last_task,schedule=excluded.schedule''',
                       (parsed['user'], schedule != 'once', 0, due, task['id'], schedule))
        return task, duplicate

    def edit_subscription(self, user, schedule, enabled=True):
        due = next_update(schedule)
        return self.execute('UPDATE subscriptions SET schedule=?,enabled=?,limit_count=0,next_run=? WHERE user=?',
                            (schedule, enabled and schedule != 'once', due, user))

    def schedule(self):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for sub in db.execute("SELECT * FROM subscriptions WHERE enabled=1 AND schedule!='once' AND next_run<=?", (now,)).fetchall():
                task, _ = self.submit(sub['user'], 0, 'subscription', db)
                db.execute('UPDATE subscriptions SET next_run=?,last_task=? WHERE user=?',
                           (next_update(sub['schedule'], now), task['id'], sub['user']))

    def recover(self):
        self.execute("""UPDATE tasks SET status='queued', started=NULL, finished=NULL, error=NULL,
                        attempts=MAX(attempts-1,0),next_run=? WHERE status='running'""", (time.time(),))

    def claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("""SELECT * FROM tasks WHERE status IN ('queued','retry') AND next_run<=?
                                ORDER BY created LIMIT 1""", (time.time(),)).fetchone()
            if not row:
                return None
            db.execute("""UPDATE tasks SET status='running',attempts=attempts+1,started=?,
                          finished=NULL,error=NULL,progress=0 WHERE id=?""", (time.time(), row['id']))
            return dict(db.execute('SELECT * FROM tasks WHERE id=?', (row['id'],)).fetchone())

    def cancel(self, identifier):
        return self.execute("""UPDATE tasks SET status='cancelled',finished=?
                              WHERE id=? AND status IN ('queued','running','retry')""", (time.time(), identifier))

    def retry(self, identifier):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM tasks WHERE id=? AND status IN ('failed','cancelled')", (identifier,)).fetchone()
            if not row:
                raise ValueError('只能重试失败或取消的任务')
            existing = db.execute("SELECT id FROM tasks WHERE url=? AND status IN ('queued','running','retry')", (row['url'],)).fetchone()
            if existing:
                raise ValueError('该对象已有活动任务')
            db.execute("""UPDATE tasks SET status='queued',attempts=0,next_run=?,finished=NULL,
                          started=NULL,error=NULL WHERE id=?""", (time.time(), identifier))

    def finish(self, task, result=None, error=None):
        with self.connect() as db:
            if error:
                status = 'retry' if task['attempts'] < task['max_attempts'] else 'failed'
                db.execute('''UPDATE tasks SET status=?,error=?,next_run=?,finished=?
                              WHERE id=? AND status='running' ''',
                           (status, error, time.time() + 30 * 2 ** (task['attempts'] - 1),
                            time.time(), task['id']))
            else:
                posts, media = result or (0, 0)
                db.execute("""UPDATE tasks SET status='succeeded',new_posts=new_posts+?,
                              new_media=new_media+?,finished=?,error=NULL WHERE id=? AND status='running'""",
                           (posts, media, time.time(), task['id']))
