"""MeTube 配置与目录隔离检查：只读文本比对，不启动容器，不下载。"""
import os
import subprocess
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class MetubeConfigTest(unittest.TestCase):
    def test_compose_keys(self):
        text = read(os.path.join("MeTube", "compose.yaml"))
        for key in ("metube", "8081:8081", "DOWNLOAD_DIR=/downloads",
                    "OUTPUT_TEMPLATE", "MAX_CONCURRENT_DOWNLOADS"):
            self.assertIn(key, text)

    def test_dir_isolation(self):
        metube = read(os.path.join("MeTube", "compose.yaml"))
        gdl = read(os.path.join("GalleryDL-X订阅", "compose.yaml"))
        self.assertIn("/vol1/1000/Docker/MeTube/downloads", metube)
        self.assertNotIn("/vol1/1000/Docker/MeTube/downloads", gdl)
        self.assertIn("GalleryDL", gdl)


class SensitiveIgnoredTest(unittest.TestCase):
    IGNORED = [
        "GalleryDL-X订阅/cookies.txt",
        "GalleryDL-X订阅/config.json",
        "GalleryDL-X订阅/Reader/compose.yaml",
        "MeTube/compose.yaml",
    ]

    def test_sensitive_not_tracked(self):
        for rel in self.IGNORED:
            r = subprocess.run(["git", "check-ignore", "-q", rel],
                               cwd=ROOT)
            self.assertEqual(r.returncode, 0, f"{rel} 应当被忽略")

    def test_no_tracked_cookie(self):
        r = subprocess.run(["git", "ls-files"], cwd=ROOT,
                           capture_output=True, text=True)
        tracked = r.stdout.split()
        self.assertNotIn("GalleryDL-X订阅/cookies.txt", tracked)
        self.assertFalse(any(p.endswith(".sqlite") for p in tracked))


if __name__ == "__main__":
    unittest.main()
