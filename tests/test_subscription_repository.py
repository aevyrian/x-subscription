"""Targeted tests for the shared legacy subscriptions.json Repository."""

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

from shared.subscription_repository import LegacySubscriptionRepository
import backend
import scheduler


class LegacySubscriptionRepositoryTest(unittest.TestCase):
    def test_missing_file_returns_empty_without_creating_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subscriptions.json"
            repo = LegacySubscriptionRepository(path)
            self.assertEqual(repo.load(), [])
            self.assertFalse(path.exists())

    def test_save_load_roundtrip_preserves_schema_and_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subscriptions.json"
            subs = [{
                "id": "s1", "user": "example", "url": "https://x.com/example",
                "frequency": "custom", "custom_days": 3, "window_start": 6,
                "window_end": 9, "first_range": 25, "save_videos": False,
                "save_metadata": True, "enabled": False, "future_field": {"keep": True},
            }]
            repo = LegacySubscriptionRepository(path)
            repo.save(subs)
            self.assertEqual(repo.load(), subs)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"subs": subs})

    def test_invalid_or_non_mapping_state_matches_legacy_empty_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subscriptions.json"
            path.write_text("not-json", encoding="utf-8")
            self.assertEqual(LegacySubscriptionRepository(path).load(), [])
            path.write_text("[]", encoding="utf-8")
            self.assertEqual(LegacySubscriptionRepository(path).load(), [])

    def test_legacy_path_injection_redirects_backend_and_scheduler(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend_path = root / "backend" / "subscriptions.json"
            scheduler_root = root / "scheduler"
            scheduler_path = scheduler_root / "subscriptions.json"
            with mock.patch.object(backend, "SUB_FILE", backend_path):
                backend._save_subs([{"id": "b1", "enabled": True}])
                self.assertEqual(backend._load_subs(), [{"id": "b1", "enabled": True}])
                self.assertEqual(backend._subscription_repository().path, backend_path)
            scheduler_path.parent.mkdir(parents=True)
            scheduler_path.write_text(json.dumps({"subs": [{"id": "s1", "enabled": False}]}), encoding="utf-8")
            with mock.patch.object(scheduler, "GDL", str(scheduler_root)):
                self.assertEqual(scheduler.load_subs(), [{"id": "s1", "enabled": False}])
                self.assertEqual(scheduler._subscription_repository().path, scheduler_path)
            self.assertTrue(backend_path.is_file())
            self.assertTrue(scheduler_path.is_file())

    def test_backend_and_scheduler_share_repository_implementation(self):
        self.assertIs(type(backend._subscription_repository()), LegacySubscriptionRepository)
        self.assertIs(type(scheduler._subscription_repository()), LegacySubscriptionRepository)

    def test_backend_add_edit_pause_resume_delete_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub_path = root / "subscriptions.json"
            with mock.patch.object(backend, "GDL", root), \
                    mock.patch.object(backend, "SUB_FILE", sub_path), \
                    mock.patch.object(backend, "_init_next_run"), \
                    mock.patch.object(backend, "_queue_first_run", return_value={"status": "queued", "trigger": "first"}):
                sub, _ = backend.add_sub({"url": "https://x.com/example", "frequency": "weekly"})
                stored = backend._load_subs()
                self.assertEqual(stored[0]["id"], sub["id"])
                self.assertTrue(stored[0]["enabled"])
                backend.update_sub(sub["id"], {"frequency": "daily", "enabled": "false", "window_start": 8})
                stored = backend._load_subs()
                self.assertEqual((stored[0]["frequency"], stored[0]["enabled"], stored[0]["window_start"]), ("daily", False, 8))
                backend.update_sub(sub["id"], {"enabled": "true"})
                self.assertTrue(backend._load_subs()[0]["enabled"])
                backend.delete_sub(sub["id"])
                self.assertEqual(backend._load_subs(), [])


if __name__ == "__main__":
    unittest.main()
