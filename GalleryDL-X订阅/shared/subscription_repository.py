"""Legacy persistence boundary for the existing ``subscriptions.json`` state."""

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path


_SUBSCRIPTION_THREAD_LOCK = threading.RLock()


class LegacySubscriptionRepository:
    """Persist the legacy subscription list without changing its JSON schema."""

    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _lock(self):
        lock_path = Path(str(self.path) + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with _SUBSCRIPTION_THREAD_LOCK:
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

    def load(self):
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                return data.get("subs", [])
            except Exception:
                return []
        return []

    def _atomic_write_json(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(value, ensure_ascii=False, indent=2))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def save(self, subscriptions):
        with self._lock():
            self._atomic_write_json({"subs": subscriptions})
