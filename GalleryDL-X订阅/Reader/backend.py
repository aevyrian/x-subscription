#!/usr/bin/env python3
"""X 阅读器后端：时间线 API、头像缓存、订阅管理、帖子详情。
零第三方依赖，仅标准库。"""
import json
import gzip
import hashlib
import hmac
import os
import random
import re
import secrets
import sys
import time
import uuid
import datetime
import threading
import urllib.request
import urllib.parse
import urllib.error
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", "/data"))
CACHE = Path(os.environ.get("CACHE_DIR", "/cache"))
GDL = Path(os.environ.get("GDL_CONFIG", "/gdl-config"))
PORT = int(os.environ.get("PORT", "9090"))
PROXY = os.environ.get("PROXY", "")
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD", "")
LOGIN_READY = bool(LOGIN_PASSWORD)
VERSION = "0.12.0"
MAX_JSON_BODY = 64 * 1024
MAX_AVATAR_BODY = 5 * 1024 * 1024
MAX_COOKIE_BODY = 1024 * 1024
AVATAR_HOSTS = {"pbs.twimg.com", "abs.twimg.com"}

MEDIA_CTYPE = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".mov": "video/quicktime", ".mkv": "video/x-matroska",
}
MEDIA_EXT = set(MEDIA_CTYPE)
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

_cache = {"signature": None, "users": [], "tweets": [], "tweets_by_user": {}, "avatars": {}, "by_tweet": {}}
_scan_lock = threading.Lock()
_scan_parts = {}
_scan_checked_at = 0.0
_random_order_cache = {"key": None, "tweets": None}
_random_order_lock = threading.Lock()
SCAN_INDEX_VERSION = 1
_storage_thread_lock = threading.RLock()
_sessions = {}
_session_lock = threading.Lock()
SESSION_TTL = 12 * 60 * 60

LOGIN_HTML = """<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>X Reader 登录</title>
<style>*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f8fa;color:#17202a;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}.box{width:min(390px,calc(100% - 32px));padding:32px;background:#fff;border:1px solid #e5e9ed;border-radius:18px;box-shadow:0 16px 48px #1f293714}.logo{font-size:34px;font-weight:700}.hint{margin:8px 0 24px;color:#65717d}input,button{width:100%;height:48px;border-radius:12px;font:inherit}input{border:1px solid #d8dee4;padding:0 14px;outline:none}input:focus{border-color:#1d9bf0;box-shadow:0 0 0 3px #1d9bf01f}button{margin-top:12px;border:0;background:#1d9bf0;color:#fff;font-weight:700;cursor:pointer}.msg{min-height:22px;margin-top:12px;color:#d92d20;font-size:14px}</style></head>
<body><main class=\"box\"><div class=\"logo\">𝕏 Reader</div><div class=\"hint\">请输入登录密码后进入本地归档</div><form id=\"f\"><input id=\"p\" type=\"password\" autocomplete=\"current-password\" autofocus placeholder=\"登录密码\"><button>登录</button><div class=\"msg\" id=\"m\"></div></form></main>
<script>f.onsubmit=async e=>{e.preventDefault();m.textContent='';let r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p.value})});if(r.ok)location.replace('/');else m.textContent=r.status===503?'服务器尚未配置登录密码':'密码不正确';};</script></body></html>"""


@contextmanager
def _storage_lock(path):
    """跨线程/进程串行化同一组 JSON 状态的读改写。"""
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _storage_thread_lock:
        fh = lock_path.open("a+")
        try:
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except ImportError:
                pass
            yield
        finally:
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except ImportError:
                pass
            fh.close()


def _atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_json(path, value, indent=None):
    _atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=indent))


def _atomic_write_bytes(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with tmp.open("wb") as fh:
            fh.write(value)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _text(value, limit=10000):
    if value is None:
        return ""
    if not isinstance(value, (str, int, float, bool)):
        return ""
    return str(value)[:limit]


def _count(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _data_root():
    root = DATA / "twitter"
    return root if root.exists() else DATA


def _data_signature(root):
    """只检查一级目录时间戳；内容未变化时避免重复解析全部归档 JSON。"""
    try:
        return tuple((p.name, p.stat().st_mtime_ns) for p in sorted(root.iterdir()) if p.is_dir())
    except OSError:
        return ()


def scan():
    global _scan_checked_at
    # Avatar and parallel startup requests share a short directory-check window.
    if _cache["signature"] is not None and time.monotonic() - _scan_checked_at < 2:
        return _cache
    root = _data_root()
    signature = _data_signature(root)
    if _cache["signature"] == signature:
        _scan_checked_at = time.monotonic()
        return _cache
    with _scan_lock:
        root = _data_root()
        signature = _data_signature(root)
        _scan_checked_at = time.monotonic()
        if _cache["signature"] == signature:
            return _cache
        return _rebuild_scan(root, signature)


def _scan_user(up):
    users, tweets, avatars, by_tweet = [], [], {}, {}
    u = up.name
    jsons, json_paths = {}, {}
    for f in up.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            tid = str(d.get("tweet_id"))
            jsons[tid] = d
            json_paths[tid] = f
        except Exception:
            pass
    media = {}
    for f in up.iterdir():
        if f.suffix.lower() in MEDIA_EXT:
            tid = f.stem.split("_")[0]
            media.setdefault(tid, []).append({
                "file": f.name,
                "ext": f.suffix[1:].lower(),
                "type": "image" if f.suffix.lower() in IMG_EXT else "video",
            })
    userinfo = None
    for tid, d in jsons.items():
        a = d.get("author") or d.get("user") or {}
        if userinfo is None:
            userinfo = {
                "name": _text(a.get("name", u), 200),
                "nick": _text(a.get("nick", u), 200),
                "uid": _text(a.get("id", ""), 100),
                "avatar": _text(a.get("profile_image"), 2048),
                "desc": _text(a.get("description"), 2000),
                "followers": _count(a.get("followers_count")),
            }
            if userinfo["avatar"]:
                avatars[u] = userinfo["avatar"]
        tweets.append({
            "tweet_id": tid,
            "date": _text(d.get("date"), 64),
            "content": _text(d.get("content"), 200000),
            "hashtags": [_text(x, 200) for x in (d.get("hashtags") or [])[:100]] if isinstance(d.get("hashtags") or [], list) else [],
            "like": _count(d.get("favorite_count")),
            "rt": _count(d.get("retweet_count")),
            "reply": _count(d.get("reply_count")),
            "view": _count(d.get("view_count")),
            "user": u,
            "media": media.get(tid, []),
        })
        by_tweet[tid] = {"user": u, "json_path": str(json_paths[tid]),
                         "media": media.get(tid, [])}
    if userinfo:
        users.append({
            **userinfo, "user": u,
            "tweets": len(jsons),
            "media": sum(len(v) for v in media.values()),
        })
    return users, tweets, avatars, by_tweet


def _scan_part_path(user):
    key = hashlib.sha256(user.encode("utf-8")).hexdigest()
    return CACHE / "scan-index-v1" / f"{key}.json.gz"


def _load_scan_part(user, stamp):
    path = _scan_part_path(user)
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            record = json.load(fh)
        if (not isinstance(record, dict)
                or record.get("version") != SCAN_INDEX_VERSION
                or record.get("user") != user
                or record.get("stamp") != stamp):
            return None
        result = record.get("result")
        if not isinstance(result, dict):
            return None
        users = result.get("users")
        tweets = result.get("tweets")
        avatars = result.get("avatars")
        by_tweet = result.get("by_tweet")
        if (not isinstance(users, list) or not isinstance(tweets, list)
                or not isinstance(avatars, dict) or not isinstance(by_tweet, dict)
                or any(not isinstance(item, dict) for item in users)
                or any(not isinstance(item, dict) for item in tweets)):
            return None
        return users, tweets, avatars, by_tweet
    except (OSError, EOFError, ValueError, TypeError):
        return None


def _save_scan_part(user, stamp, result):
    path = _scan_part_path(user)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "version": SCAN_INDEX_VERSION,
            "user": user,
            "stamp": stamp,
            "result": {
                "users": result[0], "tweets": result[1],
                "avatars": result[2], "by_tweet": result[3],
            },
        }
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=1) as fh:
            json.dump(record, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    except OSError:
        # The in-memory index remains usable if the cache volume is unavailable.
        pass
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _rebuild_scan(root, signature):
    global _cache, _scan_parts
    started = time.perf_counter()
    users, tweets, avatars, by_tweet = [], [], {}, {}
    parts = {}
    disk_hits = 0
    rescanned = 0
    for name, stamp in signature:
        key = (str(root), name)
        cached = _scan_parts.get(key)
        if cached is not None and cached[0] == stamp:
            result = cached[1]
        else:
            result = _load_scan_part(name, stamp)
            if result is None:
                result = _scan_user(root / name)
                rescanned += 1
                _save_scan_part(name, stamp, result)
            else:
                disk_hits += 1
        parts[key] = (stamp, result)
        us, ts, av, bt = result
        users.extend(us)
        tweets.extend(ts)
        avatars.update(av)
        by_tweet.update(bt)
    users.sort(key=lambda x: x["user"])
    tweets.sort(key=lambda x: x["date"], reverse=True)
    tweets_by_user = {}
    for tweet in tweets:
        tweets_by_user.setdefault(tweet["user"], []).append(tweet)
    _scan_parts = parts
    # Publish the complete new index atomically to concurrent request handlers.
    _cache = {"signature": signature, "users": users, "tweets": tweets,
              "tweets_by_user": tweets_by_user, "avatars": avatars,
              "by_tweet": by_tweet}
    elapsed = time.perf_counter() - started
    print(f"[xreader] archive index users={len(users)} tweets={len(tweets)} disk_cache={disk_hits} rescanned={rescanned} elapsed={elapsed:.2f}s", flush=True)
    return _cache


def tweet_detail(tid):
    entry = scan()["by_tweet"].get(str(tid))
    if not entry:
        return None
    try:
        d = json.loads(Path(entry["json_path"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    for key in ("date", "content", "lang"):
        d[key] = _text(d.get(key), 200000 if key == "content" else 64)
    for key in ("favorite_count", "retweet_count", "reply_count", "view_count"):
        d[key] = _count(d.get(key))
    a = d.get("author") if isinstance(d.get("author"), dict) else {}
    d["author"] = {
        "name": _text(a.get("name"), 200), "nick": _text(a.get("nick"), 200),
        "id": _text(a.get("id"), 100), "profile_image": _text(a.get("profile_image"), 2048),
        "description": _text(a.get("description"), 2000),
        "followers_count": _count(a.get("followers_count")),
    }
    d["user"] = entry["user"]
    d["media"] = list(entry["media"])
    return d


def sniff(buf):
    if buf[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", ".png"
    if buf[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", ".gif"
    if buf[:3] == b"\xff\xd8\xff":
        return "image/jpeg", ".jpg"
    if buf[:4] == b"RIFF" and buf[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return "image/jpeg", ".jpg"


def svg_avatar(user):
    u = next((x for x in scan()["users"] if x["user"] == user), None)
    nick = (u or {}).get("nick") or user
    ch = (nick[0] if nick else user[0]).upper()
    h = sum(ord(c) for c in user) % 360
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">'
           f'<rect width="200" height="200" fill="hsl({h},52%,42%)"/>'
           f'<text x="100" y="133" font-size="92" fill="#fff" text-anchor="middle" '
           f'font-family="Helvetica,Arial,sans-serif" font-weight="bold">{ch}</text></svg>')
    return "image/svg+xml", svg.encode("utf-8")


def _avatar_url_ok(url):
    try:
        parsed = urllib.parse.urlsplit(url)
    except (TypeError, ValueError):
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in AVATAR_HOSTS


class _AvatarRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _avatar_url_ok(newurl):
            raise urllib.error.HTTPError(newurl, code, "avatar redirect blocked", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_avatar(user):
    (CACHE / "avatars").mkdir(parents=True, exist_ok=True)
    existing = sorted((CACHE / "avatars").glob(user + ".*"))
    if existing and existing[0].stat().st_size > 0:
        p = existing[0]
        buf = p.read_bytes()
        return sniff(buf)[0], buf
    url = scan()["avatars"].get(user)
    if url:
        try:
            if not _avatar_url_ok(url):
                raise ValueError("avatar host is not allowed")
            handlers = [_AvatarRedirectHandler()]
            if PROXY:
                handlers.append(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
            opener = urllib.request.build_opener(*handlers)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=12) as resp:
                declared = int(resp.headers.get("Content-Length") or 0)
                if declared > MAX_AVATAR_BODY:
                    raise ValueError("avatar is too large")
                data = resp.read(MAX_AVATAR_BODY + 1)
                if len(data) > MAX_AVATAR_BODY:
                    raise ValueError("avatar is too large")
            if data:
                ctype, ext = sniff(data)
                _atomic_write_bytes(CACHE / "avatars" / (user + ext), data)
                return ctype, data
        except Exception:
            pass
    return svg_avatar(user)


# ---------- 订阅模型 ----------

SUB_FILE = GDL / "subscriptions.json"


def _load_subs():
    return _subscription_repository().load()



def _save_subs(subs):
    _subscription_repository().save(subs)



FAVORITES_FILE = GDL / ".reader-favorites.json"


def list_favorites():
    try:
        values = json.loads(FAVORITES_FILE.read_text(encoding="utf-8"))
        return [v for v in values if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_]{1,50}", v)] if isinstance(values, list) else []
    except (OSError, ValueError):
        return []


def set_favorite(body):
    user = body.get("user")
    favorite = body.get("favorite")
    if not isinstance(user, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,50}", user) or type(favorite) is not bool:
        raise ValueError("星标参数无效")
    user = user.casefold()
    with _storage_lock(FAVORITES_FILE):
        values = set(v.casefold() for v in list_favorites())
        if favorite:
            values.add(user)
        else:
            values.discard(user)
        values = sorted(values)
        _atomic_write_json(FAVORITES_FILE, values, indent=2)
    return values


def _read_lines(f):
    if not f.is_file():
        return []
    return [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def parse_user(url):
    value = str(url or "").strip()
    if value.startswith("@"):
        value = value[1:]
    if re.fullmatch(r"[A-Za-z0-9_]{1,15}", value):
        return value if value.lower() not in {"home", "explore", "search", "settings", "i"} else None
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in ("http", "https") or parsed.hostname not in {
            "x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"
        } or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            return None
        parts = parsed.path.strip("/").split("/")
        if len(parts) > 1 and parts[1] not in {"media", "status", "with_replies"}:
            return None
        return parse_user(parts[0])
    except ValueError:
        return None



def new_subscription_code(subs):
    values = [int(s["reader_code"]) for s in subs
              if str(s.get("reader_code", "")).isdigit()]
    return str(max([10100] + values) + 1)


def ensure_subscription_codes():
    ensure_subs()
    with _storage_lock(SUB_FILE):
        subs = _load_subs()
        used = set()
        changed = False
        for sub in subs:
            code = sub.get("reader_code")
            if not isinstance(code, str) or not re.fullmatch(r"[1-9][0-9]{4,}", code) or int(code) < 10101 or code in used:
                code = new_subscription_code(subs)
                sub["reader_code"] = code
                changed = True
            used.add(code)
        if changed:
            _atomic_write_json(SUB_FILE, {"subs": subs}, indent=2)
    return len(subs)


def _default_sub(user, url, freq):
    return {
        "id": uuid.uuid4().hex[:12],
        "user": user,
        "url": url,
        "frequency": freq,
        "custom_days": 7,
        "window_start": 9,
        "window_end": 20,
        "first_range": 1500,
        "save_videos": True,
        "save_metadata": True,
        "enabled": True,
    }


def ensure_subs():
    if SUB_FILE.is_file():
        return _load_subs()
    subs = []
    for url in _read_lines(GDL / "targets.txt"):
        user = parse_user(url)
        subs.append(_default_sub(user or url, url, "daily"))
    for url in _read_lines(GDL / "targets-weekly.txt"):
        user = parse_user(url)
        subs.append(_default_sub(user or url, url, "weekly"))
    if subs:
        _save_subs(subs)
    return subs


def list_nav_users():
    """Show subscribed accounts before their first successful archive."""
    users = list(scan()["users"])
    known = {u["user"].casefold() for u in users}
    for sub in ensure_subs():
        user = sub.get("user")
        if not isinstance(user, str) or not user or user.casefold() in known:
            continue
        users.append({"user": user, "name": user, "nick": user,
                      "tweets": 0, "media": 0})
        known.add(user.casefold())
    users.sort(key=lambda u: u["user"].casefold())
    return users


def _read_ts(name):
    f = GDL / name
    if f.is_file():
        try:
            return int(f.read_text().strip() or 0)
        except Exception:
            return None
    return None


def list_subs():
    subs = ensure_subs()
    jobs = _load_jobs()
    latest = {}
    for j in jobs:
        sid = j.get("sub_id")
        if sid not in latest or j.get("ts_submit", 0) > latest[sid].get("ts_submit", 0):
            latest[sid] = j
    profiles = {u.get("user"): u for u in scan()["users"]}
    for s in subs:
        s["last_run"] = _read_ts(f".last_{s['id']}")
        s["next_run"] = _read_ts(f".next_{s['id']}")
        p = profiles.get(s.get("user"), {})
        s["nick"] = p.get("nick") or s.get("user")
        s["uid"] = p.get("uid", "")
        s["avatar"] = "/avatar/" + s["user"] if p else ""
        s["archived_tweets"] = p.get("tweets", 0)
        j = latest.get(s["id"])
        s["job"] = ({k: j.get(k) for k in
                      ("job_id", "status", "trigger", "ts_submit", "started_ts",
                       "finished_ts", "exit_code", "new_media", "new_json",
                       "skipped", "error")} if j else None)
    return subs


def add_sub(d):
    user = parse_user(d.get("url", ""))
    url = d.get("url", "").strip()
    if not user or not url:
        raise ValueError("无法解析博主用户名或 URL")
    subs = ensure_subs()
    if any(s["user"].casefold() == user.casefold() for s in subs):
        raise ValueError(f"博主 @{user} 已在订阅列表中")
    s = _default_sub(user, url, d.get("frequency", "weekly"))
    s["reader_code"] = new_subscription_code(subs)
    s.update({
        "frequency": d.get("frequency", "weekly"),
        "custom_days": int(d.get("custom_days", 7)),
        "window_start": int(d.get("window_start", 9)),
        "window_end": int(d.get("window_end", 20)),
        "first_range": int(d.get("first_range", 1500)),
        "save_videos": bool(d.get("save_videos", True)),
        "save_metadata": bool(d.get("save_metadata", True)),
        "enabled": bool(d.get("enabled", True)),
    })
    if s["frequency"] not in ("daily", "weekly", "monthly", "custom", "once"):
        raise ValueError(f"未知频率 {s['frequency']}")
    if not (0 <= s["window_start"] <= 23 and 1 <= s["window_end"] <= 24
            and s["window_start"] < s["window_end"]):
        raise ValueError("时间窗口必须满足 0<=起<止<=24")
    subs.append(s)
    _save_subs(subs)
    _init_next_run(s)
    job = _queue_first_run(s)
    return s, job


FREQ_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


def _init_next_run(s):
    """新订阅预置下一次周期时刻（首抓走任务队列，不占用周期槽，避免当天重复）。"""
    if s.get("frequency") == "once":
        return
    try:
        days = FREQ_DAYS.get(s.get("frequency"), int(s.get("custom_days", 7)))
        ws, we = int(s.get("window_start", 9)), int(s.get("window_end", 20))
        base = datetime.datetime.now() + datetime.timedelta(days=days)
        d0 = base.replace(hour=0, minute=0, second=0, microsecond=0)
        t = int(d0.timestamp()) + random.randint(ws * 3600, max(ws * 3600, we * 3600 - 1))
        _atomic_write_text(GDL / f".next_{s['id']}", str(t))
    except OSError:
        pass


def _queue_first_run(s):
    """经现有调度队列提交首次抓取（串行，不直起 gallery-dl 进程）。"""
    job = {
        "job_id": uuid.uuid4().hex[:12],
        "sub_id": s["id"],
        "user": s["user"],
        "status": "queued",
        "trigger": "first",
        "ts_submit": int(time.time()),
        "started_ts": None,
        "finished_ts": None,
        "exit_code": None,
        "new_media": None,
        "new_json": None,
        "skipped": None,
        "error": None,
    }
    _append_job(job)
    trig = GDL / ".trigger.json"
    tjobs = []
    if trig.is_file():
        try:
            tjobs = json.loads(trig.read_text(encoding="utf-8"))
        except Exception:
            tjobs = []
    tjobs = [j for j in tjobs if j.get("id") != s["id"]]
    tjobs.append({"id": s["id"], "job": job["job_id"], "user": s["user"],
                  "ts": job["ts_submit"]})
    with _storage_lock(trig):
        _atomic_write_json(trig, tjobs)
    return job


def update_sub(sid, d):
    subs = ensure_subs()
    for s in subs:
        if s["id"] == sid:
            for k in ("frequency", "custom_days", "window_start", "window_end",
                      "first_range", "save_videos", "save_metadata", "enabled", "url"):
                if k in d:
                    v = d[k]
                    try:
                        if k in ("custom_days", "window_start", "window_end", "first_range"):
                            v = int(v)
                        elif k in ("save_videos", "save_metadata", "enabled"):
                            v = bool(v) if not isinstance(v, str) else v.lower() in ("1", "true", "yes", "on")
                        elif k == "url":
                            v = str(v).strip()
                            if not v:
                                raise ValueError("链接不能为空")
                        elif k == "frequency" and v not in ("daily", "weekly", "monthly", "custom", "once"):
                            raise ValueError(f"未知频率 {v}")
                    except ValueError as e:
                        if "invalid literal" in str(e):
                            raise ValueError(f"字段 {k} 必须是数字")
                        raise
                    s[k] = v
            if "url" in d:
                user = parse_user(s["url"])
                if not user:
                    raise ValueError("无法解析博主用户名或 URL")
                if any(x["user"].casefold() == user.casefold() and x["id"] != sid for x in subs):
                    raise ValueError(f"博主 @{user} 已在订阅列表中")
                s["user"] = user
            ws, we = int(s.get("window_start", 9)), int(s.get("window_end", 20))
            if not (0 <= ws <= 23 and 1 <= we <= 24 and ws < we):
                raise ValueError("时间窗口必须满足 0<=起<止<=24")
            _save_subs(subs)
            if any(k in d for k in ("frequency", "custom_days", "window_start", "window_end", "enabled")):
                (GDL / f".next_{sid}").unlink(missing_ok=True)
                _init_next_run(s)
            return s
    raise ValueError("订阅不存在")



def delete_sub(sid):
    subs = ensure_subs()
    nxt = [s for s in subs if s["id"] != sid]
    if len(nxt) == len(subs):
        raise ValueError("订阅不存在")
    _save_subs(nxt)


def trigger_sub(sid, full_scan=False):
    subs = ensure_subs()
    s = next((x for x in subs if x["id"] == sid), None)
    if not s:
        raise ValueError("订阅不存在")
    if not s.get("enabled", True):
        raise ValueError("订阅已暂停，请先恢复")
    for j in _load_jobs():
        if j.get("sub_id") == sid and j.get("status") in ("queued", "running"):
            raise ValueError("该订阅已有更新任务在进行中，请稍候")
    job = {
        "job_id": uuid.uuid4().hex[:12],
        "sub_id": sid,
        "user": s["user"],
        "status": "queued",
        "trigger": "rescan" if full_scan else "manual",
        "full_scan": bool(full_scan),
        "ts_submit": int(time.time()),
        "started_ts": None,
        "finished_ts": None,
        "exit_code": None,
        "new_media": None,
        "new_json": None,
        "skipped": None,
        "error": None,
    }
    _append_job(job)
    trig = GDL / ".trigger.json"
    tjobs = []
    if trig.is_file():
        try:
            tjobs = json.loads(trig.read_text(encoding="utf-8"))
        except Exception:
            tjobs = []
    tjobs = [j for j in tjobs if j.get("id") != sid]
    tjobs.append({"id": sid, "job": job["job_id"], "user": s["user"], "ts": job["ts_submit"]})
    with _storage_lock(trig):
        _atomic_write_json(trig, tjobs)
    return job


def _load_batches():
    if BATCH_FILE.is_file():
        try:
            d = json.loads(BATCH_FILE.read_text(encoding="utf-8"))
            return d.get("batches", []) if isinstance(d, dict) else []
        except Exception:
            return []
    return []


def _save_batches(batches):
    batches = batches[-BATCH_KEEP:]
    with _storage_lock(BATCH_FILE):
        _atomic_write_json(BATCH_FILE, {"batches": batches}, indent=1)


def _get_batch(bid):
    return next((b for b in _load_batches() if b.get("batch_id") == bid), None)


def create_batch(sub_ids=None):
    """创建全部更新批量任务：按订阅列表顺序入队，复用现有串行触发队列。"""
    subs = ensure_subs()
    if sub_ids is not None:
        want = {i for i in sub_ids if isinstance(i, str)}
        subs = [s for s in subs if s["id"] in want]
        if not subs:
            raise ValueError("指定的订阅不存在")
    jobs = _load_jobs()
    busy = {j.get("sub_id") for j in jobs if j.get("status") in ("queued", "running")}
    trig = GDL / ".trigger.json"
    tjobs = []
    if trig.is_file():
        try:
            tjobs = json.loads(trig.read_text(encoding="utf-8"))
        except Exception:
            tjobs = []
    queued_ids = {j.get("id") for j in tjobs}
    batch_id = uuid.uuid4().hex[:12]
    now = int(time.time())
    queued, skipped, new_jobs = [], [], []
    for s in subs:
        if not s.get("enabled", True):
            skipped.append({"user": s["user"], "reason": "已暂停"})
            continue
        if s["id"] in busy or s["id"] in queued_ids:
            skipped.append({"user": s["user"], "reason": "已有任务在队列中"})
            continue
        job = {
            "job_id": uuid.uuid4().hex[:12],
            "sub_id": s["id"],
            "user": s["user"],
            "batch_id": batch_id,
            "status": "queued",
            "trigger": "batch",
            "ts_submit": now,
            "started_ts": None,
            "finished_ts": None,
            "exit_code": None,
            "new_media": None,
            "new_json": None,
            "skipped": None,
            "error": None,
        }
        new_jobs.append(job)
        tjobs.append({"id": s["id"], "job": job["job_id"],
                      "user": s["user"], "ts": now, "batch": batch_id})
        queued.append(s["id"])
    if not queued:
        raise ValueError("没有可更新的订阅")
    _extend_jobs(new_jobs)
    with _storage_lock(trig):
        _atomic_write_json(trig, tjobs)
    batches = _load_batches()
    batches.append({"batch_id": batch_id, "sub_ids": queued,
                    "total": len(queued), "skipped": skipped,
                    "status": "running", "created_ts": now})
    _save_batches(batches)
    return _batch_progress(batch_id)


def _batch_progress(bid):
    b = _get_batch(bid)
    if not b:
        return None
    cancelled = b.get("status") == "cancelled"
    ids = set(b.get("sub_ids", []))
    by_sub = {}
    for j in _load_jobs():
        if j.get("batch_id") == bid and j.get("sub_id") in ids:
            by_sub[j["sub_id"]] = j
    counts = {"queued": 0, "running": 0, "success": 0, "nonew": 0, "failed": 0}
    current, failed, sm, sj = None, [], 0, 0
    for sid in b.get("sub_ids", []):
        j = by_sub.get(sid)
        if j is None or j.get("status") == "queued":
            counts["queued"] += 1
            if current is None and j is not None:
                current = j.get("user")
        elif j.get("status") == "running":
            counts["running"] += 1
            current = j.get("user")
        elif j.get("status") == "failed":
            counts["failed"] += 1
            failed.append({"user": j.get("user"), "job_id": j.get("job_id"),
                           "error": j.get("error")})
        else:
            nm, nj = j.get("new_media") or 0, j.get("new_json") or 0
            sm, sj = sm + nm, sj + nj
            if nm + nj > 0:
                counts["success"] += 1
            else:
                counts["nonew"] += 1
    done = counts["success"] + counts["nonew"] + counts["failed"]
    status = b.get("status")
    if not cancelled and done >= b.get("total", 0) and b.get("total", 0) > 0:
        status = "done"
    return {"batch_id": bid, "status": status, "total": b.get("total", 0),
            "created_ts": b.get("created_ts"), "counts": counts,
            "finished": done, "current": current,
            "sum_media": sm, "sum_json": sj,
            "failed": failed, "skipped": b.get("skipped", [])}


def cancel_batch(bid):
    batches = _load_batches()
    for b in batches:
        if b.get("batch_id") == bid:
            if b.get("status") == "cancelled":
                return _batch_progress(bid)
            b["status"] = "cancelled"
            _save_batches(batches)
            return _batch_progress(bid)
    raise ValueError("批量任务不存在")


def active_batch():
    batches = _load_batches()
    for b in reversed(batches):
        p = _batch_progress(b.get("batch_id"))
        if p and p.get("status") == "running":
            return p
    return None


JOB_FILE = GDL / ".jobs.json"
JOB_KEEP = 200

BATCH_FILE = GDL / ".batches.json"
BATCH_KEEP = 10

_SENSITIVE = re.compile(
    r"(auth_token|ct0|guest_id\w*|personalization_id|passw\w*|api_?key|secret|token)"
    r"\s*[:=]\s*(\"[^\"]*\"|'[^']*'|\S+)", re.IGNORECASE)
_PROXY_CREDS = re.compile(r"(https?://)([^:/@\s]+):([^@\s]+)@")


def _load_jobs_unlocked():
    return _job_repository().load_unlocked()



def _load_jobs():
    return _job_repository().load()



def _save_jobs_unlocked(jobs):
    _job_repository().save_unlocked(jobs)



def _save_jobs(jobs):
    _job_repository().save(jobs)



def _append_job(job):
    _job_repository().append(job)



def _extend_jobs(new_jobs):
    _job_repository().extend(new_jobs)



def get_job(jid):
    if not SAFE_NAME.match(jid or ""):
        return None
    return next((j for j in _load_jobs() if j.get("job_id") == jid), None)


def redact_log(text):
    text = _SENSITIVE.sub(r"\1=***", text or "")
    return _PROXY_CREDS.sub(r"\1***:***@", text)


def job_log_tail(jid, n=150):
    f = GDL / ".joblogs" / f"job_{jid}.log"
    if not f.is_file():
        return None
    try:
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return redact_log("\n".join(lines[-n:]))


def find_tweet_user(tid):
    for t in scan()["tweets"]:
        if str(t.get("tweet_id")) == str(tid):
            return t.get("user")
    root = DATA / "twitter"
    if root.exists():
        for up in root.iterdir():
            if up.is_dir() and list(up.glob(f"*_{tid}.json")):
                return up.name
    return None


def comments_path(user, tid):
    return DATA / "twitter" / user / "comments" / f"{tid}.json"


def read_comments(user, tid):
    f = comments_path(user, tid)
    if f.is_file():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return {"tweet_id": str(tid), "user": user, "status": "none",
            "updated_ts": None, "error": None, "comments": []}


def trigger_comment_job(tid):
    if not re.fullmatch(r"\d+", tid or ""):
        raise ValueError("无效的推文 ID")
    user = find_tweet_user(tid)
    if not user:
        raise ValueError("本地未归档该推文")
    for j in _load_jobs():
        if (j.get("kind", "media") == "comments"
                and str(j.get("tweet_id")) == str(tid)
                and j.get("status") in ("queued", "running")):
            raise ValueError("该帖子的评论更新任务正在进行中")
    job = {
        "job_id": uuid.uuid4().hex[:12],
        "kind": "comments",
        "tweet_id": str(tid),
        "user": user,
        "status": "queued",
        "trigger": "manual",
        "ts_submit": int(time.time()),
        "started_ts": None,
        "finished_ts": None,
        "exit_code": None,
        "new_comments": None,
        "error": None,
    }
    _append_job(job)
    trig = GDL / ".ctrigger.json"
    tjobs = []
    if trig.is_file():
        try:
            tjobs = json.loads(trig.read_text(encoding="utf-8"))
        except Exception:
            tjobs = []
    tjobs = [j for j in tjobs if str(j.get("tid")) != str(tid)]
    tjobs.append({"tid": str(tid), "user": user,
                  "job": job["job_id"], "ts": job["ts_submit"]})
    with _storage_lock(trig):
        _atomic_write_json(trig, tjobs)
    return job


DEFAULT_SCHEDULE = {"window_start": 9, "window_end": 20,
                    "min_runs": 1, "max_runs": 2,
                    "weekly_days_min": 6, "weekly_days_max": 8}


def read_schedule():
    f = GDL / "schedule.json"
    if f.is_file():
        try:
            return {**DEFAULT_SCHEDULE, **json.loads(f.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return dict(DEFAULT_SCHEDULE)


def write_schedule(d):
    v = {k: int(d.get(k, DEFAULT_SCHEDULE[k])) for k in DEFAULT_SCHEDULE}
    target = GDL / "schedule.json"
    with _storage_lock(target):
        _atomic_write_json(target, v, indent=2)


class Handler(BaseHTTPRequestHandler):
    server_version = "XReader"
    sys_version = ""

    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; media-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                         "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        super().end_headers()

    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _session_token(self):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            return cookie["xr_session"].value if "xr_session" in cookie else ""
        except (KeyError, ValueError):
            return ""

    def _auth_ok(self):
        token = self._session_token()
        if not LOGIN_READY or not token:
            return False
        now = time.time()
        with _session_lock:
            expires = _sessions.get(token, 0)
            if expires <= now:
                _sessions.pop(token, None)
                return False
            _sessions[token] = now + SESSION_TTL
        return True

    def _require_admin(self):
        if not LOGIN_READY:
            self._send(503, json.dumps({"error": "login password is not configured"}))
            return False
        if not self._auth_ok():
            self._send(401, json.dumps({"error": "login required"}))
            return False
        return True

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _login(self):
        if not LOGIN_READY:
            self._send(503, json.dumps({"error": "login password is not configured"}))
            return
        try:
            body = self._read_json()
        except (ValueError, OverflowError) as exc:
            self._send(400, json.dumps({"error": str(exc)}))
            return
        supplied = body.get("password")
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, LOGIN_PASSWORD):
            time.sleep(0.35)
            self._send(401, json.dumps({"error": "invalid password"}))
            return
        token = secrets.token_urlsafe(32)
        with _session_lock:
            _sessions[token] = time.time() + SESSION_TTL
        cookie = f"xr_session={token}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Strict"
        self._send(200, json.dumps({"ok": True}), headers={"Set-Cookie": cookie})

    def _logout(self):
        token = self._session_token()
        with _session_lock:
            _sessions.pop(token, None)
        self._send(200, json.dumps({"ok": True}), headers={
            "Set-Cookie": "xr_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
        })

    def _read_json(self):
        raw = self.headers.get("Content-Length", "0")
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise ValueError("invalid content length")
        if n < 0:
            raise ValueError("invalid content length")
        if n > MAX_JSON_BODY:
            raise OverflowError("request body too large")
        try:
            value = json.loads(self.rfile.read(n) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("invalid json")
        if not isinstance(value, dict):
            raise ValueError("json body must be an object")
        return value

    def _read_x_cookies(self):
        raw_length = self.headers.get("Content-Length", "")
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            raise ValueError("invalid content length")
        if length <= 0:
            raise ValueError("cookie file is empty")
        if length > MAX_COOKIE_BODY:
            raise OverflowError("cookie file is too large")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("incomplete cookie file")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("cookie file must be UTF-8 Netscape format")
        if "Netscape HTTP Cookie File" not in "\n".join(text.splitlines()[:10]):
            raise ValueError("expected a Netscape-format cookies.txt file")
        has_auth = False
        x_lines = []
        for line in text.splitlines():
            if line.startswith("#HttpOnly_"):
                cookie_line = line[len("#HttpOnly_"):]
            elif not line or line.startswith("#"):
                x_lines.append(line)
                continue
            else:
                cookie_line = line
            fields = cookie_line.split("\t")
            if len(fields) < 7:
                continue
            domain = fields[0].lstrip(".").lower()
            is_x_domain = (domain == "x.com" or domain.endswith(".x.com") or
                           domain == "twitter.com" or domain.endswith(".twitter.com"))
            if not is_x_domain:
                continue
            x_lines.append(line)
            if fields[5] == "auth_token" and fields[6]:
                has_auth = True
        if not has_auth:
            raise ValueError("cookie file does not contain an X auth_token")
        return ("\n".join(x_lines) + "\n").encode("utf-8")

    def _write_x_cookies(self, raw):
        cookie_dir = GDL / "cookies"
        cookie_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(cookie_dir, 0o700)
        path = cookie_dir / "cookies.txt"
        tmp = cookie_dir / f".cookies.{os.getpid()}.{threading.get_ident()}.tmp"
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
            if os.name == "posix":
                os.chmod(path, 0o600)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def _x_cookie_status(self):
        path = GDL / "cookies" / "cookies.txt"
        try:
            stat = path.stat()
            configured = stat.st_size > 0
            updated = int(stat.st_mtime) if configured else None
        except OSError:
            configured, updated = False, None
        self._send(200, json.dumps({"configured": configured, "updated": updated}))

    def do_HEAD(self):
        path = self.path.split("?", 1)[0]
        if path != "/login" and not self._auth_ok():
            self._redirect("/login")
            return
        if path == "/":
            p = Path(__file__).parent / "index.html"
            try:
                size = p.stat().st_size
            except OSError:
                self._send(404, b"")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
        else:
            self._send(404, b"")

    def do_GET(self):
        from api_v1 import dispatch
        if dispatch(self, sys.modules[__name__]):
            return
        path = self.path.split("?", 1)[0]
        if path == "/integration/xarchive.user.js":
            self._serve_file(Path(__file__).parent.parent / "integration" / "xarchive.user.js", "application/javascript; charset=utf-8")
            return
        query = {}
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    query[k] = v
        if path == "/login":
            if self._auth_ok():
                self._redirect("/")
            else:
                self._send(200, LOGIN_HTML, "text/html; charset=utf-8")
            return
        if not self._auth_ok():
            if path.startswith("/api/"):
                self._send(401, json.dumps({"error": "login required"}))
            else:
                self._redirect("/login")
            return
        if path == "/api/users":
            self._send(200, json.dumps(list_nav_users(), ensure_ascii=False))
        elif path == "/api/tweets":
            cache = scan()
            user = query.get("user")
            data = (cache["tweets_by_user"].get(user, [])
                    if user else cache["tweets"])
            total = len(data)
            if not user:
                order = query.get("order", "time")
                if order == "random":
                    seed = query.get("seed", "")
                    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", seed):
                        self._send(400, json.dumps({"error": "bad random seed"}))
                        return
                    key = (cache["signature"], seed)
                    with _random_order_lock:
                        if _random_order_cache["key"] != key:
                            shuffled = list(data)
                            random.Random(seed).shuffle(shuffled)
                            _random_order_cache["key"] = key
                            _random_order_cache["tweets"] = shuffled
                        data = _random_order_cache["tweets"]
                elif order == "round_robin":
                    groups = {}
                    for tweet in data:
                        groups.setdefault(tweet["user"], []).append(tweet)
                    data = [
                        group[index]
                        for index in range(max((len(group) for group in groups.values()), default=0))
                        for group in groups.values()
                        if index < len(group)
                    ]
                elif order != "time":
                    self._send(400, json.dumps({"error": "bad order"}))
                    return
            try:
                offset = max(int(query.get("offset", "0")), 0)
            except ValueError:
                offset = 0
            try:
                limit = min(max(int(query.get("limit", "20")), 1), 100)
            except ValueError:
                limit = 20
            self._send(200, json.dumps(
                {"total": total, "tweets": data[offset:offset + limit]}, ensure_ascii=False))
        elif path.startswith("/api/tweet/"):
            tid = path[len("/api/tweet/"):]
            if not SAFE_NAME.match(tid):
                self._send(400, json.dumps({"error": "bad id"}))
                return
            d = tweet_detail(tid)
            if d is None:
                self._send(404, json.dumps({"error": "not found"}))
            else:
                c = read_comments(d.get("user"), tid)
                d["comments"] = {
                    "status": c.get("status", "none"),
                    "updated_ts": c.get("updated_ts"),
                    "count": len(c.get("comments", [])),
                    "error": c.get("error"),
                    "items": c.get("comments", [])[:200],
                }
                self._send(200, json.dumps(d, ensure_ascii=False))
        elif path == "/api/admin/state":
            self._send(200, json.dumps({
                "version": VERSION,
                "schedule": read_schedule(),
                "subscriptions": list_subs(),
                "favorites": list_favorites(),
                "active_batch": active_batch(),
            }, ensure_ascii=False))
        elif path == "/api/admin/x-auth":
            self._x_cookie_status()
        elif path == "/api/admin/parse":
            self._send(200, json.dumps({"user": parse_user(query.get("url", ""))}))
        elif path == "/api/admin/jobs":
            jobs = _load_jobs()
            if query.get("sub_id"):
                jobs = [j for j in jobs if j.get("sub_id") == query["sub_id"]]
            jobs = sorted(jobs, key=lambda j: j.get("ts_submit", 0), reverse=True)[:20]
            self._send(200, json.dumps({"jobs": jobs}, ensure_ascii=False))
        elif path.startswith("/api/admin/jobs/"):
            j = get_job(path.rsplit("/", 1)[1])
            if j is None:
                self._send(404, json.dumps({"error": "not found"}))
            else:
                j = dict(j)
                j["log_tail"] = job_log_tail(j["job_id"])
                self._send(200, json.dumps({"job": j}, ensure_ascii=False))
        elif path.startswith("/api/admin/batches/"):
            p = _batch_progress(path.rsplit("/", 1)[1])
            if p is None:
                self._send(404, json.dumps({"error": "not found"}))
            else:
                self._send(200, json.dumps({"batch": p}, ensure_ascii=False))
        elif path == "/":
            self._serve_file(Path(__file__).parent / "index.html", "text/html; charset=utf-8")
        elif path == "/connect":
            self._serve_file(Path(__file__).parent / "connect.html", "text/html; charset=utf-8")
        elif path == "/integration/xarchive.user.js":
            self._serve_file(Path(__file__).parent.parent / "integration" / "xarchive.user.js", "application/javascript; charset=utf-8")
        elif path.startswith("/media/"):
            self._serve_media(path[len("/media/"):])
        elif path.startswith("/avatar/"):
            self._serve_avatar(path[len("/avatar/"):])
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        from api_v1 import dispatch
        if dispatch(self, sys.modules[__name__]):
            return
        path = self.path.split("?", 1)[0]
        if path == "/api/login":
            self._login()
            return
        if not self._require_admin():
            return
        if path == "/api/logout":
            self._logout()
            return
        if path == "/api/admin/x-auth":
            try:
                raw = self._read_x_cookies()
                self._write_x_cookies(raw)
                self._send(200, json.dumps({"ok": True}))
            except OverflowError as e:
                self._send(413, json.dumps({"error": str(e)}))
            except (OSError, ValueError) as e:
                self._send(400, json.dumps({"error": str(e)}))
            return
        try:
            body = self._read_json()
            if path == "/api/admin/subscriptions":
                s, job = add_sub(body)
                self._send(200, json.dumps(
                    {"ok": True, "sub": s,
                     "job_id": job["job_id"], "status": job["status"]},
                    ensure_ascii=False))
            elif path.startswith("/api/admin/subscriptions/") and path.endswith("/run"):
                sid = path.rsplit("/", 2)[1]
                job = trigger_sub(sid)
                self._send(200, json.dumps(
                    {"ok": True, "job_id": job["job_id"], "status": job["status"]}))
            elif path.startswith("/api/admin/subscriptions/") and path.endswith("/rescan"):
                sid = path.rsplit("/", 2)[1]
                job = trigger_sub(sid, full_scan=True)
                self._send(200, json.dumps(
                    {"ok": True, "job_id": job["job_id"], "status": job["status"]}))
            elif path == "/api/admin/schedule":
                write_schedule(body)
                self._send(200, json.dumps({"ok": True}))
            elif path.startswith("/api/admin/comments/") and path.endswith("/run"):
                tid = path.rsplit("/", 2)[1]
                job = trigger_comment_job(tid)
                self._send(200, json.dumps(
                    {"ok": True, "job_id": job["job_id"], "status": job["status"]}))
            elif path == "/api/admin/batches":
                p = create_batch(body.get("sub_ids"))
                self._send(200, json.dumps({"ok": True, "batch": p}, ensure_ascii=False))
            elif path.startswith("/api/admin/batches/") and path.endswith("/cancel"):
                bid = path.rsplit("/", 2)[1]
                p = cancel_batch(bid)
                self._send(200, json.dumps({"ok": True, "batch": p}, ensure_ascii=False))
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except OverflowError as e:
            self._send(413, json.dumps({"error": str(e)}))
        except ValueError as e:
            self._send(400, json.dumps({"error": str(e)}))

    def do_PUT(self):
        path = self.path.split("?", 1)[0]
        if not self._require_admin():
            return
        try:
            body = self._read_json()
            if path == "/api/admin/favorites":
                values = set_favorite(body)
                self._send(200, json.dumps({"ok": True, "favorites": values}))
            elif path.startswith("/api/admin/subscriptions/"):
                sid = path.rsplit("/", 1)[1]
                s = update_sub(sid, body)
                self._send(200, json.dumps({"ok": True, "sub": s}, ensure_ascii=False))
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except OverflowError as e:
            self._send(413, json.dumps({"error": str(e)}))
        except ValueError as e:
            self._send(400, json.dumps({"error": str(e)}))

    def do_DELETE(self):
        from api_v1 import dispatch
        if dispatch(self, sys.modules[__name__]):
            return
        path = self.path.split("?", 1)[0]
        if not self._require_admin():
            return
        try:
            if path.startswith("/api/admin/subscriptions/"):
                sid = path.rsplit("/", 1)[1]
                delete_sub(sid)
                self._send(200, json.dumps({"ok": True}))
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except ValueError as e:
            self._send(400, json.dumps({"error": str(e)}))

    def _serve_avatar(self, user):
        if not SAFE_NAME.match(user):
            self._send(403, json.dumps({"error": "forbidden"}))
            return
        ctype, body = fetch_avatar(user)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def _serve_media(self, rel):
        parts = rel.split("/")
        if len(parts) != 2 or not all(SAFE_NAME.match(p) for p in parts):
            self._send(403, json.dumps({"error": "forbidden"}))
            return
        user, name = parts
        f = DATA / "twitter" / user / name
        if not f.is_file() or f.suffix.lower() not in MEDIA_EXT:
            self._send(404, json.dumps({"error": "not found"}))
            return
        ctype = MEDIA_CTYPE.get(f.suffix.lower(), "application/octet-stream")
        try:
            size = f.stat().st_size
        except OSError:
            self._send(500, json.dumps({"error": "read failed"}))
            return
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        code = 200
        if rng and rng.startswith("bytes="):
            try:
                spec = rng[6:].split("-")
                if spec[0]:
                    start = max(int(spec[0]), 0)
                if len(spec) > 1 and spec[1]:
                    end = min(int(spec[1]), size - 1)
                if start <= end:
                    code = 206
                else:
                    start, end, code = 0, size - 1, 200
            except ValueError:
                start, end, code = 0, size - 1, 200
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        try:
            with open(f, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining:
                    chunk = fh.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (OSError, BrokenPipeError, ConnectionResetError):
            return

    def _serve_file(self, p, ctype):
        if not p.is_file():
            self._send(404, json.dumps({"error": "not found"}))
            return
        body = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _subscription_repository():
    from shared.subscription_repository import LegacySubscriptionRepository
    return LegacySubscriptionRepository(SUB_FILE)


def _job_repository():
    from shared.job_repository import LegacyJobRepository
    return LegacyJobRepository(JOB_FILE, keep=JOB_KEEP)


if __name__ == "__main__":
    print(f"[xreader] DATA={DATA} CACHE={CACHE} GDL={GDL} PORT={PORT} PROXY={'set' if PROXY else 'none'}")
    # Warm the archive index before accepting requests. A validated on-disk
    # index makes restarts cheap and keeps the first page off the scan path.
    scan()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
