import json
import hashlib
import re
import time
from pathlib import Path

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


def index_folder(store, user, legacy=False):
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
    with store.connect() as db:
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


def list_posts(store, q='', user='', post='', favorite=False, tag='', kind='', limit=30, offset=0,
               order='timeline', seed='', snapshot=0):
    where, args = [], []
    if q:
        where.append('(content LIKE ? OR display_name LIKE ? OR user LIKE ? OR tags LIKE ? OR note LIKE ?)')
        args.extend(['%' + q[:200] + '%'] * 5)
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
    return {'items': rows, 'total': total, 'snapshot': snapshot}
