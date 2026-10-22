"""Shared task intake for the website and ScriptCat; gallery-dl stays the engine."""
import hashlib
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from contextlib import contextmanager

from shared.job_repository import LegacyJobRepository

HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
RESERVED = {"home", "explore", "search", "settings", "i", "notifications", "messages", "compose", "intent"}


def target(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("请输入 X 博主或帖子链接")
    value = value.strip()
    if re.fullmatch(r"@?[A-Za-z0-9_]{1,15}", value):
        value = "https://x.com/" + value.lstrip("@")
    try:
        url = urlsplit(value)
        parts = url.path.strip("/").split("/")
        if (url.scheme not in {"http", "https"} or url.hostname not in HOSTS
                or url.username or url.password or url.port not in {None, 80, 443}
                or not re.fullmatch(r"[A-Za-z0-9_]{1,15}", parts[0])
                or parts[0].lower() in RESERVED):
            raise ValueError()
        user = parts[0].lower()
        if len(parts) == 1 or (len(parts) == 2 and parts[1] in {"media", "with_replies"}):
            return {"user": user, "tweet_id": None, "url": f"https://x.com/{user}/media"}
        if len(parts) == 3 and parts[1] == "status" and re.fullmatch(r"[0-9]{1,25}", parts[2]):
            return {"user": user, "tweet_id": parts[2], "url": f"https://x.com/{user}/status/{parts[2]}"}
    except (ValueError, IndexError):
        pass
    raise ValueError("仅支持 X / Twitter 的博主主页和单帖链接")


class Clients:
    """Store only credential hashes; no website password or X Cookie is shared."""
    def __init__(self, root):
        self.path = Path(root) / "clients.sqlite"

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE IF NOT EXISTS clients (id TEXT PRIMARY KEY, name TEXT NOT NULL, digest TEXT UNIQUE NOT NULL, created INTEGER NOT NULL)")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def issue(self, name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise ValueError("连接名称需要 1–60 个字符")
        token, identifier = secrets.token_urlsafe(32), uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO clients VALUES (?, ?, ?, ?)",
                       (identifier, name.strip(), hashlib.sha256(token.encode()).hexdigest(), int(time.time())))
        return {"id": identifier, "name": name.strip(), "token": token}

    def authenticate(self, token):
        if not token or len(token) > 256:
            return None
        with self.connect() as db:
            row = db.execute("SELECT id FROM clients WHERE digest = ?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return row["id"] if row else None

    def list(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id, name, created FROM clients ORDER BY created DESC")]

    def revoke(self, identifier):
        with self.connect() as db:
            db.execute("DELETE FROM clients WHERE id = ?", (identifier,))


def submit(root, body, client_id="website"):
    parsed = target(body.get("url"))
    limit = body.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= 1500:
        raise ValueError("首次下载范围需为 1–1500 条")
    repo = LegacyJobRepository(Path(root) / ".jobs.json")
    with repo._lock():
        jobs = repo.load_unlocked()
        existing = next((job for job in jobs if job.get("target_url") == parsed["url"]
                         and job.get("status") in {"queued", "running"}), None)
        if existing:
            return public(existing), True
        # Keep queued/running work even when the completed-history cap is reached.
        job = {"job_id": uuid.uuid4().hex[:12], "kind": "download", "user": parsed["user"],
               "tweet_id": parsed["tweet_id"], "target_url": parsed["url"], "first_range": limit,
               "client_id": client_id, "trigger": "website" if client_id == "website" else "scriptcat",
               "status": "queued", "ts_submit": int(time.time()), "started_ts": None,
               "finished_ts": None, "new_media": None, "new_json": None, "error": None}
        jobs.append(job)
        repo.save_unlocked(jobs)
    return public(job), False


def public(job):
    fields = ("job_id", "kind", "user", "tweet_id", "target_url", "trigger", "status", "ts_submit",
              "started_ts", "finished_ts", "new_media", "new_json", "skipped", "error")
    result = {key: job.get(key) for key in fields}
    # Log-derived messages are not returned through the integration API.
    if result["error"]:
        result["error"] = "下载失败，请在网站任务中心查看原因"
    result["archive_path"] = "/#tweet=" + str(job["tweet_id"]) if job.get("tweet_id") else "/?user=" + job.get("user", "")
    return result
