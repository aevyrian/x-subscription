"""Real gallery-dl HTTP download + SQLite dedup, using a local fixture only."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

PAYLOAD = b"\xff\xd8\xffsynthetic-local-download-fixture"


class Fixture(BaseHTTPRequestHandler):
    hits = 0
    def log_message(self, *args):
        pass
    def do_GET(self):
        type(self).hits += 1
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="xarchive-engine-") as folder:
            root = Path(folder)
            command = [sys.executable, "-m", "gallery_dl", "--ignore-config", "--download-archive",
                       str(root / "archive.sqlite"), "-D", str(root / "media"),
                       f"http://127.0.0.1:{server.server_port}/fixture.jpg"]
            env = dict(os.environ, NO_PROXY="127.0.0.1,localhost")
            first = subprocess.run(command, env=env, capture_output=True, timeout=30)
            assert first.returncode == 0, first.stderr.decode(errors="replace")
            files = list((root / "media").rglob("*.jpg"))
            assert len(files) == 1 and files[0].read_bytes() == PAYLOAD
            assert (root / "archive.sqlite").is_file()
            hits = Fixture.hits
            second = subprocess.run(command, env=env, capture_output=True, timeout=30)
            assert second.returncode == 0
            assert Fixture.hits == hits, "duplicate download made another HTTP request"
            print("PASS: real gallery-dl download, exact file bytes, SQLite archive, no duplicate request")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
