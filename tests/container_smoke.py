"""Real Docker lifecycle check using isolated, disposable named volumes."""
import http.cookiejar
import json
import secrets
import subprocess
import time
import urllib.request
import uuid


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main():
    name = "xarchive-check-" + uuid.uuid4().hex[:12]
    volumes = [name + suffix for suffix in ("-config", "-data", "-cache")]
    password = secrets.token_urlsafe(24)
    try:
        args = ["run", "-d", "--name", name, "-p", "127.0.0.1::9090",
                "-e", "LOGIN_PASSWORD=" + password]
        for volume, target in zip(volumes, ("/config", "/downloads", "/cache")):
            args += ["-v", volume + ":" + target]
        docker(*args, "xarchive:check")
        for restart in (False, True):
            if restart:
                docker("restart", name)
            binding = docker("port", name, "9090/tcp").splitlines()[0]
            base = "http://" + binding
            for _ in range(80):
                try:
                    with urllib.request.urlopen(base + "/login", timeout=2) as response:
                        assert response.status == 200
                    break
                except (OSError, TimeoutError):
                    time.sleep(0.25)
            else:
                raise RuntimeError("reader did not start")
            client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            request = urllib.request.Request(base + "/api/login", json.dumps({"password": password}).encode(),
                                             {"Content-Type": "application/json"})
            with client.open(request) as response:
                assert response.status == 200
            with client.open(base + "/api/tweets") as response:
                assert response.status == 200
            marker = "from pathlib import Path; p=Path('/config/smoke-marker'); "
            marker += "assert p.read_text() == 'persisted'" if restart else "p.write_text('persisted')"
            docker("exec", name, "python", "-c", marker)
            docker("exec", name, "python", "-c", "import os; assert os.getuid() == 1000")
        print("PASS: build runtime, login, API, non-root, persistence, restart")
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=False, capture_output=True)
        for volume in volumes:
            subprocess.run(["docker", "volume", "rm", volume], check=False, capture_output=True)


if __name__ == "__main__":
    main()
