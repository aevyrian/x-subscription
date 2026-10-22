"""C1/C5: current subscription storage and archive preservation."""
import unittest

from support.legacy_env import LegacyEnvironment, backend


class SubscriptionContract(unittest.TestCase):
    def test_add_duplicate_edit_pause_resume_delete_preserves_archive(self):
        with LegacyEnvironment() as env:
            folder = env.archived_tweet()
            sub, first = backend.add_sub({"url": "https://x.com/exampleuser", "frequency": "weekly"})
            self.assertEqual(first["status"], "queued")
            self.assertEqual(first["trigger"], "first")
            self.assertEqual(len(backend._load_subs()), 1)
            with self.assertRaises(ValueError):
                backend.add_sub({"url": "https://x.com/exampleuser"})
            changed = backend.update_sub(sub["id"], {"frequency": "daily", "enabled": "false", "window_start": 8})
            self.assertEqual((changed["frequency"], changed["enabled"], changed["window_start"]), ("daily", False, 8))
            with self.assertRaises(ValueError):
                backend.trigger_sub(sub["id"])
            self.assertTrue(backend.update_sub(sub["id"], {"enabled": "true"})["enabled"])
            backend.delete_sub(sub["id"])
            self.assertEqual(backend._load_subs(), [])
            for relative in ("exampleuser_2001.json", "2001_0.jpg", "comments/2001.json"):
                self.assertTrue((folder / relative).is_file())

    def test_edit_recomputes_existing_next_run(self):
        with LegacyEnvironment() as env:
            sub, _ = backend.add_sub({"url": "https://x.com/exampleuser", "frequency": "weekly"})
            next_file = env.gdl / f".next_{sub['id']}"
            before = next_file.read_text(encoding="utf-8")
            backend.update_sub(sub["id"], {"frequency": "daily", "window_start": 6, "window_end": 7})
            self.assertNotEqual(next_file.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
