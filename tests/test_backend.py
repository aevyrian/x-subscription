"""xreader 后端回归测试：临时目录模拟数据，不触碰真实下载目录。"""
import json
import os
import sys
import tempfile
import unittest
import io
from email.message import Message
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "GalleryDL-X订阅", "Reader"))

import backend


def reset_cache():
    backend._cache.update(signature=None, users=[], tweets=[], avatars={}, by_tweet={})


class ParseUserTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(backend.parse_user("https://x.com/SomeUser/media"), "SomeUser")

    def test_invalid(self):
        self.assertIsNone(backend.parse_user("https://example.com/none"))
        self.assertIsNone(backend.parse_user(""))


class RedactLogTest(unittest.TestCase):
    def test_proxy_masked(self):
        out = backend.redact_log("via http://user:pass@10.0.0.2:7890 ok")
        self.assertNotIn("user:pass", out)
        self.assertIn("***:***@", out)

    def test_token_masked(self):
        out = backend.redact_log('auth_token: "abc123" done')
        self.assertNotIn("abc123", out)


class SecurityBoundaryTest(unittest.TestCase):
    def _handler(self, headers=None, body=b""):
        h = object.__new__(backend.Handler)
        h.headers = Message()
        for key, value in (headers or {}).items():
            h.headers[key] = value
        h.rfile = io.BytesIO(body)
        return h

    def test_login_requires_valid_server_session(self):
        old_password, old_ready = backend.LOGIN_PASSWORD, backend.LOGIN_READY
        try:
            backend.LOGIN_PASSWORD = "fixed-password"
            backend.LOGIN_READY = True
            h = self._handler()
            self.assertFalse(h._auth_ok())
            with backend._session_lock:
                backend._sessions["test-session"] = backend.time.time() + 60
            h = self._handler({"Cookie": "xr_session=test-session"})
            self.assertTrue(h._auth_ok())
        finally:
            with backend._session_lock:
                backend._sessions.pop("test-session", None)
            backend.LOGIN_PASSWORD, backend.LOGIN_READY = old_password, old_ready

    def test_json_body_limit(self):
        h = self._handler({"Content-Length": str(backend.MAX_JSON_BODY + 1)})
        with self.assertRaises(OverflowError):
            h._read_json()

    def test_avatar_host_allowlist(self):
        self.assertTrue(backend._avatar_url_ok("https://pbs.twimg.com/a.jpg"))
        self.assertFalse(backend._avatar_url_ok("http://pbs.twimg.com/a.jpg"))
        self.assertFalse(backend._avatar_url_ok("https://127.0.0.1/a.jpg"))


class ReadCommentsTest(unittest.TestCase):
    def test_missing_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = backend.DATA
            backend.DATA = Path(tmp)
            try:
                d = backend.read_comments("ghost", "123")
                self.assertEqual(d["status"], "none")
                self.assertEqual(d["comments"], [])
            finally:
                backend.DATA = old


class ScanTest(unittest.TestCase):
    def _tweet_json(self):
        return {
            "tweet_id": "2001",
            "date": "2026-09-18 10:00:00",
            "content": "测试推文 #tag",
            "hashtags": ["tag"],
            "favorite_count": 5,
            "retweet_count": 1,
            "reply_count": 2,
            "view_count": 100,
            "author": {"name": "mockuser", "nick": "模拟用户", "id": "42",
                       "profile_image": "", "description": "d", "followers_count": 7},
        }

    def test_scan_groups_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            udir = Path(tmp) / "twitter" / "mockuser"
            udir.mkdir(parents=True)
            (udir / "mockuser_2001.json").write_text(
                json.dumps(self._tweet_json()), encoding="utf-8")
            (udir / "2001_0.jpg").write_bytes(b"\xff\xd8\xff000")
            (udir / "2001_1.mp4").write_bytes(b"\x00\x00\x00\x00")
            old = backend.DATA
            backend.DATA = Path(tmp)
            reset_cache()
            try:
                c = backend.scan()
                self.assertEqual(len(c["tweets"]), 1)
                media = c["tweets"][0]["media"]
                self.assertEqual(len(media), 2)
                users = [u for u in c["users"] if u["user"] == "mockuser"]
                self.assertEqual(users[0]["nick"], "模拟用户")
                self.assertEqual(users[0]["uid"], "42")
            finally:
                backend.DATA = old
                reset_cache()

    def test_tweet_detail_and_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            udir = Path(tmp) / "twitter" / "mockuser"
            udir.mkdir(parents=True)
            (udir / "mockuser_2001.json").write_text(
                json.dumps(self._tweet_json()), encoding="utf-8")
            (udir / "2001_0.jpg").write_bytes(b"\xff\xd8\xff000")
            cdir = udir / "comments"
            cdir.mkdir()
            payload = {"tweet_id": "2001", "user": "mockuser", "status": "ok",
                       "updated_ts": 1, "error": None,
                       "comments": [{"id": "3001", "parent_id": "2001",
                                     "author_name": "replier", "content": "赞同"}]}
            (cdir / "2001.json").write_text(json.dumps(payload), encoding="utf-8")
            old = backend.DATA
            backend.DATA = Path(tmp)
            reset_cache()
            try:
                d = backend.tweet_detail("2001")
                self.assertIsNotNone(d)
                self.assertEqual(len(d["media"]), 1)
                c = backend.read_comments("mockuser", "2001")
                self.assertEqual(c["status"], "ok")
                self.assertEqual(len(c["comments"]), 1)
            finally:
                backend.DATA = old
                reset_cache()


if __name__ == "__main__":
    unittest.main()
