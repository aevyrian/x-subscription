"""Legacy persistence boundary for the existing ``.jobs.json`` state."""

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path


_JOB_THREAD_LOCK = threading.RLock()


class LegacyJobRepository:
    """Persist the legacy Job list without changing its file schema or ordering."""

    def __init__(self, path, keep=200):
        self.path = Path(path)
        self.keep = keep

    @contextmanager
    def _lock(self):
        lock_path = Path(str(self.path) + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with _JOB_THREAD_LOCK:
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

    def load_unlocked(self):
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                return data.get("jobs", []) if isinstance(data, dict) else []
            except Exception:
                return []
        return []

    def load(self):
        with self._lock():
            return self.load_unlocked()

    def _atomic_write_json(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(value, ensure_ascii=False, indent=1))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def save_unlocked(self, jobs):
        # History trimming must never discard pending or active downloads.
        finished = [i for i, job in enumerate(jobs) if job.get("status") not in {"queued", "running"}]
        retained = set(finished[-self.keep:])
        self._atomic_write_json({"jobs": [job for i, job in enumerate(jobs)
                                          if i in retained or job.get("status") in {"queued", "running"}]})

    def save(self, jobs):
        with self._lock():
            self.save_unlocked(jobs)

    def append(self, job):
        with self._lock():
            jobs = self.load_unlocked()
            jobs.append(job)
            self.save_unlocked(jobs)

    def extend(self, new_jobs):
        with self._lock():
            jobs = self.load_unlocked()
            jobs.extend(new_jobs)
            self.save_unlocked(jobs)

    def update(self, job_id, **changes):
        with self._lock():
            jobs = self.load_unlocked()
            for job in jobs:
                if job.get("job_id") == job_id:
                    job.update(changes)
                    break
            self.save_unlocked(jobs)
