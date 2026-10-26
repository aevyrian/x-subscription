import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from typing import Literal
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .store import Store, target
from .worker import Worker
from .sharing import register_sharing, share_origin, public_request_allowed

STATIC = Path(__file__).resolve().parent / 'static'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(value, salt):
    return hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Password(Body):
    password: SecretStr = Field(min_length=8, max_length=256)


Schedule = Literal['once', 'daily', 'three_days', 'weekly', 'monthly', 'quarterly', 'yearly']
TaskFilter = Literal['all', 'active', 'confirmation', 'attention', 'done']


class Intake(Body):
    url: str = Field(min_length=1, max_length=2048)
    mode: str = Field(default='subscribe', pattern='^(download|update|blogger|subscribe|once)$')
    schedule: Schedule = 'quarterly'
    # Older installed widgets send these fields; the smart policy controls scope.
    limit: int = Field(default=0, ge=0, le=10000)
    interval_minutes: int | None = None


class BloggerEdit(Body):
    display_name: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=2000)
    featured: bool | None = None


class SubscriptionEdit(Body):
    enabled: bool = True
    schedule: Schedule = 'quarterly'


class TaskBulk(Body):
    action: Literal['cancel', 'retry', 'delete']
    filter: TaskFilter = 'all'
    q: str = Field(default='', max_length=200)


class HistoryConfirm(Body):
    choice: Literal['all', 'recent300', 'skip']


class ClientNew(Body):
    name: str = Field(min_length=1, max_length=60)


class SettingsEdit(Body):
    cookies: SecretStr | None = Field(default=None, max_length=100000)
    clear_cookies: bool = False
    x_proxy: SecretStr | None = Field(default=None, max_length=2048)
    clear_proxy: bool = False
    default_schedule: Schedule = 'quarterly'


class PostEdit(Body):
    favorite: bool | None = None
    tags: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=4000)


def create_app(data_dir=None, run_worker=True, runner=None):
    store = Store(data_dir or os.environ.get('XARCHIVE_DATA', 'data'))
    from .engine import Engine, parse_cookies
    from .library import list_bloggers, list_posts, safe_file
    if runner is None and run_worker:
        runner = Engine(store)
    worker = Worker(store, runner)
    login_lock = threading.Lock()
    failures = {}
    connection_lock = threading.Lock()
    connection_failures = {}
    public_origin = share_origin()

    @asynccontextmanager
    async def lifespan(app):
        if run_worker:
            worker.start()
        yield
        if run_worker:
            worker.stop()

    app = FastAPI(title='X Archive', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.worker = store, worker

    @app.exception_handler(ValueError)
    async def bad_value(request, exc):
        return JSONResponse({'error': str(exc)}, status_code=400)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse({'error': exc.detail}, status_code=exc.status_code)

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def invalid_body(request, exc):
        # Pydantic's default response can echo passwords or Cookie input.
        return JSONResponse({'error': '输入格式不正确，请检查字段和范围'}, status_code=422)

    @app.middleware('http')
    async def guards(request, call_next):
        if not public_request_allowed(request, public_origin):
            return JSONResponse({'error': '页面不存在'}, status_code=404)
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            if int(request.headers.get('content-length', '0') or 0) > 131072:
                return JSONResponse({'error': '请求过大'}, status_code=413)
            origin = request.headers.get('origin')
            expected = str(request.base_url).rstrip('/')
            # Bearer credentials are explicit, not ambient browser cookies; GM requests may carry X's Origin.
            if origin and origin != expected and not request.headers.get('authorization', '').startswith('Bearer '):
                return JSONResponse({'error': '不接受跨站网页请求；浮窗请使用连接凭据'}, status_code=403)
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 131072:
                    return JSONResponse({'error': '请求过大'}, status_code=413)
                chunks.append(chunk)
            # Starlette's middleware replays _body after the request stream has been consumed.
            request._body = b''.join(chunks)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        if request.url.path.startswith('/s/'):
            response.headers['Cache-Control'] = 'private, no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        return response

    def identity(request: Request):
        header = request.headers.get('authorization', '')
        if header.startswith('Bearer '):
            token = header[7:]
            if token.startswith('xarchive.'):
                try:
                    if len(token) > 1400:
                        raise ValueError()
                    encoded = token[9:]
                    token = base64.b64decode(encoded+'='*(-len(encoded)%4), altchars=b'-_', validate=True).decode('utf-8')
                except (ValueError, UnicodeError):
                    raise HTTPException(401, '连接密码格式不正确，请重新填写')
            if len(token) <= 256:
                rows = store.query('SELECT id FROM clients WHERE digest=?', (digest(token),))
                if rows:
                    return 'scriptcat:' + rows[0]['id']
                value = store.setting('connection_password')
                if value and len(token) >= 8:
                    address = request.client.host if request.client else ''
                    with connection_lock:
                        now = time.time()
                        recent = [t for t in connection_failures.get(address, []) if t > now-300]
                        if len(recent) >= 10:
                            raise HTTPException(429, '连接尝试过多，请 5 分钟后再试')
                        if hmac.compare_digest(password_hash(token, value['salt']), value['hash']):
                            connection_failures.pop(address, None)
                            return 'scriptcat:password'
                        connection_failures[address] = recent + [now]
            # An invalid explicit password must not fall back to a browser's login cookie.
            raise HTTPException(401, '连接密码不正确或已更改，请重新填写')
        session = request.cookies.get('session', '')
        if session and len(session) <= 256 and store.query('SELECT 1 FROM sessions WHERE digest=? AND expires>?', (digest(session), time.time())):
            return 'website'
        raise HTTPException(401, '请先登录或填写连接凭据')

    def admin(who=Depends(identity)):
        if who != 'website':
            raise HTTPException(403, '此操作需要网站登录')
        return who

    def session(response, request):
        value = secrets.token_urlsafe(32)
        store.execute('DELETE FROM sessions WHERE expires<=?', (time.time(),))
        store.execute('INSERT INTO sessions VALUES (?,?)', (digest(value), time.time() + 7 * 86400))
        response.set_cookie('session', value, httponly=True, samesite='strict', secure=request.url.scheme == 'https', max_age=7 * 86400)

    @app.get('/health')
    def health():
        store.query('SELECT 1')
        worker_ok = worker.healthy()
        healthy = not run_worker or worker_ok
        return JSONResponse({'status': 'ok' if healthy else 'degraded', 'worker': worker_ok},
                            status_code=200 if healthy else 503)

    @app.get('/api/v1/auth')
    def auth_state(request: Request):
        try:
            logged_in = identity(request) == 'website'
        except HTTPException:
            logged_in = False
        return {'setup_required': not bool(store.setting('admin')), 'logged_in': logged_in}

    @app.post('/api/v1/auth/setup')
    def setup(body: Password, request: Request, response: Response):
        with login_lock:
            if store.setting('admin'):
                raise HTTPException(409, '网站已初始化，请登录')
            salt = secrets.token_hex(16)
            value = {'salt': salt, 'hash': password_hash(body.password.get_secret_value(), salt)}
            with store.connect() as db:
                import json
                try:
                    db.execute('INSERT INTO settings VALUES (?,?)', ('admin', json.dumps(value)))
                except sqlite3.IntegrityError:
                    raise HTTPException(409, '网站已初始化，请登录')
        session(response, request)
        return {'ok': True}

    @app.post('/api/v1/auth/login')
    def login(body: Password, request: Request, response: Response):
        with login_lock:
            now = time.time()
            address = request.client.host if request.client else ''
            recent = [t for t in failures.get(address, []) if t > now - 300]
            if len(recent) >= 10:
                raise HTTPException(429, '登录尝试过多，请 5 分钟后再试')
            value = store.setting('admin')
            if not value or not hmac.compare_digest(password_hash(body.password.get_secret_value(), value['salt']), value['hash']):
                failures[address] = recent + [now]
                raise HTTPException(401, '密码不正确')
            failures.pop(address, None)
        session(response, request)
        return {'ok': True}

    @app.post('/api/v1/auth/logout')
    def logout(request: Request, response: Response, _=Depends(admin)):
        store.execute('DELETE FROM sessions WHERE digest=?', (digest(request.cookies.get('session', '')),))
        response.delete_cookie('session')
        return {'ok': True}

    @app.get('/api/v1/stats')
    def stats(_=Depends(admin)):
        return {**{t: store.query(f'SELECT COUNT(*) n FROM {t}')[0]['n'] for t in ('posts', 'media', 'bloggers', 'subscriptions')},
                'active_tasks': store.query("SELECT COUNT(*) n FROM tasks WHERE status IN ('queued','running','retry')")[0]['n'],
                'confirmation_tasks': store.query("SELECT COUNT(*) n FROM tasks WHERE status='awaiting_confirmation'")[0]['n']}

    @app.post('/api/v1/tasks')
    def submit(body: Intake, who=Depends(identity)):
        parsed = target(body.url)
        if body.mode in ('subscribe', 'once') and not parsed['post_id']:
            task, duplicate = store.subscribe(body.url, 'once' if body.mode == 'once' else body.schedule)
        elif body.mode == 'blogger':
            parsed = target(body.url)
            with store.connect() as db:
                store.add_blogger(parsed['user'], db)
            return {'user': parsed['user'], 'archive_path': '/?user=' + parsed['user']}
        else:
            task, duplicate = store.submit(body.url, source='update' if body.mode == 'update' else who)
        return {'task': task, 'duplicate': duplicate, 'archive_path': archive_path(task)}

    def archive_path(task):
        return '/?post=' + task['post_id'] if task['post_id'] else '/?user=' + task['user']

    @app.get('/api/v1/tasks')
    def tasks(limit: int = 100, offset: int = 0, filter: TaskFilter = 'all',
              q: str = Query(default='', max_length=200), _=Depends(admin)):
        return store.list_tasks(filter, q, max(1, min(limit, 200)), max(0, offset))

    @app.post('/api/v1/tasks/bulk')
    def bulk_tasks(body: TaskBulk, _=Depends(admin)):
        return store.bulk_tasks(body.action, body.filter, body.q)

    @app.get('/api/v1/tasks/{identifier}')
    def task_detail(identifier: str, _=Depends(identity)):
        rows = store.query('SELECT * FROM tasks WHERE id=?', (identifier,))
        if not rows:
            raise HTTPException(404, '任务不存在')
        return {'task': rows[0], 'archive_path': archive_path(rows[0])}

    @app.post('/api/v1/tasks/{identifier}/confirm')
    def confirm_history(identifier: str, body: HistoryConfirm, _=Depends(admin)):
        try:
            task = store.confirm_history(identifier, body.choice)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {'task': task, 'archive_path': archive_path(task)}

    @app.post('/api/v1/tasks/{identifier}/{action}')
    def task_action(identifier: str, action: str, _=Depends(admin)):
        if action == 'cancel':
            if not store.cancel(identifier):
                raise HTTPException(409, '该任务已结束或不存在')
        elif action == 'retry':
            store.retry(identifier)
        elif action == 'delete':
            try:
                removed = store.delete_task(identifier)
            except ValueError:
                raise HTTPException(409, '活动任务请先取消，再删除记录') from None
            if not removed:
                raise HTTPException(409, '任务仍在进行或不存在，请先取消')
        else:
            raise HTTPException(404, '操作不存在')
        return {'ok': True}

    @app.get('/api/v1/bloggers')
    def bloggers(featured: bool = False, q: str = Query(default='', max_length=200), user: str = '', _=Depends(admin)):
        return list_bloggers(store, q=q, user=user, featured=featured)

    @app.patch('/api/v1/bloggers/{user}')
    def edit_blogger(user: str, body: BloggerEdit, _=Depends(admin)):
        fields = body.model_dump(exclude_none=True)
        if not fields:
            raise ValueError('没有要更新的内容')
        if not store.execute('UPDATE bloggers SET '+','.join(k+'=?' for k in fields)+' WHERE user=?', [*fields.values(),user]):
            raise HTTPException(404, '博主不存在')
        return {'ok': True}

    @app.get('/api/v1/avatars/{user}')
    def avatar(user: str, _=Depends(admin)):
        from .avatars import avatar_file
        path = avatar_file(store, user)
        if path:
            signature = path.read_bytes()[:12]
            content_type = ('image/png' if signature.startswith(b'\x89PNG') else 'image/gif' if signature.startswith(b'GIF')
                            else 'image/webp' if signature.startswith(b'RIFF') else 'image/jpeg')
            return FileResponse(path, media_type=content_type, headers={'Cache-Control':'private, max-age=86400'})
        # The initial remains usable when an account has no readable avatar.
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><rect width="80" height="80" rx="40" fill="#e8f5fd"/><text x="40" y="53" text-anchor="middle" font-family="sans-serif" font-size="36" fill="#1d6eaa">{user[0].upper()}</text></svg>'
        return Response(svg, media_type='image/svg+xml', headers={'Cache-Control':'private, max-age=300'})

    @app.get('/api/v1/subscriptions')
    def subscriptions(_=Depends(admin)):
        return {'items': store.query('''SELECT s.*,COALESCE(b.featured,0) featured,
                       COALESCE(NULLIF(b.display_name,''),s.user) display_name
                       FROM subscriptions s LEFT JOIN bloggers b ON b.user=s.user ORDER BY s.user''')}

    @app.patch('/api/v1/subscriptions/{user}')
    def edit_subscription(user: str, body: SubscriptionEdit, _=Depends(admin)):
        if not store.edit_subscription(user, body.schedule, body.enabled):
            raise HTTPException(404, '订阅不存在')
        return {'ok': True}

    @app.delete('/api/v1/subscriptions/{user}')
    def delete_subscription(user: str, _=Depends(admin)):
        store.execute('DELETE FROM subscriptions WHERE user=?', (user,))
        return {'ok': True}

    @app.get('/api/v1/clients')
    def clients(_=Depends(admin)):
        return {'items': store.query('SELECT id,name,created FROM clients ORDER BY created DESC')}

    @app.post('/api/v1/clients')
    def add_client(body: ClientNew, _=Depends(admin)):
        value, identifier = secrets.token_urlsafe(32), secrets.token_hex(12)
        store.execute('INSERT INTO clients VALUES (?,?,?,?)', (identifier, body.name, digest(value), time.time()))
        return {'id': identifier, 'token': value}

    @app.delete('/api/v1/clients/{identifier}')
    def delete_client(identifier: str, _=Depends(admin)):
        store.execute('DELETE FROM clients WHERE id=?', (identifier,))
        return {'ok': True}

    @app.put('/api/v1/connection/password')
    def set_connection_password(body: Password, _=Depends(admin)):
        salt = secrets.token_hex(16)
        store.set_setting('connection_password', {'salt': salt, 'hash': password_hash(body.password.get_secret_value(), salt)})
        with connection_lock:
            connection_failures.clear()
        return {'ok': True}

    @app.get('/api/v1/connection/check')
    def check_connection(who=Depends(identity)):
        if who == 'website':
            raise HTTPException(403, '请在脚本中填写连接密码后验证')
        return {'ok': True, 'method': 'password' if who == 'scriptcat:password' else 'legacy'}

    @app.get('/api/v1/settings')
    def settings(_=Depends(admin)):
        import gallery_dl.version
        return {'cookie_configured': bool(store.setting('x_cookies')),
                'connection_password_configured': bool(store.setting('connection_password')),
                'proxy_configured': bool(store.setting('x_proxy')),
                'legacy_attached': bool(store.legacy),
                'default_schedule': store.setting('default_schedule', 'quarterly'),
                'engine_version': gallery_dl.version.__version__, 'data_dir': str(store.root)}

    @app.put('/api/v1/settings')
    def save_settings(body: SettingsEdit, _=Depends(admin)):
        cookies = None
        proxy = None
        if body.clear_cookies:
            cookies = {}
        elif body.cookies and body.cookies.get_secret_value().strip():
            cookies = parse_cookies(body.cookies.get_secret_value())
        if body.clear_proxy:
            proxy = ''
        elif body.x_proxy and body.x_proxy.get_secret_value().strip():
            proxy = body.x_proxy.get_secret_value().strip()
            try:
                parsed = urlsplit(proxy)
                port = parsed.port
                valid = (parsed.scheme in ('http', 'https') and parsed.hostname
                         and (port is None or 1 <= port <= 65535) and parsed.path in ('', '/')
                         and not parsed.query and not parsed.fragment and not any(c.isspace() for c in proxy))
            except ValueError:
                valid = False
            if not valid:
                raise ValueError('代理地址需要有效的 HTTP / HTTPS 根地址和端口')
        # Validate everything before persisting any settings.
        with store.connect() as db:
            import json
            values = {'default_schedule': body.default_schedule}
            if cookies is not None:
                values['x_cookies'] = cookies
            if proxy is not None:
                values['x_proxy'] = proxy
            for key, value in values.items():
                db.execute('INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))
            if cookies is not None:
                db.execute("DELETE FROM settings WHERE key='x_rate_limit_until'")
                db.execute("""UPDATE tasks SET cooldown_until=0,cooldown_scope='',cooldown_reason='',next_run=MIN(next_run,?)
                              WHERE cooldown_scope='shared'""", (time.time(),))
                db.execute("INSERT INTO settings VALUES ('x_cookie_revision','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
        return {'ok': True}

    @app.get('/api/v1/posts')
    def posts(q: str = '', user: str = '', post: str = '', favorite: bool = False,
              tag: str = '', kind: str = '', limit: int = 30, offset: int = 0,
              order: Literal['random','timeline','oldest'] = 'random', seed: str = '', snapshot: float = 0, _=Depends(admin)):
        return list_posts(store, q, user, post, favorite, tag, kind, limit, offset, order, seed, snapshot)

    @app.patch('/api/v1/posts/{identifier}')
    def edit_post(identifier: str, body: PostEdit, _=Depends(admin)):
        fields = body.model_dump(exclude_none=True)
        if not fields:
            raise ValueError('没有要更新的内容')
        if 'tags' in fields:
            fields['tags'] = ','.join(dict.fromkeys(t.strip() for t in fields['tags'].replace('，', ',').split(',') if t.strip()))
        clause = ','.join(k + '=?' for k in fields)
        if not store.execute('UPDATE posts SET ' + clause + ' WHERE id=?', [*fields.values(), identifier]):
            raise HTTPException(404, '帖子不存在')
        return {'ok': True}

    @app.get('/api/v1/media/{identifier}')
    def media_file(identifier: int, download: bool = False, _=Depends(admin)):
        rows = store.query('SELECT path FROM media WHERE id=?', (identifier,))
        if not rows:
            raise HTTPException(404, '媒体不存在')
        try:
            path = safe_file(store, rows[0]['path'])
        except ValueError:
            raise HTTPException(404, '媒体文件不存在，请检查存储卷')
        return FileResponse(path, filename=path.name if download else None,
                            headers={'Cache-Control': 'private, max-age=3600'})

    register_sharing(app, store, admin, public_origin)

    @app.get('/')
    def index():
        if (STATIC / 'index.html').exists():
            html = (STATIC / 'index.html').read_text(encoding='utf-8')
            return HTMLResponse(html, headers={'Cache-Control': 'no-cache'})
        return {'message': 'X Archive API is running'}

    @app.get('/static/{filename}')
    def static_file(filename: str):
        if filename not in ('app.js', 'style.css', 'viewer.js', 'viewer.css', 'share.js', 'share.css'):
            raise HTTPException(404, '文件不存在')
        return FileResponse(STATIC / filename, headers={'Cache-Control': 'no-cache'})

    @app.get('/integrations/xarchive.user.js')
    def userscript():
        return FileResponse(Path(__file__).resolve().parent / 'integration' / 'xarchive.user.js',
                            media_type='application/javascript')

    return app
