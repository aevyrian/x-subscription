import json
import hashlib
import re
import time
from pathlib import Path
from contextlib import nullcontext

from .store import target
from .avatars import avatar_url_ok

KINDS = {'.jpg': 'image', '.jpeg': 'image', '.png': 'image', '.webp': 'image',
         '.gif': 'image', '.avif': 'image', '.mp4': 'video', '.webm': 'video', '.mov': 'video'}


def safe_file(store, relative):
    relative = str(relative)
    legacy = relative.startswith('@legacy/')
    root = store.legacy if legacy else store.downloads
    if root is None:
        raise ValueError('旧归档只读卷未挂载')
    path = (root / (relative[8:] if legacy else relative)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('归档文件不存在')
    return path


def index_folder(store, user, legacy=False, db=None):
    root = store.legacy if legacy else store.downloads
    if root is None:
        raise ValueError('旧归档只读卷未挂载')
    folder = root / user
    prefix = '@legacy/' if legacy else ''
    if not folder.resolve().is_relative_to(root):
        raise ValueError('无效的归档目录')
    if not folder.is_dir():
        return 0, 0
    new_posts = new_media = 0
    # shortcut: scan one blogger after each job; use incremental indexing for very large archives.
    files = list(folder.iterdir())
    media_by_post = {}
    for path in files:
        match = re.fullmatch(r'(\d{1,25})_\d+\.[A-Za-z0-9]+', path.name)
        if match and path.suffix.lower() in KINDS:
            media_by_post.setdefault(match[1], []).append(path)
    with (store.connect() if db is None else nullcontext(db)) as db:
        for meta in files:
            match = re.fullmatch(r'(?:[A-Za-z0-9_]{1,50}_)?(\d{1,25})\.json', meta.name)
            if not match:
                continue
            try:
                safe_file(store, prefix + meta.relative_to(root).as_posix())
                data = json.loads(meta.read_text(encoding='utf-8'))
                identifier = str(data['tweet_id'])
                if identifier != match[1]:
                    continue
                author = data.get('author') or data.get('user') or {}
                handle = target(author.get('name') or user)['user']
                name = str(author.get('nick') or handle)[:100]
                content = str(data.get('content') or '')[:100000]
                date = str(data.get('date') or '')[:100]
            except (ValueError, KeyError, TypeError, AttributeError, OSError):
                continue
            store.add_blogger(handle, db)
            avatar = author.get('profile_image') or ''
            db.execute("UPDATE bloggers SET display_name=CASE WHEN display_name='' THEN ? ELSE display_name END WHERE user=?", (name, handle))
            if avatar_url_ok(avatar):
                db.execute('UPDATE bloggers SET avatar_url=? WHERE user=?', (avatar, handle))
            exists = db.execute('SELECT 1 FROM posts WHERE id=?', (identifier,)).fetchone()
            db.execute('''INSERT INTO posts(id,user,display_name,content,posted_at,url,added)
                          VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                          user=excluded.user,display_name=excluded.display_name,
                          content=excluded.content,posted_at=excluded.posted_at,url=excluded.url''',
                       (identifier, handle, name, content, date, f'https://x.com/{handle}/status/{identifier}', time.time()))
            new_posts += not bool(exists)
            for media in media_by_post.get(identifier, []):
                relative = prefix + media.relative_to(root).as_posix()
                try:
                    checked = safe_file(store, relative)
                except ValueError:
                    continue
                inserted = db.execute('INSERT OR IGNORE INTO media(post_id,path,kind,size) VALUES (?,?,?,?)',
                                      (identifier, relative, KINDS[media.suffix.lower()], checked.stat().st_size)).rowcount
                new_media += inserted
    return new_posts, new_media


def blogger_match(q):
    name = q.strip().lower()[:200]
    handle = name.removeprefix('@') or name
    return '''(instr(lower(b.user),?)>0 OR instr(lower(b.display_name),?)>0 OR EXISTS
               (SELECT 1 FROM posts history WHERE history.user=b.user AND instr(lower(history.display_name),?)>0))''', [handle, name, name]


def list_bloggers(store, q='', user='', featured=False, limit=None):
    q, user = q.strip()[:200], user.strip().removeprefix('@').lower()
    where, args = [], []
    if q:
        condition, values = blogger_match(q)
        where.append(condition)
        args.extend(values)
    if user:
        where.append('b.user=?')
        args.append(user)
    if featured:
        where.append('b.featured=1')
    clause = ' WHERE ' + ' AND '.join(where) if where else ''
    ordering = 'b.featured DESC,b.created DESC,b.user'
    order_args = []
    if q:
        ordering = 'CASE WHEN b.user=? THEN 0 ELSE 1 END,' + ordering
        order_args.append(q.lower().removeprefix('@'))
    with store.connect() as db:
        db.execute('BEGIN')
        total = db.execute('SELECT COUNT(*) n FROM bloggers b' + clause, args).fetchone()['n']
        sql = '''SELECT b.*,
                 (SELECT COUNT(*) FROM posts p WHERE p.user=b.user) post_count,
                 (SELECT COUNT(*) FROM media m JOIN posts p ON p.id=m.post_id WHERE p.user=b.user) media_count,
                 (SELECT COUNT(*) FROM posts p WHERE p.user=b.user AND p.favorite=1) favorite_count,
                 (SELECT p.posted_at FROM posts p WHERE p.user=b.user ORDER BY length(p.id) DESC,p.id DESC LIMIT 1) last_post_at,
                 COALESCE(s.enabled,0) subscribed,COALESCE(s.enabled,0) enabled,s.schedule,s.next_run,
                 a.id active_id,a.status active_status,a.next_run active_next_run,a.source active_source,a.error active_error
                 FROM bloggers b LEFT JOIN subscriptions s ON s.user=b.user
                 LEFT JOIN tasks a ON a.id=(SELECT t.id FROM tasks t WHERE t.user=b.user AND t.post_id IS NULL
                     AND t.status IN ('queued','running','retry')
                     ORDER BY CASE t.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,t.created,t.id LIMIT 1)'''
        query_args = [*args, *order_args]
        sql += clause + ' ORDER BY ' + ordering
        if limit is not None:
            sql += ' LIMIT ?'
            query_args.append(max(1, limit))
        rows = [dict(row) for row in db.execute(sql, query_args)]
    for row in rows:
        active = {key: row.pop('active_' + key) for key in ('id', 'status', 'next_run', 'source', 'error')}
        row['active_task'] = active if active['id'] else None
    return {'items': rows, 'total': total}


def list_posts(store, q='', user='', post='', favorite=False, tag='', kind='', limit=30, offset=0,
               order='timeline', seed='', snapshot=0):
    q, user = q.strip()[:200], user.strip().removeprefix('@').lower()
    where, args = [], []
    if q:
        condition, values = blogger_match(q)
        where.append('''(instr(lower(content),?)>0 OR instr(lower(display_name),?)>0 OR instr(lower(user),?)>0
                        OR instr(lower(tags),?)>0 OR instr(lower(note),?)>0
                        OR user IN (SELECT b.user FROM bloggers b WHERE ''' + condition + '))')
        args.extend([q.lower(), q.lower(), q.lower().removeprefix('@') or q.lower(), q.lower(), q.lower(), *values])
    for field, value in (('user', user), ('id', post)):
        if value:
            where.append(field + '=?')
            args.append(value)
    if favorite:
        where.append('favorite=1')
    if tag:
        where.append("(',' || tags || ',') LIKE ?")
        args.append('%,' + tag[:60] + ',%')
    if kind in ('image', 'video'):
        where.append('EXISTS (SELECT 1 FROM media m WHERE m.post_id=posts.id AND kind=?)')
        args.append(kind)
    snapshot = snapshot if snapshot > 0 else time.time()
    where.append('added<=?')
    args.append(snapshot)
    clause = ' WHERE ' + ' AND '.join(where) if where else ''
    total = store.query('SELECT COUNT(*) n FROM posts' + clause, args)[0]['n']
    with store.connect() as db:
        ordering = 'length(id) DESC,id DESC'
        if order == 'oldest':
            ordering = 'length(id),id'
        if order == 'random':
            db.create_function('shuffle_rank', 1, lambda identifier: hashlib.blake2b((seed[:64] + ':' + identifier).encode(), digest_size=8).hexdigest(), deterministic=True)
            ordering = 'shuffle_rank(id),id'
        rows = [dict(r) for r in db.execute('SELECT * FROM posts' + clause + ' ORDER BY ' + ordering + ' LIMIT ? OFFSET ?',
                                           args + [max(1, min(limit, 100)), max(0, offset)])]
    if rows:
        placeholders = ','.join('?' for _ in rows)
        media = store.query(f'SELECT * FROM media WHERE post_id IN ({placeholders}) ORDER BY id', [r['id'] for r in rows])
        grouped = {}
        for item in media:
            item['url'] = '/api/v1/media/' + str(item['id'])
            grouped.setdefault(item['post_id'], []).append(item)
        for row in rows:
            row['media'] = grouped.get(row['id'], [])
    result = {'items': rows, 'total': total, 'snapshot': snapshot}
    if q or user:
        bloggers = list_bloggers(store, q=q, user=user, limit=20)
        result.update(bloggers=bloggers['items'], blogger_total=bloggers['total'])
    return result
