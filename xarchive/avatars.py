import hashlib
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def avatar_url_ok(url):
    if not isinstance(url, str) or len(url) > 2048:
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname == 'pbs.twimg.com'
                and not parsed.username and not parsed.password and parsed.port in (None, 443)
                and parsed.path.startswith('/profile_images/'))
    except (TypeError, ValueError):
        return False


class AvatarRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not avatar_url_ok(newurl):
            raise ValueError('头像跳转地址不可用')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def avatar_file(store, user):
    if not re.fullmatch(r'[a-z0-9_]{1,15}', user):
        raise ValueError('无效的博主名称')
    rows = store.query('SELECT avatar_url FROM bloggers WHERE user=?', (user,))
    url = rows[0]['avatar_url'] if rows else ''
    if not avatar_url_ok(url):
        return None
    folder = store.config / 'avatars'
    folder.mkdir(exist_ok=True)
    name = user + '-' + hashlib.sha256(url.encode()).hexdigest()[:16]
    path = folder / (name + '.image')
    if path.is_file():
        return path
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        handlers = [AvatarRedirect()]
        proxy = store.setting('x_proxy')
        if proxy:
            handlers.append(urllib.request.ProxyHandler(proxy if isinstance(proxy,dict) else {'http':proxy,'https':proxy}))
        with urllib.request.build_opener(*handlers).open(request, timeout=8) as response:
            if response.headers.get_content_type() not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif'):
                return None
            data = response.read(2 * 1024 * 1024 + 1)
        if not data or len(data) > 2 * 1024 * 1024:
            return None
        # A unique temporary file avoids partial reads during simultaneous requests.
        import tempfile
        import os
        fd, temporary = tempfile.mkstemp(dir=folder, prefix=name)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
            Path(temporary).replace(path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return path
    except (OSError, ValueError, urllib.error.URLError):
        return None
