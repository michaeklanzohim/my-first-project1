#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

# 释放被旧进程占用的端口，避免 "Address already in use" 导致启动失败
PORT=5178
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
elif command -v lsof >/dev/null 2>&1; then
  lsof -ti:"${PORT}" | xargs -r kill -9 >/dev/null 2>&1 || true
fi
sleep 1

echo "启动音乐搜索服务: http://127.0.0.1:${PORT}"
.venv/bin/python server.py
