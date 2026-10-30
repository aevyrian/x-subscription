import json
import os
import re
import sqlite3
import time
import uuid
import calendar
import random
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

RESERVED = set('home explore search settings i notifications messages compose intent login logout signup tos privacy jobs about download'.split())
ACTIVE = ('queued', 'running', 'retry')
SCHEDULES = ('once', 'daily', 'three_days', 'weekly', 'monthly', 'quarterly', 'yearly')
LOCAL_TIME = timezone(timedelta(hours=8))
TASK_FILTERS = {'all': (), 'active': ACTIVE, 'attention': ('failed', 'retry', 'awaiting_confirmation'),
                'done': ('succeeded',), 'confirmation': ('awaiting_confirmation',)}


def in_update_window(timestamp):
    return 8 <= datetime.fromtimestamp(timestamp, LOCAL_TIME).hour < 18


def work_minute(day):
    return day.replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(minutes=random.randrange(600))


def next_work_window(timestamp):
    if in_update_window(timestamp):
        return timestamp
    current = datetime.fromtimestamp(timestamp, LOCAL_TIME)
    return work_minute(current + timedelta(days=current.hour >= 18)).timestamp()


def spread_updates(rows, timestamp, days):
    rows = list(rows)
    random.shuffle(rows)
    tomorrow = datetime.fromtimestamp(timestamp, LOCAL_TIME).replace(hour=8, minute=0, second=0, microsecond=0) + timedelta(days=1)
    days = max(1, days)
    minutes = days * 600
    # Separate random draws into slots so the whole queue cannot land on one day.
    for index, row in enumerate(rows):
        if len(rows) >= days:
            offset = index * days // len(rows) * 600 + random.randrange(600)
        else:
            low, high = index * minutes // len(rows), (index + 1) * minutes // len(rows)
            offset = random.randrange(low, max(low + 1, high))
        yield row, (tomorrow + timedelta(days=offset // 600, minutes=offset % 600)).timestamp()


def task_selection(task_filter, q):
    if task_filter not in TASK_FILTERS or not isinstance(q, str):
        raise ValueError('无效的任务筛选条件')
    where, args = [], []
    if q.strip():
        query = q.strip().lower()
        if re.fullmatch(r'@[a-z0-9_]{1,15}', query):
            where.append('user=?')
            args.append(query[1:])
        else:
            where.append("instr(lower(user || ' ' || COALESCE(post_id,'') || ' ' || id),?)>0")
            args.append(query)
    statuses = TASK_FILTERS[task_filter]
    if statuses:
        where.append('status IN (' + ','.join('?' for _ in statuses) + ')')
        args.extend(statuses)
    return (' WHERE ' + ' AND '.join(where) if where else ''), args


def next_update(schedule, timestamp=None):
    if schedule not in SCHEDULES:
        raise ValueError('无效的更新频率')
    if schedule == 'once':
        return 0
    current = datetime.fromtimestamp(time.time() if timestamp is None else timestamp, LOCAL_TIME)
    if schedule in ('monthly', 'quarterly', 'yearly'):
        months = {'monthly': 1, 'quarterly': 3, 'yearly': 12}[schedule]
        year, month = divmod(current.year * 12 + current.month - 1 + months, 12)
        current = current.replace(year=year, month=month + 1,
                                  day=min(current.day, calendar.monthrange(year, month + 1)[1]))
    else:
        current += timedelta(days={'daily': 1, 'three_days': 3, 'weekly': 7}[schedule])
    return work_minute(current).timestamp()


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
                    schedule TEXT NOT NULL DEFAULT 'quarterly',
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
            db.execute('BEGIN IMMEDIATE')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(subscriptions)')}
            if 'schedule' not in columns:
                db.execute("ALTER TABLE subscriptions ADD COLUMN schedule TEXT NOT NULL DEFAULT 'monthly'")
                for row in db.execute('SELECT user,interval_minutes FROM subscriptions').fetchall():
                    cadence = {1440:'daily', 10080:'weekly'}.get(row['interval_minutes'], 'monthly')
                    db.execute('UPDATE subscriptions SET schedule=?,limit_count=0,next_run=? WHERE user=?',
                               (cadence, next_update(cadence), row['user']))
            if 'scanned_posts' not in {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}:
                db.execute('ALTER TABLE tasks ADD COLUMN scanned_posts INTEGER NOT NULL DEFAULT 0')
            if 'generation' not in {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}:
                db.execute('ALTER TABLE tasks ADD COLUMN generation INTEGER NOT NULL DEFAULT 0')
            task_columns = {r['name'] for r in db.execute('PRAGMA table_info(tasks)')}
            for name, declaration in (
                ('task_kind', "TEXT NOT NULL DEFAULT 'legacy'"), ('crawl_mode', "TEXT NOT NULL DEFAULT 'auto'"),
                ('history_choice', "TEXT NOT NULL DEFAULT ''"), ('total_posts', 'INTEGER'),
                ('media_posts', 'INTEGER NOT NULL DEFAULT 0'), ('media_complete', 'INTEGER NOT NULL DEFAULT 0'),
                ('probe_complete', 'INTEGER NOT NULL DEFAULT 0'), ('cursor', 'TEXT'), ('since_id', 'TEXT'),
                ('cooldown_scope', "TEXT NOT NULL DEFAULT ''"), ('cooldown_reason', "TEXT NOT NULL DEFAULT ''"),
                ('cooldown_until', 'REAL NOT NULL DEFAULT 0')):
                if name not in task_columns:
                    db.execute(f'ALTER TABLE tasks ADD COLUMN {name} {declaration}')
            if 'task_kind' not in task_columns:
                db.execute('DROP INDEX tasks_active')
                db.execute("""CREATE UNIQUE INDEX tasks_active ON tasks(url,task_kind)
                              WHERE status IN ('queued','running','retry','awaiting_confirmation')""")
            db.execute('''CREATE TABLE IF NOT EXISTS crawl_posts (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                tweet_id TEXT NOT NULL, payload TEXT NOT NULL, has_media INTEGER NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0, UNIQUE(task_id,tweet_id))''')
            blogger_columns = {r['name'] for r in db.execute('PRAGMA table_info(bloggers)')}
            for name, declaration in (('avatar_url', "TEXT NOT NULL DEFAULT ''"), ('featured', 'INTEGER NOT NULL DEFAULT 0')):
                if name not in blogger_columns:
                    db.execute(f'ALTER TABLE bloggers ADD COLUMN {name} {declaration}')
            if 'last_success_at' not in blogger_columns:
                db.execute('ALTER TABLE bloggers ADD COLUMN last_success_at REAL')
                db.execute("""UPDATE bloggers SET last_success_at=(SELECT MAX(t.finished) FROM tasks t
                              WHERE t.user=bloggers.user AND t.post_id IS NULL AND t.status='succeeded')""")
            for name, declaration in (('history_status', "TEXT NOT NULL DEFAULT 'legacy'"), ('high_water_id', 'TEXT')):
                if name not in blogger_columns:
                    db.execute(f'ALTER TABLE bloggers ADD COLUMN {name} {declaration}')
            if not db.execute("SELECT 1 FROM settings WHERE key='all_downloads_v2'").fetchone():
                db.execute("INSERT INTO settings VALUES ('all_downloads_v2','true')")
                db.execute("INSERT INTO settings VALUES ('timeout_minutes','0') ON CONFLICT(key) DO UPDATE SET value='0'")
            if not db.execute("SELECT 1 FROM settings WHERE key='quarterly_schedule_v1'").fetchone():
                now = time.time()
                current = datetime.fromtimestamp(now, LOCAL_TIME)
                end = datetime.fromtimestamp(next_update('quarterly', now), LOCAL_TIME)
                days = max(1, (end.date() - current.date()).days - 1)
                rows = db.execute('SELECT user FROM subscriptions').fetchall()
                for row, due in spread_updates(rows, now, days):
                    db.execute("UPDATE subscriptions SET schedule='quarterly',limit_count=0,next_run=? WHERE user=?", (due, row['user']))
                db.execute("INSERT INTO settings VALUES ('default_schedule','\"quarterly\"') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
                db.execute("INSERT INTO settings VALUES ('quarterly_schedule_v1','true')")

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

    @staticmethod
    def _cooldown_until(db):
        row = db.execute("SELECT value FROM settings WHERE key='x_rate_limit_until'").fetchone()
        return float(json.loads(row['value'])) if row else 0

    @staticmethod
    def _cookie_revision(db):
        row = db.execute("SELECT value FROM settings WHERE key='x_cookie_revision'").fetchone()
        return int(json.loads(row['value'])) if row else 0

    def add_blogger(self, user, db):
        db.execute("INSERT OR IGNORE INTO bloggers(user,created,history_status) VALUES (?,?,'new')", (user, time.time()))

    @staticmethod
    def _history_baseline(db, user, created):
        baseline = max(0, int(created * 1000 - 1288834974657)) << 22
        latest = db.execute('SELECT id FROM posts WHERE user=? ORDER BY LENGTH(id) DESC,id DESC LIMIT 1', (user,)).fetchone()
        return str(min(baseline, int(latest['id'])) if latest else baseline)

    def submit(self, value, limit=0, source='website', db=None):
        parsed = target(value)
        if type(limit) is not int or not 0 <= limit <= 10000:
            raise ValueError('无效的下载范围')
        if db is None:
            with self.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                return self.submit(value, limit, source, connection)
        self.add_blogger(parsed['user'], db)
        blogger = db.execute('SELECT * FROM bloggers WHERE user=?', (parsed['user'],)).fetchone()
        history = db.execute("SELECT MIN(created) created FROM tasks WHERE user=? AND task_kind='history'", (parsed['user'],)).fetchone()['created']
        has_history = history is not None
        kind = 'single' if parsed['post_id'] else 'incremental' if blogger['history_status'] != 'new' or source in ('scheduled', 'update') and has_history else 'history'
        existing = db.execute("""SELECT * FROM tasks WHERE user=? AND post_id IS ?
                              AND (task_kind=? OR task_kind='legacy')
                              AND status IN ('queued','running','retry','awaiting_confirmation')""",
                              (parsed['user'], parsed['post_id'], kind)).fetchone()
        if existing:
            if source != 'scheduled' and existing['source'] == 'scheduled' and existing['status'] in ('queued', 'retry'):
                db.execute('UPDATE tasks SET source=?,next_run=? WHERE id=?', (source, time.time(), existing['id']))
                existing = db.execute('SELECT * FROM tasks WHERE id=?', (existing['id'],)).fetchone()
            return dict(existing), True
        identifier, now = uuid.uuid4().hex, time.time()
        since_id = None
        if kind == 'history' and not blogger['high_water_id']:
            db.execute('UPDATE bloggers SET high_water_id=? WHERE user=?',
                       (self._history_baseline(db, parsed['user'], history if has_history else now), parsed['user']))
        if kind == 'incremental':
            latest = db.execute('SELECT id FROM posts WHERE user=? ORDER BY LENGTH(id) DESC,id DESC LIMIT 1', (parsed['user'],)).fetchone()
            since_id = blogger['high_water_id'] or (self._history_baseline(db, parsed['user'], history) if has_history else
                latest['id'] if latest else str(max(0, int((blogger['created'] * 1000 - 1288834974657))) << 22))
            # Partial downloads must not move the next task past unfinished tweets.
            db.execute('UPDATE bloggers SET high_water_id=COALESCE(high_water_id,?) WHERE user=?', (since_id, parsed['user']))
        db.execute('''INSERT INTO tasks(id,user,post_id,url,limit_count,source,next_run,created,task_kind,since_id)
                      VALUES (?,?,?,?,?,?,?,?,?,?)''',
                   (identifier, parsed['user'], parsed['post_id'], parsed['url'], limit, source, now, now, kind, since_id))
        return dict(db.execute('SELECT * FROM tasks WHERE id=?', (identifier,)).fetchone()), False

    def subscribe(self, value, schedule='quarterly'):
        parsed = target(value)
        if parsed['post_id']:
            raise ValueError('订阅需要博主主页')
        due = next_update(schedule)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("""UPDATE bloggers SET history_status='new' WHERE user=? AND history_status='legacy'
                          AND NOT EXISTS (SELECT 1 FROM subscriptions WHERE user=bloggers.user)""", (parsed['user'],))
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

    def schedule(self, timestamp=None):
        now = time.time() if timestamp is None else timestamp
        if not in_update_window(now):
            return 0
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self._cooldown_until(db) > now:
                return 0
            self._spread_overdue(db, now)
            dispatch = db.execute("SELECT value FROM settings WHERE key IN ('scheduled_dispatch_after','scheduled_claim_after')").fetchall()
            if any(json.loads(row['value']) > now for row in dispatch):
                return 0
            sub = db.execute("SELECT * FROM subscriptions WHERE enabled=1 AND schedule!='once' AND next_run<=? ORDER BY next_run,user LIMIT 1", (now,)).fetchone()
            if not sub:
                return 0
            task, duplicate = self.submit(sub['user'], 0, 'scheduled', db)
            if not duplicate:
                db.execute('UPDATE tasks SET next_run=?,created=? WHERE id=?', (now, now, task['id']))
            db.execute('UPDATE subscriptions SET next_run=?,last_task=? WHERE user=?',
                       (next_update(sub['schedule'], now), task['id'], sub['user']))
            db.execute("INSERT INTO settings VALUES ('scheduled_dispatch_after',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (json.dumps(now + random.randint(15, 30) * 60),))
            return int(not duplicate)

    def recover(self, timestamp=None):
        now = time.time() if timestamp is None else timestamp
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("""UPDATE tasks SET status='queued', started=NULL, finished=NULL, error=NULL,
                          generation=generation+1,attempts=MAX(attempts-1,0),
                          next_run=CASE WHEN source='scheduled' THEN ? ELSE ? END WHERE status='running'""",
                       (next_work_window(now), now))
            self._spread_overdue(db, now)

    def _spread_overdue(self, db, now):
        midnight = datetime.fromtimestamp(now, LOCAL_TIME).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        rows = [('subscription', row['user']) for row in db.execute("SELECT user FROM subscriptions WHERE enabled=1 AND schedule!='once' AND next_run<?", (midnight,))]
        rows += [('task', row['id']) for row in db.execute("SELECT id FROM tasks WHERE source='scheduled' AND status IN ('queued','retry') AND next_run<?", (midnight,))]
        for (kind, identifier), due in spread_updates(rows, now, 3):
            if kind == 'subscription':
                db.execute('UPDATE subscriptions SET next_run=? WHERE user=?', (due, identifier))
            else:
                db.execute('UPDATE tasks SET next_run=? WHERE id=?', (due, identifier))

    def claim(self, timestamp=None):
        now = time.time() if timestamp is None else timestamp
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if self._cooldown_until(db) > now:
                return None
            dispatch = db.execute("SELECT value FROM settings WHERE key='scheduled_claim_after'").fetchone()
            automatic_allowed = in_update_window(now) and (not dispatch or json.loads(dispatch['value']) <= now)
            row = db.execute("""SELECT * FROM tasks WHERE status IN ('queued','retry') AND next_run<=?
                                AND cooldown_until<=? AND (source!='scheduled' OR ?)
                                ORDER BY CASE task_kind WHEN 'incremental' THEN 0 WHEN 'single' THEN 1 ELSE 2 END,
                                next_run,created LIMIT 1""", (now, now, automatic_allowed)).fetchone()
            if not row:
                return None
            db.execute("""UPDATE tasks SET status='running',attempts=attempts+1,generation=generation+1,started=?,
                          finished=NULL,error=NULL WHERE id=?""", (now, row['id']))
            if row['source'] == 'scheduled':
                db.execute("INSERT INTO settings VALUES ('scheduled_claim_after',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (json.dumps(now + random.randint(15, 30) * 60),))
            task = dict(db.execute('SELECT * FROM tasks WHERE id=?', (row['id'],)).fetchone())
            task['cookie_revision'] = self._cookie_revision(db)
            return task

    def cancel(self, identifier):
        return self.execute("""UPDATE tasks SET status='cancelled',finished=?
                              WHERE id=? AND status IN ('queued','running','retry','failed','awaiting_confirmation')""", (time.time(), identifier))

    def confirm_history(self, identifier, choice):
        if choice not in ('all', 'recent300', 'skip'):
            raise ValueError('无效的历史抓取选择')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM tasks WHERE id=? AND status='awaiting_confirmation' AND task_kind='history'", (identifier,)).fetchone()
            if not row:
                raise ValueError('任务不存在或已不再等待确认')
            now = time.time()
            db.execute('''UPDATE tasks SET history_choice=?,status=?,next_run=?,finished=?,error=NULL
                          WHERE id=?''', (choice, 'cancelled' if choice == 'skip' else 'queued',
                                          max(now, row['cooldown_until']), now if choice == 'skip' else None, identifier))
            if choice == 'skip':
                db.execute("UPDATE bloggers SET history_status='skipped' WHERE user=?", (row['user'],))
            return dict(db.execute('SELECT * FROM tasks WHERE id=?', (identifier,)).fetchone())

    def retry(self, identifier):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM tasks WHERE id=? AND status IN ('failed','cancelled','retry')", (identifier,)).fetchone()
            if not row:
                raise ValueError('只能重试失败、取消或等待重试的任务')
            if not self._retry_task(db, row):
                raise ValueError('该对象已有活动任务')

    def _retry_task(self, db, row):
        if row['task_kind'] == 'history' and row['history_choice'] == 'skip':
            return 0
        if db.execute("""SELECT 1 FROM tasks WHERE url=? AND (task_kind=? OR task_kind='legacy' OR ?='legacy') AND id!=?
                       AND status IN ('queued','running','retry','awaiting_confirmation')""", (row['url'], row['task_kind'], row['task_kind'], row['id'])).fetchone():
            return 0
        status = 'awaiting_confirmation' if row['task_kind'] == 'history' and row['media_posts'] > 1000 and not row['history_choice'] else 'queued'
        return db.execute("""UPDATE tasks SET status=?,source='website',attempts=0,next_run=?,finished=NULL,
                            started=NULL,error=NULL WHERE id=?""", (status, max(time.time(), row['cooldown_until']), row['id'])).rowcount

    def delete_task(self, identifier):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM tasks WHERE id=?', (identifier,)).fetchone()
            if not row:
                return 0
            if row['status'] not in ('succeeded', 'failed', 'cancelled'):
                raise ValueError('活动任务请先取消，再删除记录')
            db.execute('UPDATE subscriptions SET last_task=NULL WHERE last_task=?', (identifier,))
            return db.execute('DELETE FROM tasks WHERE id=?', (identifier,)).rowcount

    def list_tasks(self, task_filter='all', q='', limit=100, offset=0):
        query_where, query_args = task_selection('all', q)
        where, args = task_selection(task_filter, q)
        with self.connect() as db:
            db.execute('BEGIN')
            grouped = {row['status']: row['n'] for row in db.execute('SELECT status,COUNT(*) n FROM tasks' + query_where + ' GROUP BY status', query_args)}
            counts = {name: sum(grouped.values()) if not statuses else sum(grouped.get(status, 0) for status in statuses)
                      for name, statuses in TASK_FILTERS.items()}
            items = [dict(row) for row in db.execute('''SELECT t.*,(SELECT COUNT(*) FROM posts p WHERE p.user=t.user) archive_posts
                                                       FROM tasks t''' + where + ' ORDER BY created DESC,id DESC LIMIT ? OFFSET ?',
                                                      [*args, max(1, min(limit, 200)), max(0, offset)])]
            return {'items': items, 'total': counts[task_filter], 'counts': counts,
                    'cooldown_until': self._cooldown_until(db)}

    def bulk_tasks(self, action, task_filter='all', q=''):
        if action not in ('cancel', 'retry', 'delete'):
            raise ValueError('无效的批量操作')
        where, args = task_selection(task_filter, q)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT * FROM tasks' + where + ' ORDER BY created DESC,id DESC', args).fetchall()
            affected = 0
            for row in rows:
                if action == 'cancel' and row['status'] in (*ACTIVE, 'failed', 'awaiting_confirmation'):
                    affected += db.execute("UPDATE tasks SET status='cancelled',finished=? WHERE id=?", (time.time(), row['id'])).rowcount
                elif action == 'retry' and row['status'] in ('failed', 'cancelled', 'retry'):
                    affected += self._retry_task(db, row)
                elif action == 'delete' and row['status'] in ('succeeded', 'failed', 'cancelled'):
                    db.execute('UPDATE subscriptions SET last_task=NULL WHERE last_task=?', (row['id'],))
                    affected += db.execute('DELETE FROM tasks WHERE id=?', (row['id'],)).rowcount
            return {'affected': affected, 'skipped': len(rows) - affected, 'matched': len(rows)}

    def finish(self, task, result=None, error=None, retry_after=None, cooldown_scope='shared'):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            now = time.time()
            if error:
                limited = retry_after is not None
                status = 'retry' if limited or task['attempts'] < task['max_attempts'] else 'failed'
                delay = max(1, retry_after) if limited else 30 * 2 ** (task['attempts'] - 1)
                current_cookie = self._cookie_revision(db)
                same_account = task.get('cookie_revision', current_cookie) == current_cookie
                if limited and not same_account:
                    delay, error = 1, 'X 登录设置已更新，使用新设置重试'
                due = now + delay
                if task['source'] == 'scheduled':
                    due = next_work_window(due)
                db.execute('''UPDATE tasks SET status=?,error=?,attempts=MAX(attempts-?,0),next_run=?,finished=?,
                              cooldown_scope=?,cooldown_reason=?,cooldown_until=?
                              WHERE id=? AND status='running' AND generation=? ''',
                           (status, error, int(limited), due,
                            now, cooldown_scope if limited else '', error if limited else '',
                            now + delay if limited and same_account else 0, task['id'], task['generation']))
                if limited and same_account and cooldown_scope != 'task':
                    until = max(self._cooldown_until(db), now + delay)
                    db.execute("INSERT INTO settings VALUES ('x_rate_limit_until',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (json.dumps(until),))
            else:
                posts, media = result or (0, 0)
                changed = db.execute("""UPDATE tasks SET status='succeeded',new_posts=new_posts+?,
                              new_media=new_media+?,finished=?,error=NULL WHERE id=? AND status='running' AND generation=?""",
                           (posts, media, now, task['id'], task['generation'])).rowcount
                if changed and task['post_id'] is None:
                    db.execute('UPDATE bloggers SET last_success_at=? WHERE user=?', (now, task['user']))
                    current = db.execute('SELECT * FROM tasks WHERE id=?', (task['id'],)).fetchone()
                    if current['task_kind'] == 'history':
                        db.execute('UPDATE bloggers SET history_status=? WHERE user=?',
                                   ('recent300' if current['history_choice'] == 'recent300' else 'complete', task['user']))
                        db.execute('UPDATE bloggers SET high_water_id=COALESCE(high_water_id,?) WHERE user=?',
                                   (self._history_baseline(db, task['user'], current['created']), task['user']))
                    newest = db.execute('SELECT tweet_id FROM crawl_posts WHERE task_id=? AND completed=1 ORDER BY LENGTH(tweet_id) DESC,tweet_id DESC LIMIT 1', (task['id'],)).fetchone() if current['task_kind'] != 'history' else None
                    if newest:
                        old = db.execute('SELECT high_water_id FROM bloggers WHERE user=?', (task['user'],)).fetchone()['high_water_id']
                        if old is None or int(newest['tweet_id']) > int(old):
                            db.execute('UPDATE bloggers SET high_water_id=? WHERE user=?', (newest['tweet_id'], task['user']))
