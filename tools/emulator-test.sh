#!/usr/bin/env bash
# 在 Android 模拟器上验证「装完 APK 就能用」这条链路（macOS + 本机 SDK）。
#
# 覆盖的是 macOS 上测不到的部分：Chaquopy 内嵌引擎能不能起、jniLibs 里的 adb
# 能不能 execve、它依赖的 .so 找不找得到、连电视 + 发按键 + 截图整条路通不通。
#
# 踩过的坑（都有回归测试兜底，见 tests/test_android_engine.py）：
#   - boot.py 里 from com.chaquo.python import Android 在后台线程会
#     ModuleNotFoundError，导致引擎根本没起来 → 路径改由 NativeActivity 从 Java 传
#   - adb 的依赖是传递的：libprotobuf 还要 libabsl_* / libbrotlicommon / libutf8_*，
#     少一个就 CANNOT LINK EXECUTABLE
#   - jniLibs 只打包 *.so，所以 adb 必须叫 libadb.so
#
# 用法：tools/emulator-test.sh [AVD 名，默认 test_avd]
set -euo pipefail
cd "$(dirname "$0")/.."
AVD="${1:-test_avd}"
SDK="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
ADB="$SDK/platform-tools/adb"
PKG=com.atv.remote.native
APK=ATVRemote-native.apk
PLIST="$HOME/Library/LaunchAgents/com.atv.emulator.plist"

echo "== 1/6 起模拟器（headless，交给 launchd 免得被 exec 会话收掉） =="
python3 - "$AVD" "$PLIST" "$SDK" <<'PYEOF'
import os, plistlib, sys
avd, plist, sdk = sys.argv[1], sys.argv[2], sys.argv[3]
plistlib.dump({
    "Label": "com.atv.emulator",
    "ProgramArguments": [os.path.join(sdk, "emulator", "emulator"),
                         "-avd", avd, "-no-window", "-no-audio", "-no-boot-anim",
                         "-no-snapshot-save", "-gpu", "swiftshader_indirect",
                         "-port", "5554"],
    "RunAtLoad": False, "KeepAlive": False,
    "StandardOutPath": "/tmp/emu.log", "StandardErrorPath": "/tmp/emu.log",
}, open(plist, "wb"))
PYEOF
launchctl bootout gui/501/com.atv.emulator 2>/dev/null || true
launchctl bootstrap gui/501 "$PLIST"
launchctl kickstart gui/501/com.atv.emulator
"$ADB" wait-for-device
for i in $(seq 1 60); do
  [ "$("$ADB" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ] && break
  sleep 2
done
echo "   ABI: $("$ADB" shell getprop ro.product.cpu.abi | tr -d '\r')（APK 里的 adb 只有 arm64）"

echo "== 2/6 装 APK =="
"$ADB" install -r "$APK"

echo "== 3/6 启动 App，看引擎横幅 =="
"$ADB" logcat -c
"$ADB" shell am start -n "$PKG/com.atv.remote.NativeActivity" >/dev/null
sleep 12
"$ADB" logcat -d | grep -E "python.stdout" | sed 's/^.*python.stdout: //' | tail -12

echo "== 4/6 让模拟器自己变成「电视」：开 adb over TCP =="
"$ADB" shell setprop service.adb.tcp.port 5555
sleep 2
"$ADB" forward tcp:18300 tcp:8300

echo "== 5/6 打 App 内嵌服务的 HTTP 接口 =="
"$ADB" shell am force-stop "$PKG"
"$ADB" shell am start -n "$PKG/com.atv.remote.NativeActivity" >/dev/null
sleep 14
curl -s "http://127.0.0.1:18300/api/status" |
  python3 -c "import json,sys; d=json.load(sys.stdin); print('adb:', d['adb_found'], d['adb_version'])"
curl -s -X POST -H 'Content-Type: application/json' -d '{"target":"127.0.0.1"}' \
  "http://127.0.0.1:18300/api/connect"
curl -s -X POST -H 'Content-Type: application/json' -d '{"type":"key","code":24}' \
  "http://127.0.0.1:18300/api/cmd"
curl -s -o /tmp/emulator-shot.png "http://127.0.0.1:18300/api/screenshot"
python3 -c "from PIL import Image; print('screenshot:', Image.open('/tmp/emulator-shot.png').size)"

echo "== 6/6 界面长什么样（截图 + OCR） =="
"$ADB" exec-out screencap -p > /tmp/emulator-ui.png
[ -x /tmp/ocrtool ] && /tmp/ocrtool /tmp/emulator-ui.png | head -12 || true

echo "== 收尾：停掉模拟器 =="
launchctl bootout gui/501/com.atv.emulator || true
echo "✅ 模拟器验证完成"

