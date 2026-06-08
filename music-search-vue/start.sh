#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
echo "启动音乐搜索服务: http://127.0.0.1:5178"
.venv/bin/python server.py
