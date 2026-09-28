#!/usr/bin/env bash
# 从 1024 全出血 PNG 重建 AppIcon.iconset 并打成 .icns
# （macOS 11+ 全出血规范：不要预套圆角，系统会自己加连续圆角遮罩）
set -euo pipefail
cd "$(dirname "$0")"
SET=AppIcon.iconset
rm -rf "$SET"
mkdir -p "$SET"
gen() {  # gen <目标边长> <输出名>
  sips -z "$1" "$1" AppIcon_1024.png --out "$SET/$2" >/dev/null
}
gen 16    icon_16x16.png
gen 32    icon_16x16@2x.png
gen 32    icon_32x32.png
gen 64    icon_32x32@2x.png
gen 128   icon_128x128.png
gen 256   icon_128x128@2x.png
gen 256   icon_256x256.png
gen 512   icon_256x256@2x.png
gen 512   icon_512x512.png
gen 1024  icon_512x512@2x.png
iconutil -c icns "$SET" -o AppIcon.icns
echo "OK icns rebuilt: $(ls "$SET" | wc -l | tr -d ' ') sizes"
