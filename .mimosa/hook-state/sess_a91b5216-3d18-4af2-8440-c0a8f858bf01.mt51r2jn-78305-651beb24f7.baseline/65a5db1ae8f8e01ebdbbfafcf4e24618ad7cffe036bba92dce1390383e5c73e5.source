#!/data/data/com.termux/files/usr/bin/bash
# Termux 引擎启动脚本（由 ATVRemote App 通过 RUN_COMMAND 拉起）
cd "$(dirname "$0")"
exec >> "$HOME/atv-remote/server.log" 2>&1
echo "---- $(date) ----"
command -v termux-wake-lock >/dev/null && termux-wake-lock
ADB_BIN="$(command -v adb || echo /data/data/com.termux/files/usr/bin/adb)"
python server.py --host 127.0.0.1 --adb "$ADB_BIN" --no-open
