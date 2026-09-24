#!/usr/bin/env bash
# 改动后的验证基线：Python 后端导入检查 + 前端语法检查
# 用法: ./check.sh
set -uo pipefail

cd "$(dirname "$0")"
fail=0

run() {
  echo "▶ $*"
  if "$@"; then
    echo "  ✅ 通过"
  else
    echo "  ❌ 失败"
    fail=1
  fi
}

run python3 -c 'import server, atv_backend'
run node --check static/app.js

# HTTP 层加固的行为回归（配对凭据 / Host 头 / keep-alive 帧），只用标准库
run python3 -m unittest discover -s tests

# 内嵌进 Android 原生包的前后端副本是否还跟根目录一致（同步用 ./sync-native.sh）
run ./sync-native.sh --check

# 有 venv 时额外验证 pyatv 路径（Apple TV 分支）也能导入
if [ -x .venv/bin/python ]; then
  run .venv/bin/python -c 'import server, atv_backend'
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "✅ ALL CHECKS PASSED"
else
  echo "❌ 存在失败项"
fi
exit "$fail"
