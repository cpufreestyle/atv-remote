#!/bin/zsh
# ATV Remote — Mac 一键安装：开机自启 + 崩溃自动重启（之后无需任何手动操作）
set -e
cd "$(dirname "$0")"
PY="$PWD/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

echo "停掉旧实例..."
pkill -f "python.*server.py --no-open" 2>/dev/null || true
sleep 1

PLIST="$HOME/Library/LaunchAgents/com.atv.remote.plist"
mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.atv.remote</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PY</string>
        <string>$PWD/server.py</string>
        <string>--no-open</string>
    </array>
    <key>WorkingDirectory</key><string>$PWD</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>5</integer>
    <key>StandardOutPath</key><string>$PWD/server.log</string>
    <key>StandardErrorPath</key><string>$PWD/server.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
sleep 2
curl -s -o /dev/null -w "服务状态: HTTP %{http_code} → http://127.0.0.1:8300\n" http://127.0.0.1:8300/
echo "✅ 已设为开机自启（登录后自动运行，崩溃自动重启）"
echo "   取消自启: launchctl unload $PLIST && rm $PLIST"
