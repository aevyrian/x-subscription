"""Loopback-only synthetic UI preview. No scheduler and no real X account."""
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="xarchive-preview-") as folder:
    root = Path(folder)
    for name in ("config", "downloads", "cache"):
        (root / name).mkdir()
    os.environ.update(GDL_CONFIG=str(root / "config"), DATA_DIR=str(root / "downloads"),
                      CACHE_DIR=str(root / "cache"), LOGIN_PASSWORD="preview-test-password", PROXY="")
    sys.path[:0] = [str(ROOT / "GalleryDL-X订阅"), str(ROOT / "GalleryDL-X订阅/Reader")]
    import backend
    user = root / "downloads/twitter/exampleuser"
    user.mkdir(parents=True)
    (user / "exampleuser_2001.json").write_text(json.dumps({"tweet_id": "2001", "content": "这是一条用于界面验收的演示归档。", "date": "2026-10-22 12:00:00", "author": {"name": "exampleuser", "nick": "示例博主"}}), encoding="utf-8")
    server = backend.ThreadingHTTPServer(("127.0.0.1", 19090), backend.Handler)
    print("Synthetic preview: http://127.0.0.1:19090", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
