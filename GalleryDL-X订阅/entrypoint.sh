#!/bin/sh
# 容器入口：调用 Python 订阅调度器（按 subscriptions.json 逐订阅排程）
echo "[entrypoint] starting subscription scheduler"
[ -f /config/scheduler.py ] || { echo "[entrypoint] ERROR: scheduler.py missing" >&2; exit 1; }
exec python3 /config/scheduler.py