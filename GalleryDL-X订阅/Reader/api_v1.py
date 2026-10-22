"""Versioned API shared by the website and the ScriptCat companion."""
import json
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from shared import intake


def dispatch(handler, app):
    path = urlsplit(handler.path).path
    admin = path.startswith("/api/admin/clients")
    if not admin and not path.startswith("/api/v1/"):
        return False

    def send(status, value):
        handler._send(status, json.dumps(value, ensure_ascii=False))

    clients = intake.Clients(app.GDL)
    session = handler._auth_ok()
    authorization = handler.headers.get("Authorization", "")
    client = clients.authenticate(authorization[7:]) if authorization.startswith("Bearer ") else None
    if not session and (admin or not client):
        send(401, {"error": "请登录网站或配置有效的脚本连接凭据"})
        return True
    method = handler.command
    try:
        if admin:
            if method == "GET" and path == "/api/admin/clients":
                send(200, {"clients": clients.list()})
            elif method == "POST" and path == "/api/admin/clients":
                send(201, clients.issue(handler._read_json().get("name", "ScriptCat")))
            elif method == "DELETE" and path.count("/") == 4:
                clients.revoke(path.rsplit("/", 1)[1])
                send(200, {"ok": True})
            else:
                send(404, {"error": "接口不存在"})
        elif method == "POST" and path == "/api/v1/tasks":
            body = handler._read_json()
            mode = body.get("mode", "download")
            if mode == "subscribe":
                parsed = intake.target(body.get("url"))
                if parsed["tweet_id"]:
                    raise ValueError("订阅需要博主主页链接")
                frequency = body.get("frequency", "weekly")
                if frequency not in {"daily", "weekly", "monthly"}:
                    raise ValueError("订阅频率需为 daily、weekly 或 monthly")
                existing = next((sub for sub in app.ensure_subs() if sub["user"].lower() == parsed["user"]), None)
                if existing:
                    send(200, {"duplicate": True, "user": parsed["user"], "archive_path": "/?user=" + parsed["user"]})
                else:
                    limit = body.get("limit", 20)
                    if type(limit) is not int or not 1 <= limit <= 1500:
                        raise ValueError("首次下载范围需为 1–1500 条")
                    sub, job = app.add_sub({"url": parsed["url"], "frequency": frequency, "first_range": limit})
                    send(201, {"duplicate": False, "task": intake.public(job), "user": sub["user"]})
            elif mode == "download":
                job, duplicate = intake.submit(app.GDL, body, client or "website")
                send(200 if duplicate else 201, {"task": job, "duplicate": duplicate})
            else:
                raise ValueError("未知操作")
        elif method == "GET" and path == "/api/v1/tasks":
            send(200, {"tasks": [intake.public(job) for job in reversed(app._load_jobs())]})
        elif method == "GET" and path.startswith("/api/v1/tasks/"):
            job = app.get_job(path.rsplit("/", 1)[1])
            send(200 if job else 404, {"task": intake.public(job)} if job else {"error": "任务不存在"})
        elif method == "GET" and path == "/api/v1/library":
            value = parse_qs(urlsplit(handler.path).query).get("url", [""])[0]
            parsed = intake.target(value)
            cache = app.scan()
            posts = cache.get("tweets_by_user", {}).get(parsed["user"], [])
            archived = bool(cache["by_tweet"].get(parsed["tweet_id"])) if parsed["tweet_id"] else bool(posts)
            send(200, {**parsed, "archived": archived, "count": len(posts)})
        else:
            send(404, {"error": "接口不存在"})
    except OverflowError as error:
        send(413, {"error": str(error)})
    except (TypeError, ValueError):
        send(400, {"error": "请求参数无效，请检查 X 链接、范围和操作"})
    except (OSError, sqlite3.Error):
        send(503, {"error": "存储暂不可用"})
    return True
