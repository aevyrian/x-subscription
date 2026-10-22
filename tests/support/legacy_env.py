"""Synthetic, temporary storage for legacy characterization tests."""
import json
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GalleryDL-X订阅"))
sys.path.insert(0, str(ROOT / "GalleryDL-X订阅" / "Reader"))
import backend  # noqa: E402
import scheduler  # noqa: E402


class LegacyEnvironment:
    """Redirect mutable module globals into disposable directories.

    The assert guards are intentional: tests must never fall back to deployment
    defaults such as /config, /data, or a real user archive.
    """

    def __enter__(self):
        self.stack = ExitStack()
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.gdl = self.root / "gdl"
        self.data = self.root / "data"
        self.cache = self.root / "cache"
        for path in (self.gdl, self.data, self.cache):
            path.mkdir()
            assert path.is_relative_to(self.root)
        for obj, attr, value in (
            (backend, "GDL", self.gdl),
            (backend, "DATA", self.data),
            (backend, "CACHE", self.cache),
            (backend, "SUB_FILE", self.gdl / "subscriptions.json"),
            (backend, "JOB_FILE", self.gdl / ".jobs.json"),
            (backend, "BATCH_FILE", self.gdl / ".batches.json"),
            (backend, "LOGIN_PASSWORD", "synthetic-test-password"),
            (backend, "LOGIN_READY", True),
            (backend, "PROXY", ""),
            (scheduler, "GDL", str(self.gdl)),
        ):
            self.stack.enter_context(mock.patch.object(obj, attr, value))
        self.stack.enter_context(mock.patch.object(scheduler.subprocess, "run", side_effect=AssertionError("real subprocess blocked")))
        backend._cache.update(signature=None, users=[], tweets=[], avatars={}, by_tweet={})
        backend._sessions.clear()
        return self

    def __exit__(self, *args):
        backend._cache.update(signature=None, users=[], tweets=[], avatars={}, by_tweet={})
        backend._sessions.clear()
        return self.stack.__exit__(*args)

    def write_subs(self, subs):
        (self.gdl / "subscriptions.json").write_text(json.dumps({"subs": subs}), encoding="utf-8")

    def write_jobs(self, jobs):
        (self.gdl / ".jobs.json").write_text(json.dumps({"jobs": jobs}), encoding="utf-8")

    def write_triggers(self, triggers):
        (self.gdl / ".trigger.json").write_text(json.dumps(triggers), encoding="utf-8")

    def archived_tweet(self, user="exampleuser", tid="2001"):
        folder = self.data / "twitter" / user
        folder.mkdir(parents=True, exist_ok=True)
        tweet = {"tweet_id": tid, "date": "2026-01-01 10:00:00", "content": "synthetic post",
                 "author": {"name": user, "nick": "Example", "id": "42"},
                 "favorite_count": 2, "reply_count": 1}
        (folder / f"{user}_{tid}.json").write_text(json.dumps(tweet), encoding="utf-8")
        (folder / f"{tid}_0.jpg").write_bytes(b"\xff\xd8\xffsynthetic")
        comments = folder / "comments"
        comments.mkdir(exist_ok=True)
        (comments / f"{tid}.json").write_text(json.dumps({
            "tweet_id": tid, "user": user, "status": "ok", "comments": [
                {"id": "3001", "parent_id": tid, "content": "synthetic reply"}]}), encoding="utf-8")
        return folder
