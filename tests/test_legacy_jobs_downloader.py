"""C2/C3/C4/C10: downloader command and job results without a real process."""
import io
import json
import os
import subprocess
import unittest
from unittest import mock

from support.legacy_env import LegacyEnvironment, backend, scheduler


def sub(first_range=25, save_videos=True, save_metadata=True):
    return {"id": "s1", "user": "exampleuser", "url": "https://x.com/exampleuser",
            "frequency": "weekly", "first_range": first_range,
            "save_videos": save_videos, "save_metadata": save_metadata, "enabled": True}


class _CaptureOverride(io.StringIO):
    def __init__(self, sink):
        super().__init__()
        self.sink = sink

    def close(self):
        if not self.closed:
            self.sink.append(json.loads(self.getvalue()))
        super().close()


class DownloaderContract(unittest.TestCase):
    def run_synthetic(self, env, first, output, rc=0, config=None):
        current = config or sub()
        env.write_jobs([{"job_id": "j1", "sub_id": "s1", "status": "queued"}])
        captured_args, overrides, statuses = [], [], []
        real_open = open

        def safe_open(path, mode="r", *args, **kwargs):
            if str(path) == "/tmp/gdl_over.json":
                self.assertEqual(mode, "w")
                return _CaptureOverride(overrides)
            return real_open(path, mode, *args, **kwargs)

        def fake_download(args, timeout=3600):
            captured_args.append(args)
            with real_open(args[4], encoding="utf-8") as stream:
                overrides.append(json.load(stream))
            statuses.append(scheduler._find_job("j1")["status"])
            return output, rc, output

        with mock.patch("builtins.open", side_effect=safe_open), mock.patch.object(scheduler, "run_gallery_dl", side_effect=fake_download):
            result = scheduler.run_sub(current, first=first, job_id="j1", trigger="manual")
        return result, scheduler._find_job("j1"), captured_args[0], overrides[0], statuses

    def test_first_run_command_counts_and_archive_config(self):
        with LegacyEnvironment() as env:
            (env.gdl / "config.json").write_text(json.dumps({"extractor": {"archive": "/config/archive.sqlite", "twitter": {}}}), encoding="utf-8")
            result, job, args, override, statuses = self.run_synthetic(
                env, True, "/downloads/twitter/exampleuser/2001_0.jpg\n/downloads/twitter/exampleuser/exampleuser_2001.json\n# /downloads/twitter/exampleuser/old.jpg\n")
            self.assertEqual(result[0], "done")
            self.assertEqual(statuses, ["running"])
            self.assertEqual((job["status"], job["new_media"], job["new_json"], job["skipped"]), ("done", 1, 1, 1))
            self.assertEqual(args[:2], ["gallery-dl", "-c"])
            self.assertEqual(os.path.normpath(args[2]), str(env.gdl / "config.json"))
            self.assertEqual(args[3], "-c")
            self.assertFalse(os.path.exists(args[4]))
            self.assertEqual(args[-3:], ["--range", "1-25", "https://x.com/exampleuser"])
            self.assertTrue(override["extractor"]["twitter"]["videos"])
            self.assertEqual(override["extractor"]["twitter"]["postprocessors"][0]["name"], "metadata")
            self.assertEqual(json.loads((env.gdl / "config.json").read_text())["extractor"]["archive"], "/config/archive.sqlite")
            self.assertTrue((env.gdl / ".last_s1").is_file())
            self.assertFalse((env.gdl / "archive.sqlite").exists())

    def test_incremental_no_full_refetch_no_new_and_toggles(self):
        with LegacyEnvironment() as env:
            (env.gdl / "config.json").write_text(json.dumps({"extractor": {"archive": "/config/archive.sqlite", "twitter": {}}}), encoding="utf-8")
            result, job, args, override, statuses = self.run_synthetic(
                env, False, "", config=sub(save_videos=False, save_metadata=False))
            self.assertEqual(result[0], "done")
            self.assertEqual(statuses, ["running"])
            self.assertNotIn("--range", args)
            self.assertEqual(os.path.normpath(args[2]), str(env.gdl / "config.json"))
            self.assertEqual(json.loads((env.gdl / "config.json").read_text())["extractor"]["archive"], "/config/archive.sqlite")
            self.assertFalse((env.gdl / "archive.sqlite").exists())
            self.assertEqual((job["new_media"], job["new_json"], job["skipped"]), (0, 0, 0))
            self.assertFalse(override["extractor"]["twitter"]["videos"])
            self.assertEqual(override["extractor"]["twitter"]["postprocessors"], [])
            # Known legacy representation: done + zero counts, no explicit changed field.
            self.assertNotIn("changed", job)

    def test_failed_run_records_result(self):
        with LegacyEnvironment() as env:
            result, job, _, _, statuses = self.run_synthetic(env, False, "AuthRequired", rc=1)
            self.assertEqual(result[0], "failed")
            self.assertEqual(statuses, ["running"])
            self.assertEqual((job["status"], job["exit_code"]), ("failed", 1))
            self.assertFalse((env.gdl / ".last_s1").exists())

    def test_manual_duplicate_prevention_and_first_metadata(self):
        with LegacyEnvironment() as env:
            env.write_subs([sub()])
            first = backend._queue_first_run(sub())
            self.assertEqual(first["trigger"], "first")
            with self.assertRaises(ValueError):
                backend.trigger_sub("s1")
            jobs = backend._load_jobs()
            jobs[0]["status"] = "done"
            backend._save_jobs(jobs)
            manual = backend.trigger_sub("s1")
            self.assertEqual(manual["trigger"], "manual")
            self.assertEqual(manual["status"], "queued")

    def test_scheduled_job_metadata_and_no_range_after_prior_run(self):
        with LegacyEnvironment() as env:
            env.write_jobs([])
            real_open = open
            # A scheduled invocation creates its own queued job before downloader call.
            with mock.patch.object(scheduler, "run_gallery_dl", return_value=("", 0, "")), \
                    mock.patch("builtins.open", side_effect=lambda p, mode="r", *a, **k: _CaptureOverride([]) if str(p) == "/tmp/gdl_over.json" else real_open(p, mode, *a, **k)):
                status, err = scheduler.run_sub(sub(), first=False, trigger="schedule")
            self.assertEqual((status, err), ("done", ""))
            jobs = scheduler._load_jobs()
            self.assertEqual((jobs[0]["trigger"], jobs[0]["status"]), ("schedule", "done"))

    def test_timeout_retry_and_error_classification(self):
        with LegacyEnvironment():
            with mock.patch.object(scheduler.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 4, "", "ProxyError: synthetic"),
                subprocess.CompletedProcess([], 0, "ok", "")]), mock.patch.object(scheduler.time, "sleep") as sleep:
                out, code, _ = scheduler.run_gallery_dl(["gallery-dl"], timeout=2)
            self.assertEqual((out, code), ("ok", 0))
            sleep.assert_called_once()
            with mock.patch.object(scheduler.subprocess, "run", side_effect=subprocess.TimeoutExpired(["gallery-dl"], 2, output=b"partial")):
                _, code, _ = scheduler.run_gallery_dl(["gallery-dl"], timeout=2)
            self.assertEqual(code, 124)
            self.assertIn("Cookie", scheduler.classify_error("AuthRequired", 1))
            self.assertIn("限流", scheduler.classify_error("HTTP 429", 1))


if __name__ == "__main__":
    unittest.main()
