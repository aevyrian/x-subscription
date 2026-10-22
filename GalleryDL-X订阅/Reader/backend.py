#!/usr/bin/env python3
"""X 阅读器后端：时间线 API、头像缓存、订阅管理、帖子详情。
零第三方依赖，仅标准库。"""
import json
import os
import random
import re
import time
import uuid
import datetime
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", "/data"))
CACHE = Path(os.environ.get("CACHE_DIR", "/cache"))
GDL = Path(os.environ.get("GDL_CONFIG", "/gdl-config"))
PORT = int(os.environ.get("PORT", "9090"))
PROXY = os.environ.get("PROXY", "")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "xarchive")
VERSION = "0.5.1"

MEDIA_CTYPE = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".mov": "video/quicktime", ".mkv": "video/x-matroska",
}
MEDIA_EXT = set(MEDIA_CTYPE)
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

_cache = {"t": 0, "users": [], "tweets": [], "avatars": {}}
TTL = 30


def scan():
    now = time.time()
    if _cache["t"] and now - _cache["t"] < TTL:
        return _cache
    root = DATA / "twitter"
    if not root.exists():
        root = DATA
    users, tweets, avatars = [], [], {}
    for up in sorted(root.iterdir()):
        if not up.is_dir():
            continue
        u = up.name
        jsons = {}
        for f in up.glob("*.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                jsons[str(d.get("tweet_id"))] = d
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
                    "name": a.get("name", u),
                    "nick": a.get("nick", u),
                    "uid": str(a.get("id", "")),
                    "avatar": a.get("profile_image") or "",
                    "desc": a.get("description", ""),
                    "followers": a.get("followers_count", 0),
                }
                if userinfo["avatar"]:
                    avatars[u] = userinfo["avatar"]
            tweets.append({
                "tweet_id": tid,
                "date": d.get("date", ""),
                "content": d.get("content", ""),
                "hashtags": d.get("hashtags") or [],
                "like": d.get("favorite_count", 0),
                "rt": d.get("retweet_count", 0),
                "reply": d.get("reply_count", 0),
                "view": d.get("view_count", 0),
                "user": u,
                "media": media.get(tid, []),
            })
        if userinfo:
            users.append({
                **userinfo, "user": u,
                "tweets": len(jsons),
                "media": sum(len(v) for v in media.values()),
            })
    users.sort(key=lambda x: x["user"])
    tweets.sort(key=lambda x: x["date"], reverse=True)
    _cache.update(t=now, users=users, tweets=tweets, avatars=avatars)
    return _cache


def tweet_detail(tid):
    root = DATA / "twitter"
    if not root.exists():
        return None
    for up in root.iterdir():
        if not up.is_dir():
            continue
        for f in up.glob(f"*_{tid}.json"):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            media = []
            for mf in up.iterdir():
                if mf.suffix.lower() in MEDIA_EXT and mf.stem.split("_")[0] == str(tid):
                    media.append({
                        "file": mf.name,
                        "ext": mf.suffix[1:].lower(),
                        "type": "image" if mf.suffix.lower() in IMG_EXT else "video",
                    })
            media.sort(key=lambda x: x["file"])
            d["user"] = up.name
            d["media"] = media
            return d
    return None


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
            handlers = []
            if PROXY:
                handlers.append(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
            opener = urllib.request.build_opener(*handlers)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = opener.open(req, timeout=12).read()
            if data:
                ctype, ext = sniff(data)
                (CACHE / "avatars" / (user + ext)).write_bytes(data)
                return ctype, data
        except Exception:
            pass
    return svg_avatar(user)


# ---------- 订阅模型 ----------

SUB_FILE = GDL / "subscriptions.json"


def _load_subs():
    if SUB_FILE.is_file():
        try:
            return json.loads(SUB_FILE.read_text(encoding="utf-8")).get("subs", [])
        except Exception:
            return []
    return []


def _save_subs(subs):
    SUB_FILE.write_text(json.dumps({"subs": subs}, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_lines(f):
    if not f.is_file():
        return []
    return [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def parse_user(url):
    m = re.search(r"x\.com/([A-Za-z0-9_]+)", url or "")
    return m.group(1) if m else None


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
    user = d.get("user") or parse_user(d.get("url", ""))
    url = d.get("url", "").strip()
    if not user or not url:
        raise ValueError("无法解析博主用户名或 URL")
    subs = ensure_subs()
    if any(s["user"] == user for s in subs):
        raise ValueError(f"博主 @{user} 已在订阅列表中")
    s = _default_sub(user, url, d.get("frequency", "weekly"))
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
    if s["frequency"] not in ("daily", "weekly", "monthly", "custom"):
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
    try:
        days = FREQ_DAYS.get(s.get("frequency"), int(s.get("custom_days", 7)))
        ws, we = int(s.get("window_start", 9)), int(s.get("window_end", 20))
        base = datetime.datetime.now() + datetime.timedelta(days=days)
        d0 = base.replace(hour=0, minute=0, second=0, microsecond=0)
        t = int(d0.timestamp()) + random.randint(ws * 3600, max(ws * 3600, we * 3600 - 1))
        (GDL / f".next_{s['id']}").write_text(str(t), encoding="utf-8")
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
    jobs = _load_jobs()
    jobs.append(job)
    _save_jobs(jobs)
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
    trig.write_text(json.dumps(tjobs, ensure_ascii=False), encoding="utf-8")
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
                        elif k == "frequency" and v not in ("daily", "weekly", "monthly", "custom"):
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
                if any(x["user"] == user and x["id"] != sid for x in subs):
                    raise ValueError(f"博主 @{user} 已在订阅列表中")
                s["user"] = user
            ws, we = int(s.get("window_start", 9)), int(s.get("window_end", 20))
            if not (0 <= ws <= 23 and 1 <= we <= 24 and ws < we):
                raise ValueError("时间窗口必须满足 0<=起<止<=24")
            _save_subs(subs)
            return s
    raise ValueError("订阅不存在")


def delete_sub(sid):
    subs = ensure_subs()
    nxt = [s for s in subs if s["id"] != sid]
    if len(nxt) == len(subs):
        raise ValueError("订阅不存在")
    _save_subs(nxt)


def trigger_sub(sid):
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
        "trigger": "manual",
        "ts_submit": int(time.time()),
        "started_ts": None,
        "finished_ts": None,
        "exit_code": None,
        "new_media": None,
        "new_json": None,
        "skipped": None,
        "error": None,
    }
    jobs = _load_jobs()
    jobs.append(job)
    _save_jobs(jobs)
    trig = GDL / ".trigger.json"
    tjobs = []
    if trig.is_file():
        try:
            tjobs = json.loads(trig.read_text(encoding="utf-8"))
        except Exception:
            tjobs = []
    tjobs = [j for j in tjobs if j.get("id") != sid]
    tjobs.append({"id": sid, "job": job["job_id"], "user": s["user"], "ts": job["ts_submit"]})
    trig.write_text(json.dumps(tjobs, ensure_ascii=False), encoding="utf-8")
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
    BATCH_FILE.write_text(json.dumps({"batches": batches}, ensure_ascii=False, indent=1),
                          encoding="utf-8")


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
    queued, skipped = [], []
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
        jobs.append(job)
        tjobs.append({"id": s["id"], "job": job["job_id"],
                      "user": s["user"], "ts": now, "batch": batch_id})
        queued.append(s["id"])
    if not queued:
        raise ValueError("没有可更新的订阅")
    _save_jobs(jobs)
    trig.write_text(json.dumps(tjobs, ensure_ascii=False), encoding="utf-8")
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


def _load_jobs():
    if JOB_FILE.is_file():
        try:
            d = json.loads(JOB_FILE.read_text(encoding="utf-8"))
            return d.get("jobs", []) if isinstance(d, dict) else []
        except Exception:
            return []
    return []


def _save_jobs(jobs):
    jobs = jobs[-JOB_KEEP:]
    JOB_FILE.write_text(json.dumps({"jobs": jobs}, ensure_ascii=False, indent=1), encoding="utf-8")


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
    jobs = _load_jobs()
    jobs.append(job)
    _save_jobs(jobs)
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
    trig.write_text(json.dumps(tjobs, ensure_ascii=False), encoding="utf-8")
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
    (GDL / "schedule.json").write_text(json.dumps(v, ensure_ascii=False, indent=2), encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _auth_ok(self):
        return self.headers.get("X-Admin-Token") == ADMIN_TOKEN

    def _read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return None

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        query = {}
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    query[k] = v
        if path == "/api/users":
            self._send(200, json.dumps(scan()["users"], ensure_ascii=False))
        elif path == "/api/tweets":
            data = scan()["tweets"]
            if query.get("user"):
                data = [t for t in data if t["user"] == query["user"]]
            total = len(data)
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
                "active_batch": active_batch(),
            }, ensure_ascii=False))
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
        elif path.startswith("/media/"):
            self._serve_media(path[len("/media/"):])
        elif path.startswith("/avatar/"):
            self._serve_avatar(path[len("/avatar/"):])
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self._auth_ok():
            self._send(403, json.dumps({"error": "unauthorized"}))
            return
        body = self._read_json() or {}
        try:
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
        except ValueError as e:
            self._send(400, json.dumps({"error": str(e)}))

    def do_PUT(self):
        path = self.path.split("?", 1)[0]
        if not self._auth_ok():
            self._send(403, json.dumps({"error": "unauthorized"}))
            return
        body = self._read_json() or {}
        try:
            if path.startswith("/api/admin/subscriptions/"):
                sid = path.rsplit("/", 1)[1]
                s = update_sub(sid, body)
                self._send(200, json.dumps({"ok": True, "sub": s}, ensure_ascii=False))
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except ValueError as e:
            self._send(400, json.dumps({"error": str(e)}))

    def do_DELETE(self):
        path = self.path.split("?", 1)[0]
        if not self._auth_ok():
            self._send(403, json.dumps({"error": "unauthorized"}))
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
        try:
            with open(f, "rb") as fh:
                fh.seek(start)
                body = fh.read(end - start + 1)
        except OSError:
            self._send(500, json.dumps({"error": "read failed"}))
            return
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

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


if __name__ == "__main__":
    print(f"[xreader] DATA={DATA} CACHE={CACHE} GDL={GDL} PORT={PORT} PROXY={'set' if PROXY else 'none'}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
