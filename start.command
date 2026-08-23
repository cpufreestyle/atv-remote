#!/bin/zsh
# ATV Remote 启动脚本（macOS 双击运行）
cd "$(dirname "$0")"
if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python server.py
else
  exec /opt/homebrew/bin/python3 server.py
fi
