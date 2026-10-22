"""Supervise the existing reader and scheduler in a single container."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def initialize():
    if not os.environ.get("LOGIN_PASSWORD"):
        raise ValueError("LOGIN_PASSWORD must be configured")
    config = Path(os.environ.get("GDL_CONFIG", "/config"))
    data = Path(os.environ.get("DATA_DIR", "/downloads"))
    cache = Path(os.environ.get("CACHE_DIR", "/cache"))
    for folder in (config, data, cache, config / "cookies"):
        folder.mkdir(parents=True, exist_ok=True)
    target = config / "config.json"
    if not target.exists():
        settings = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        extractor = settings["extractor"]
        extractor.pop("proxy", None)
        extractor["base-directory"] = str(data)
        extractor["archive"] = str(config / "archive.sqlite")
        extractor["twitter"]["cookies"] = str(config / "cookies" / "cookies.txt")
        with target.open("x", encoding="utf-8") as stream:
            json.dump(settings, stream, ensure_ascii=False, indent=2)
    return config, data, cache


def main():
    config, data, cache = initialize()
    env = dict(os.environ, GDL_CONFIG=str(config), DATA_DIR=str(data), CACHE_DIR=str(cache),
               PYTHONUNBUFFERED="1")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    if env.get("PROXY"):
        env.update(HTTP_PROXY=env["PROXY"], HTTPS_PROXY=env["PROXY"])
    children = []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    result = 0
    try:
        for script in (ROOT / "scheduler.py", ROOT / "Reader" / "backend.py"):
            children.append(subprocess.Popen([sys.executable, str(script)], env=env,
                                             start_new_session=True))
        while not stopping:
            if any(child.poll() is not None for child in children):
                result = 1
                break
            time.sleep(0.25)
    finally:
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 10
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
