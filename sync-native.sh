#!/usr/bin/env bash
# 把根目录的前后端同步进 Android 原生包的内嵌副本 android-native/app/src/main/python。
# 那些副本是构建产物（.gitignore 已排除，boot.py 除外）：漂移过一次的后果是
# 「新后端 + 旧前端」的 APK 安静地发出去。
#
# 文件清单不在这里手工维护：问 server.py 自己拿 BUNDLE_PY（server.py 的本地 import
# 闭包），新增 atv_*.py 不用回来补一行；static/ 整个目录照搬，和 Gradle 的
# syncPythonSrc 保持一致。
#
# 用法: ./sync-native.sh           同步，并顺带跑一遍 check.sh
#       ./sync-native.sh --check   只检查，不写（check.sh 用的就是这个）
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
    mkdir -p "$(dirname "$2")" && cp -f "$1" "$2" && echo "  synced $1 → $2"
  fi
}

py_files=$(python3 -c 'import server; print(" ".join(server.BUNDLE_PY))')
if [ -z "$py_files" ]; then
  echo "❌ 读不到 server.py 的本地模块清单（server.py 本身有问题？）"
  exit 1
fi

for f in $py_files; do copy "$f" "$EMB/$f"; done
for f in static/*; do
  if [ -f "$f" ]; then copy "$f" "$EMB/$f"; fi
done

# 反向检查：副本里有、根目录没有的文件。Gradle 的 Copy 只增不删，被删掉的前端资源
# 会一直留在 src/main/python 里跟着进 APK，所以「多出来」同样是漂移。
# 同步模式下顺手清掉；boot.py 是手写的启动器（在 git 里），不参与比对。
if [ -d "$EMB" ]; then
  for f in "$EMB"/*.py "$EMB"/static/*; do
    if [ ! -e "$f" ]; then continue; fi
    rel=${f#$EMB/}
    if [ "$rel" = "boot.py" ]; then continue; fi
    if [ -e "$rel" ]; then continue; fi
    if [ "$check" -eq 1 ]; then
      echo "❌ 内嵌副本多出 $rel（根目录已经没有这个文件）"
    else
      rm -f "$f" && echo "  removed stale $EMB/$rel"
    fi
    rc=1
  done
fi

if [ "$check" -eq 1 ]; then
  [ "$rc" -eq 0 ] && echo "✅ 内嵌副本与根目录一致" || echo "❌ 内嵌副本已漂移，执行 ./sync-native.sh"
else
  ./check.sh || rc=1
fi
exit "$rc"
