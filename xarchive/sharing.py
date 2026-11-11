import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse

from .library import safe_file

STATIC = Path(__file__).resolve().parent / 'static'


def share_origin():
    origin = os.environ.get('XARCHIVE_SHARE_ORIGIN', '').rstrip('/')
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            raise ValueError('分享站点需要 HTTPS 根地址')
    return origin


def public_request_allowed(request, origin):
    if not origin or (request.url.hostname or '').rstrip('.') != urlsplit(origin).hostname.rstrip('.'):
        return True
    # The public hostname never exposes login, settings or archive browsing.
    return request.method in ('GET', 'HEAD') and bool(re.fullmatch(
        r'/s/[A-Za-z0-9_-]{32}(?:/data|/media/\d+)?|/static/(?:viewer|share)\.(?:js|css)', request.url.path))


def register_sharing(app, store, admin, origin):
    def shared_post(token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{32}', token):
            raise HTTPException(404, '分享不存在或已停止')
        rows = store.query('''SELECT p.id,p.user,p.display_name,p.content,p.posted_at,p.url
                              FROM shares s JOIN posts p ON p.id=s.post_id WHERE s.token=?''', (token,))
        if not rows:
            raise HTTPException(404, '分享不存在或已停止')
        return rows[0]

    def status(identifier):
        if not store.query('SELECT 1 FROM posts WHERE id=?', (identifier,)):
            raise HTTPException(404, '帖子不存在')
        rows = store.query('SELECT token FROM shares WHERE post_id=?', (identifier,))
        return {'enabled': bool(origin), 'active': bool(rows),
                'url': origin + '/s/' + rows[0]['token'] if rows and origin else ''}

    @app.get('/api/v1/posts/{identifier}/share')
    def share_status(identifier: str, _=Depends(admin)):
        return status(identifier)

    @app.post('/api/v1/posts/{identifier}/share')
    def create_share(identifier: str, _=Depends(admin)):
        if not origin:
            raise HTTPException(503, '外网分享入口尚未连接')
        status(identifier)
        store.execute('INSERT OR IGNORE INTO shares(post_id,token,created) VALUES (?,?,?)',
                      (identifier, secrets.token_urlsafe(24), time.time()))
        return status(identifier)

    @app.delete('/api/v1/posts/{identifier}/share')
    def revoke_share(identifier: str, _=Depends(admin)):
        store.execute('DELETE FROM shares WHERE post_id=?', (identifier,))
        return {'ok': True}

    @app.get('/s/{token}')
    def public_page(token: str):
        shared_post(token)
        return FileResponse(STATIC / 'share.html')

    @app.get('/s/{token}/data')
    def public_data(token: str):
        post = shared_post(token)
        post['media'] = store.query('SELECT id,kind FROM media WHERE post_id=? AND size>0 ORDER BY id', (post['id'],))
        for media in post['media']:
            media['url'] = f'/s/{token}/media/{media["id"]}'
        return post

    @app.get('/s/{token}/media/{identifier}')
    def public_media(token: str, identifier: int, download: bool = False):
        post = shared_post(token)
        rows = store.query('SELECT path FROM media WHERE id=? AND post_id=?', (identifier, post['id']))
        if not rows:
            raise HTTPException(404, '媒体不存在')
        try:
            path = safe_file(store, rows[0]['path'])
        except ValueError:
            raise HTTPException(404, '媒体文件不可用')
        return FileResponse(path, filename=path.name if download else None)
