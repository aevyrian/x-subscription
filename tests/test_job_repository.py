"""Targeted tests for the shared legacy .jobs.json Repository."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
GDL_ROOT = ROOT / "GalleryDL-X订阅"
READER_ROOT = GDL_ROOT / "Reader"
sys.path.insert(0, str(GDL_ROOT))
sys.path.insert(0, str(READER_ROOT))

from shared.job_repository import LegacyJobRepository
import backend
import scheduler


class LegacyJobRepositoryTest(unittest.TestCase):
    def test_missing_file_returns_empty_without_creating_jobs_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".jobs.json"
            repo = LegacyJobRepository(path)
            self.assertEqual(repo.load(), [])
            self.assertFalse(path.exists())

    def test_empty_state_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".jobs.json"
            path.write_text('{"jobs": []}', encoding="utf-8")
            self.assertEqual(LegacyJobRepository(path).load(), [])

    def test_save_load_roundtrip_preserves_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".jobs.json"
            jobs = [{"job_id": "j1", "status": "queued"}, {"job_id": "j2", "status": "done"}]
            repo = LegacyJobRepository(path)
            repo.save(jobs)
            self.assertEqual(repo.load(), jobs)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"jobs": jobs})

    def test_append_and_extend_preserve_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = LegacyJobRepository(Path(tmp) / ".jobs.json")
            repo.append({"job_id": "j1"})
            repo.extend([{"job_id": "j2"}, {"job_id": "j3"}])
            self.assertEqual([job["job_id"] for job in repo.load()], ["j1", "j2", "j3"])

    def test_update_matches_legacy_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = LegacyJobRepository(Path(tmp) / ".jobs.json")
            repo.save([{"job_id": "j1", "status": "queued"}, {"job_id": "j2", "status": "queued"}])
            repo.update("j2", status="done", exit_code=0)
            self.assertEqual(repo.load(), [
                {"job_id": "j1", "status": "queued"},
                {"job_id": "j2", "status": "done", "exit_code": 0},
            ])

    def test_trim_keeps_latest_200(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = LegacyJobRepository(Path(tmp) / ".jobs.json")
            repo.save([{"job_id": f"j{i:03d}"} for i in range(260)])
            jobs = repo.load()
            self.assertEqual(len(jobs), 200)
            self.assertEqual(jobs[0]["job_id"], "j060")
            self.assertEqual(jobs[-1]["job_id"], "j259")

    def test_legacy_module_path_injection_uses_only_temp_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend_path = root / "backend" / ".jobs.json"
            scheduler_root = root / "scheduler"
            scheduler_path = scheduler_root / ".jobs.json"
            with mock.patch.object(backend, "JOB_FILE", backend_path):
                backend._append_job({"job_id": "b1", "status": "queued"})
                self.assertEqual(backend._load_jobs(), [{"job_id": "b1", "status": "queued"}])
                self.assertEqual(backend._job_repository().path, backend_path)
            with mock.patch.object(scheduler, "GDL", str(scheduler_root)):
                scheduler._append_job({"job_id": "s1", "status": "queued"})
                scheduler._update_job("s1", status="done")
                self.assertEqual(scheduler._load_jobs(), [{"job_id": "s1", "status": "done"}])
                self.assertEqual(scheduler._job_repository().path, scheduler_path)
            self.assertTrue(backend_path.is_file())
            self.assertTrue(scheduler_path.is_file())

    def test_backend_and_scheduler_share_repository_implementation(self):
        self.assertIs(type(backend._job_repository()), LegacyJobRepository)
        self.assertIs(type(scheduler._job_repository()), LegacyJobRepository)


if __name__ == "__main__":
    unittest.main()
