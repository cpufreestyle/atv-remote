#!/usr/bin/env bash
# 把根目录的 server.py / atv_backend.py / static 同步进 Android 原生包的内嵌副本。
# 那几份是手工拷贝的（Chaquopy 只编 src/main/python/ 里现成的文件），
# 漂移过一次的后果是「新后端 + 旧前端」的 APK 安静地发出去。
# 用法: ./sync-native.sh          同步
#       ./sync-native.sh --check   只检查，不写（等价 check.sh 里的 diff 部分）
set -uo pipefail
cd "$(dirname "$0")"

EMB=android-native/app/src/main/python
check=0
[ "${1:-}" = "--check" ] && check=1
rc=0

copy() {  # copy <src> <dst>
  if [ "$check" -eq 1 ]; then
    diff -q "$1" "$2" || rc=1
  else
    cp -f "$1" "$2" && echo "  synced $1 → $2"
  fi
}

for f in server.py atv_backend.py; do copy "$f" "$EMB/$f"; done
for f in app.js index.html style.css manifest.webmanifest; do copy "static/$f" "$EMB/static/$f"; done

if [ "$check" -eq 1 ]; then
  [ "$rc" -eq 0 ] && echo "✅ 内嵌副本与根目录一致" || echo "❌ 内嵌副本已漂移，执行 ./sync-native.sh"
else
  ./check.sh || rc=1
fi
exit "$rc"
