#!/usr/bin/env python3
"""gallery-dl 订阅调度器：按 subscriptions.json 逐订阅独立排程 + 消费立即更新触发器。
任务状态持久化到 .jobs.json，执行日志落盘到 logs/job_<id>.log。"""
import json
import os
import random
import subprocess
import time
import datetime

GDL = os.environ.get("GDL_CONFIG", "/config")
FREQ = {"daily": 1, "weekly": 7, "monthly": 30}


def _p(name):
    return os.path.join(GDL, name)


def load_subs():
    f = _p("subscriptions.json")
    if os.path.isfile(f):
        try:
            return json.load(open(f, encoding="utf-8")).get("subs", [])
        except Exception:
            return []
    return []


def _load_jobs():
    f = _p(".jobs.json")
    if os.path.isfile(f):
        try:
            d = json.load(open(f, encoding="utf-8"))
            return d.get("jobs", []) if isinstance(d, dict) else []
        except Exception:
            return []
    return []


def _save_jobs(jobs):
    jobs = jobs[-50:]
    with open(_p(".jobs.json"), "w", encoding="utf-8") as f:
        json.dump({"jobs": jobs}, f, ensure_ascii=False, indent=1)


def _update_job(job_id, **kw):
    jobs = _load_jobs()
    for j in jobs:
        if j.get("job_id") == job_id:
            j.update(kw)
            break
    _save_jobs(jobs)


def _find_job(job_id):
    return next((j for j in _load_jobs() if j.get("job_id") == job_id), None)


def last_ts(s):
    f = f"{GDL}/.last_{s['id']}"
    if os.path.isfile(f):
        try:
            return int(open(f).read().strip() or 0)
        except Exception:
            return None
    return None


def compute_next(s, after=None):
    days = FREQ.get(s.get("frequency"), int(s.get("custom_days", 7)))
    ws = int(s.get("window_start", 9))
    we = int(s.get("window_end", 20))
    base = after if after else time.time()
    dt = datetime.datetime.fromtimestamp(base)
    if after:
        dt = dt + datetime.timedelta(days=days)
    d0 = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    t = int(d0.timestamp()) + random.randint(ws * 3600, max(ws * 3600, we * 3600 - 1))
    return t


def classify_error(out, rc):
    if "AuthRequired" in out or "authenticated cookies needed" in out:
        return "Cookie 失效，需要重新上传 cookies"
    if " 429" in out or "429 " in out or "Rate limit" in out or "rate-limit" in out:
        return "X 请求限流，已退避；稍后自动重试"
    if "Permission denied" in out:
        return "文件权限错误"
    if "NameResolution" in out or "Failed to establish" in out or "ProxyError" in out or "Connection aborted" in out or "Timeout" in out:
        return "网络/代理连接失败"
    if "FileNotFoundError" in out or "No such file or directory: 'gallery-dl'" in out:
        return "gallery-dl 未启动"
    if rc != 0:
        return f"gallery-dl 退出码 {rc}"
    return ""


def is_fatal_error(err):
    """批量执行中遇到此类错误应暂停后续队列，避免重复触发相同认证/环境错误。"""
    if not err:
        return False
    return ("Cookie 失效" in err or "文件权限错误" in err
            or "gallery-dl 未启动" in err)


def run_sub(s, first, job_id=None, trigger="schedule"):
    if job_id is None:
        import uuid
        job_id = uuid.uuid4().hex[:12]
        jobs = _load_jobs()
        jobs.append({
            "job_id": job_id, "sub_id": s["id"], "user": s.get("user"),
            "status": "queued", "trigger": trigger,
            "ts_submit": int(time.time()), "started_ts": None,
            "finished_ts": None, "exit_code": None,
            "new_media": None, "new_json": None, "skipped": None, "error": None,
        })
        _save_jobs(jobs)
    pp = [{"name": "metadata", "filename": "{author[name]}_{tweet_id}.json"}] \
        if s.get("save_metadata", True) else []
    over = {"extractor": {"twitter": {
        "videos": bool(s.get("save_videos", True)),
        "postprocessors": pp,
    }}}
    with open("/tmp/gdl_over.json", "w", encoding="utf-8") as f:
        json.dump(over, f)
    args = ["gallery-dl", "-c", f"{GDL}/config.json", "-c", "/tmp/gdl_over.json"]
    r = int(s.get("first_range", 0))
    if first and r > 0:
        args += ["--range", f"1-{r}"]
    args.append(s["url"])
    print(f"[sched] {time.strftime('%F %T')} run @{s.get('user')} "
          f"{'首次' if first else '增量'} job={job_id}", flush=True)
    _update_job(job_id, status="running", started_ts=int(time.time()))
    log_dir = os.path.join(GDL, ".joblogs")
    os.makedirs(log_dir, exist_ok=True)
    out, rc = "", -1
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=3600)
        out = (p.stdout or "") + (p.stderr or "")
        rc = p.returncode
    except FileNotFoundError:
        out = "FileNotFoundError: gallery-dl"
        rc = 127
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "") + "\n[TIMEOUT] 任务超时(3600s)"
        rc = 124
    except Exception as e:
        out = f"ERROR: {e}"
        rc = 1
    try:
        with open(os.path.join(log_dir, f"job_{job_id}.log"), "w", encoding="utf-8") as f:
            f.write(f"$ {' '.join(args)}\n\n{out}\n[exit={rc}]\n")
    except OSError as e:
        print(f"[sched] log write failed: {e}", flush=True)
    new_media = new_json = skipped = 0
    for ln in out.splitlines():
        t = ln.strip()
        if t.startswith("# /downloads/") or t.startswith("#/downloads/"):
            skipped += 1
        elif t.startswith("/downloads/"):
            if t.endswith(".json"):
                new_json += 1
            else:
                new_media += 1
    err = classify_error(out, rc)
    status = "done" if not err else "failed"
    _update_job(job_id, status=status, finished_ts=int(time.time()),
                exit_code=rc, new_media=new_media, new_json=new_json,
                skipped=skipped, error=err or None)
    with open(f"{GDL}/.last_{s['id']}", "w") as f:
        f.write(str(int(time.time())))
    print(f"[sched] @{s.get('user')} job={job_id} {status} "
          f"new_media={new_media} new_json={new_json} skipped={skipped} {err}", flush=True)
    return status, err


def load_trigger():
    f = f"{GDL}/.trigger.json"
    if os.path.isfile(f):
        try:
            return json.load(open(f, encoding="utf-8"))
        except Exception:
            return []
    return []


def clear_trigger():
    f = f"{GDL}/.trigger.json"
    if os.path.isfile(f):
        try:
            os.remove(f)
        except Exception:
            pass


def _write_trigger(lst):
    """写回剩余队列（逐任务消费后调用，崩溃重启只剩未执行项）。"""
    try:
        with open(f"{GDL}/.trigger.json", "w", encoding="utf-8") as f:
            json.dump(lst, f, ensure_ascii=False)
    except OSError as e:
        print(f"[sched] trigger write failed: {e}", flush=True)


def _load_batches():
    f = f"{GDL}/.batches.json"
    if os.path.isfile(f):
        try:
            d = json.load(open(f, encoding="utf-8"))
            return d.get("batches", []) if isinstance(d, dict) else []
        except Exception:
            return []
    return []


def _batch_cancelled(batch_id):
    if not batch_id:
        return False
    for b in _load_batches():
        if b.get("batch_id") == batch_id:
            return b.get("status") == "cancelled"
    return False


def consume_triggers(subs):
    """串行消费下载队列：同一时刻最多一个 gallery-dl 进程；致命错误或批量停止则跳过剩余项。"""
    trig = load_trigger()
    if not trig:
        return
    mark_stale_trigger(trig, subs)
    by_id = {s["id"]: s for s in subs}
    halted, halt_reason = False, ""
    remaining = list(trig)
    for job in trig:
        jid = job.get("job")
        cur = _find_job(jid) if jid else None
        if not cur or cur.get("status") != "queued":
            remaining = [r for r in remaining if r.get("job") != jid]
            _write_trigger(remaining)
            continue
        if halted or _batch_cancelled(job.get("batch")):
            _update_job(jid, status="failed", finished_ts=int(time.time()),
                        error=halt_reason if halted else "批量任务已停止")
            remaining = [r for r in remaining if r.get("job") != jid]
            _write_trigger(remaining)
            continue
        s = by_id.get(job.get("id"))
        if s and s.get("enabled", True):
            _st, err = run_sub(s, first=(last_ts(s) is None),
                               job_id=jid, trigger=cur.get("trigger") or "manual")
            if is_fatal_error(err):
                halted, halt_reason = True, err
        remaining = [r for r in remaining if r.get("job") != jid]
        _write_trigger(remaining)
    clear_trigger()


def mark_stale_trigger(jobs_list, subs):
    by_id = {s["id"]: s for s in subs}
    for job in jobs_list:
        sid = job.get("id")
        jid = job.get("job")
        s = by_id.get(sid)
        if not s or not s.get("enabled", True):
            if jid:
                cur = _find_job(jid)
                if cur and cur.get("status") == "queued":
                    _update_job(jid, status="failed", finished_ts=int(time.time()),
                                error="订阅不存在或已暂停")


def load_ctriggers():
    f = f"{GDL}/.ctrigger.json"
    if os.path.isfile(f):
        try:
            return json.load(open(f, encoding="utf-8"))
        except Exception:
            return []
    return []


def clear_ctriggers():
    f = f"{GDL}/.ctrigger.json"
    if os.path.isfile(f):
        try:
            os.remove(f)
        except Exception:
            pass


def comment_record(m, focal_tid):
    a = m.get("author") or {}
    tid = str(m.get("tweet_id") or "")
    if not tid or tid == str(focal_tid):
        return None
    return {
        "id": tid,
        "parent_id": str(m.get("reply_id") or focal_tid),
        "author_id": str(a.get("id", "")),
        "author_name": a.get("name", ""),
        "nick": a.get("nick", ""),
        "content": m.get("content", ""),
        "date": m.get("date", ""),
        "lang": m.get("lang", ""),
        "fav": m.get("favorite_count", 0),
        "url": f"https://x.com/{a.get('name', '')}/status/{tid}" if a.get("name") else "",
    }


def run_comments(tid, user, job_id):
    print(f"[sched] {time.strftime('%F %T')} comments @{user}/{tid} job={job_id}", flush=True)
    _update_job(job_id, status="running", started_ts=int(time.time()))
    stdout, stderr, rc = "", "", -1
    try:
        p = subprocess.run(
            ["gallery-dl", "-c", f"{GDL}/config.json", "-s", "-j",
             "-o", "extractor.twitter.conversations=true",
             "-o", "extractor.twitter.text-tweets=true",
             "--range", "1-300",
             f"https://x.com/{user}/status/{tid}"],
            capture_output=True, text=True, timeout=1200)
        stdout, stderr = (p.stdout or ""), (p.stderr or "")
        rc = p.returncode
    except FileNotFoundError:
        stderr = "FileNotFoundError: gallery-dl"
        rc = 127
    except subprocess.TimeoutExpired as e:
        stdout = (e.stdout or "") if isinstance(e.stdout, str) else ""
        stderr = (e.stderr or "") if isinstance(e.stderr, str) else ""
        stderr += "\n[TIMEOUT]"
        rc = 124
    except Exception as e:
        stderr = f"ERROR: {e}"
        rc = 1
    out = stdout + stderr
    err = classify_error(out, rc)
    log_dir = os.path.join(GDL, ".joblogs")
    os.makedirs(log_dir, exist_ok=True)
    try:
        with open(os.path.join(log_dir, f"job_{job_id}.log"), "w", encoding="utf-8") as f:
            f.write(f"$ gallery-dl comments @{user}/{tid}\n\n{out}\n[exit={rc}]\n")
    except OSError as e:
        print(f"[sched] comment log write failed: {e}", flush=True)
    merged = {}
    cdir = f"/downloads/twitter/{user}/comments"
    cfile = f"{cdir}/{tid}.json"
    try:
        if os.path.isfile(cfile):
            old = json.load(open(cfile, encoding="utf-8"))
            for c in old.get("comments", []):
                if c.get("id") and c.get("content"):
                    merged[str(c["id"])] = c
    except Exception:
        pass
    if not err:
        try:
            s, e = stdout.index("["), stdout.rindex("]") + 1
            data = json.loads(stdout[s:e])
        except Exception:
            data = []
            err = "评论数据解析失败"
        for e in data:
            m = e[1] if isinstance(e, list) and len(e) > 1 and isinstance(e[1], dict) else None
            if not m:
                continue
            rec = comment_record(m, tid)
            if rec and rec["content"]:
                merged[rec["id"]] = rec
    comments = sorted(merged.values(), key=lambda c: c["date"])
    status = "failed" if err else ("ok" if comments else "empty")
    try:
        os.makedirs(cdir, exist_ok=True)
        with open(cfile, "w", encoding="utf-8") as f:
            json.dump({"tweet_id": str(tid), "user": user, "status": status,
                       "updated_ts": int(time.time()), "error": err or None,
                       "comments": comments}, f, ensure_ascii=False, indent=1)
    except OSError as e:
        status, err = "failed", f"评论文件写入失败: {e}"
    _update_job(job_id, status=("failed" if status == "failed" else "done"),
                finished_ts=int(time.time()), exit_code=rc,
                new_comments=len(comments),
                error=err or None)
    print(f"[sched] comments @{user}/{tid} job={job_id} {status} n={len(comments)} {err}",
          flush=True)


def main():
    print("[sched] subscriptions scheduler started", flush=True)
    while True:
        try:
            subs = load_subs()
            consume_triggers(subs)
            for cj in load_ctriggers():
                jid, tid, user = cj.get("job"), cj.get("tid"), cj.get("user")
                cur = _find_job(jid) if jid else None
                if cur and cur.get("status") == "queued" and tid and user:
                    run_comments(tid, user, jid)
                elif jid:
                    _update_job(jid, status="failed", finished_ts=int(time.time()),
                                error="评论任务参数无效")
            clear_ctriggers()
            for s in subs:
                if not s.get("enabled", True):
                    continue
                nf = f"{GDL}/.next_{s['id']}"
                if not os.path.isfile(nf):
                    with open(nf, "w") as f:
                        f.write(str(compute_next(s)))
                try:
                    nxt = int(open(nf).read().strip() or 0)
                except Exception:
                    nxt = 0
                if time.time() >= nxt:
                    run_sub(s, first=(last_ts(s) is None))
                    with open(nf, "w") as f:
                        f.write(str(compute_next(s, after=time.time())))
        except Exception as e:
            print(f"[sched] loop error: {e}", flush=True)
        time.sleep(60)


if __name__ == "__main__":
    main()
