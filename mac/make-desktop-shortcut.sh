#!/usr/bin/env bash
# 在桌面生成「ATV Remote.app」：双击即确保遥控服务在跑，并打开 Web 界面。
# 服务本来由 ~/Library/LaunchAgents/com.atv.remote.plist 托管（RunAtLoad + KeepAlive），
# 这个 app 只做两件事：服务万一没在跑就把它拉起来，然后用默认浏览器打开遥控器。
# 幂等：重复执行会覆盖重建。用法：./mac/make-desktop-shortcut.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
LABEL="com.atv.remote"
URL="http://127.0.0.1:8300/"
APP="${HOME}/Desktop/ATV Remote.app"

SRC="$(mktemp -t atvshortcut)"
trap 'rm -f "$SRC"' EXIT

cat > "$SRC" <<'APPLESCRIPT'
on server_ready()
	try
		set code to do shell script "curl -s -m 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:8300/api/status"
		if code is "200" then return true
	end try
	return false
end server_ready

on wait_ready(max_tries)
	repeat with i from 1 to max_tries
		delay 0.5
		if server_ready() then return true
	end repeat
	return false
end wait_ready

on ensure_server()
	if server_ready() then return true
	-- 服务没在跑：先把它交给开机自启的 LaunchAgent（KeepAlive 本该保证它一直在）
	try
		do shell script "launchctl kickstart -k gui/$(id -u)/__LABEL__ 2>/dev/null || launchctl load ~/Library/LaunchAgents/__LABEL__.plist 2>/dev/null || true"
	end try
	if wait_ready(20) then return true
	-- LaunchAgent 不在（没装过或被删掉）：用后台进程直接起一份
	try
		do shell script "cd __REPO__ && { [ -x .venv/bin/python ] && PY=.venv/bin/python || PY=python3; } && nohup $PY server.py --no-open >> server.log 2>&1 &"
	end try
	if wait_ready(20) then return true
	return false
end ensure_server

on run
	if ensure_server() then
		open location "__URL__"
	else
		display dialog "ATV Remote 服务未能启动，请到 __REPO__ 查看 server.log。" buttons {"好"} default button "1" with icon stop with title "ATV Remote"
	end if
end run
APPLESCRIPT

sed -i "" "s|__LABEL__|${LABEL}|g; s|__REPO__|${REPO}|g; s|__URL__|${URL}|g" "$SRC"

rm -rf "$APP"
osacompile -o "$APP" "$SRC"

# 用仓库里的应用图标，别让它顶着一张白纸的 AppleScript 默认图标
# （注意：光拷 icns 不够，见下面 Assets.car 那步）
cp "$REPO/mac/AppIcon.icns" "$APP/Contents/Resources/applet.icns"
/usr/libexec/PlistBuddy -c "Set :CFBundleIconFile applet" "$APP/Contents/Info.plist" 2>/dev/null \
	|| /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string applet" "$APP/Contents/Info.plist"
# osacompile 自带的 Assets.car 里有一副 AppleScript 默认「羊皮纸」图标栈，名字恰好也叫
# applet。Finder 优先读 Assets.car（Info.plist 的 CFBundleIconName=applet），上面拷进去的
# applet.icns 会被它整副盖掉——桌面看到的是羊皮纸，不是遥控器。所以用 AppIcon.icns
# 重编译一副同名（applet）的图标栈把默认 car 盖回去。
ICON_WORK="$(mktemp -d)"
iconutil -c iconset "$REPO/mac/AppIcon.icns" -o "$ICON_WORK/applet.iconset"
mkdir -p "$ICON_WORK/applet.xcassets"
mv "$ICON_WORK/applet.iconset" "$ICON_WORK/applet.xcassets/applet.appiconset"
cat > "$ICON_WORK/applet.xcassets/applet.appiconset/Contents.json" <<'JSON'
{
  "images" : [
    { "filename" : "icon_16x16.png",      "idiom" : "mac", "scale" : "1x", "size" : "16x16" },
    { "filename" : "icon_16x16@2x.png",   "idiom" : "mac", "scale" : "2x", "size" : "16x16" },
    { "filename" : "icon_32x32.png",      "idiom" : "mac", "scale" : "1x", "size" : "32x32" },
    { "filename" : "icon_32x32@2x.png",   "idiom" : "mac", "scale" : "2x", "size" : "32x32" },
    { "filename" : "icon_128x128.png",    "idiom" : "mac", "scale" : "1x", "size" : "128x128" },
    { "filename" : "icon_128x128@2x.png", "idiom" : "mac", "scale" : "2x", "size" : "128x128" },
    { "filename" : "icon_256x256.png",    "idiom" : "mac", "scale" : "1x", "size" : "256x256" },
    { "filename" : "icon_256x256@2x.png", "idiom" : "mac", "scale" : "2x", "size" : "256x256" },
    { "filename" : "icon_512x512.png",    "idiom" : "mac", "scale" : "1x", "size" : "512x512" },
    { "filename" : "icon_512x512@2x.png", "idiom" : "mac", "scale" : "2x", "size" : "512x512" }
  ],
  "info" : { "author" : "xcode", "version" : 1 }
}
JSON
/usr/bin/actool --compile "$APP/Contents/Resources" \
	--output-partial-info-plist "$ICON_WORK/partial.plist" \
	--app-icon applet --platform macosx --minimum-deployment-target 14.0 \
	"$ICON_WORK/applet.xcassets"
# osacompile 生成的 applet 自带 ad-hoc 签名，而上面两步改的都是被封存的资源
# （applet.icns / Info.plist），不改回来 CodeResources 就对不上，Finder 双击时
# 会被 Gatekeeper 判定为「已损坏，无法打开」。所以最后必须重新签一次（ad-hoc 即可）。
codesign --force --sign - --timestamp=none "$APP"
touch "$APP"   # Finder 缓存图标，touch 一下让它重读

echo "✅ 已生成：${APP}"
echo "   - 图标：mac/AppIcon.icns"
echo "   - 服务地址：${URL}（没在跑时会自动拉起 ${LABEL}）"
