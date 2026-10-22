"""gallerydl 调度逻辑回归测试：纯本地模拟数据，不调用真实 X，不执行下载。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "GalleryDL-X订阅"))

import scheduler


class ComputeNextTest(unittest.TestCase):
    def test_window_bounds(self):
        sub = {"id": "t1", "frequency": "daily", "window_start": 9, "window_end": 20}
        for _ in range(50):
            nxt = scheduler.compute_next(sub)
            import datetime
            dt = datetime.datetime.fromtimestamp(nxt)
            secs = dt.hour * 3600 + dt.minute * 60 + dt.second
            self.assertGreaterEqual(secs, 9 * 3600)
            self.assertLessEqual(secs, 20 * 3600 - 1)

    def test_after_adds_days(self):
        sub = {"id": "t1", "frequency": "weekly", "window_start": 9, "window_end": 20}
        base = 1700000000
        nxt = scheduler.compute_next(sub, after=base)
        self.assertGreater(nxt, base + 6 * 86400)


class ClassifyErrorTest(unittest.TestCase):
    def test_cookie(self):
        self.assertIn("Cookie", scheduler.classify_error("AuthRequired: need login", 1))

    def test_ratelimit(self):
        self.assertIn("限流", scheduler.classify_error("HTTP 429 too many", 1))

    def test_network(self):
        self.assertIn("网络", scheduler.classify_error("ProxyError: Failed to establish", 1))

    def test_clean(self):
        self.assertEqual(scheduler.classify_error("all ok", 0), "")

    def test_exit_code(self):
        self.assertIn("7", scheduler.classify_error("weird", 7))


class CommentRecordTest(unittest.TestCase):
    def _item(self, tid="222", content="hello"):
        return {
            "tweet_id": tid,
            "reply_id": None,
            "content": content,
            "date": "2026-09-18 10:00:00",
            "lang": "zh",
            "favorite_count": 3,
            "author": {"id": "9", "name": "someone", "nick": "某人"},
        }

    def test_focal_filtered(self):
        self.assertIsNone(scheduler.comment_record(self._item(tid="111"), "111"))

    def test_parent_fallback(self):
        rec = scheduler.comment_record(self._item(), "111")
        self.assertEqual(rec["id"], "222")
        self.assertEqual(rec["parent_id"], "111")
        self.assertEqual(rec["author_name"], "someone")
        self.assertTrue(rec["url"].endswith("/status/222"))

    def test_parent_kept(self):
        m = self._item()
        m["reply_id"] = "333"
        rec = scheduler.comment_record(m, "111")
        self.assertEqual(rec["parent_id"], "333")


class JobsRoundtripTest(unittest.TestCase):
    def test_save_load_trim(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = scheduler.GDL
            scheduler.GDL = tmp
            try:
                jobs = [{"job_id": f"j{i:03d}"} for i in range(60)]
                scheduler._save_jobs(jobs)
                loaded = scheduler._load_jobs()
                self.assertEqual(len(loaded), 50)
                self.assertEqual(loaded[0]["job_id"], "j010")
                scheduler._update_job("j059", status="done")
                self.assertEqual(scheduler._find_job("j059")["status"], "done")
            finally:
                scheduler.GDL = old


if __name__ == "__main__":
    unittest.main()
