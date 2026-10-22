import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from support.legacy_env import LegacyEnvironment, backend, scheduler

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("archive_service", ROOT / "GalleryDL-X订阅/service.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class ContainerContracts(unittest.TestCase):
    def test_initialize_and_restart_preserve_config_archive_and_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = dict(LOGIN_PASSWORD="synthetic-test", GDL_CONFIG=str(root / "config"),
                       DATA_DIR=str(root / "downloads"), CACHE_DIR=str(root / "cache"))
            with mock.patch.dict(os.environ, env):
                config, data, _ = service.initialize()
                settings = json.loads((config / "config.json").read_text())
                self.assertNotIn("proxy", settings["extractor"])
                self.assertEqual(settings["extractor"]["base-directory"], str(data))
                (config / "config.json").write_text('{"custom": true}')
                (config / "archive.sqlite").write_bytes(b"synthetic-archive")
                (data / "photo.jpg").write_bytes(b"synthetic-image")
                service.initialize()
                self.assertEqual((config / "config.json").read_text(), '{"custom": true}')
                self.assertEqual((config / "archive.sqlite").read_bytes(), b"synthetic-archive")
                self.assertEqual((data / "photo.jpg").read_bytes(), b"synthetic-image")

    def test_missing_password_fails_before_creating_storage(self):
        with mock.patch.dict(os.environ, {"LOGIN_PASSWORD": ""}):
            with self.assertRaises(ValueError):
                service.initialize()

    def test_x_urls_and_spoofed_hosts(self):
        for value in ("https://x.com/exampleuser/media", "https://twitter.com/exampleuser/status/2001", "@exampleuser"):
            self.assertEqual(backend.parse_user(value), "exampleuser")
        for value in ("https://evil.test/x.com/exampleuser", "https://x.com.evil.test/exampleuser", "https://x.com@evil.test/exampleuser", "https://x.com/home", "file:///x.com/exampleuser"):
            self.assertIsNone(backend.parse_user(value))
        with LegacyEnvironment():
            with self.assertRaises(ValueError):
                backend.add_sub({"url": "https://evil.test/x.com/exampleuser", "user": "exampleuser"})

    def test_newer_reader_favorites_and_codes_survive_restart(self):
        with LegacyEnvironment() as env, mock.patch.object(backend, "FAVORITES_FILE", Path(tempfile.gettempdir()) / "unused"):
            with mock.patch.object(backend, "FAVORITES_FILE", env.gdl / ".reader-favorites.json"):
                backend.set_favorite({"user": "ExampleUser", "favorite": True})
                self.assertEqual(backend.list_favorites(), ["exampleuser"])
                sub, _ = backend.add_sub({"url": "https://x.com/exampleuser"})
                backend.ensure_subscription_codes()
                stored = backend._load_subs()[0]
                self.assertTrue(stored.get("reader_code"))
                self.assertEqual(stored["id"], sub["id"])


if __name__ == "__main__":
    unittest.main()
