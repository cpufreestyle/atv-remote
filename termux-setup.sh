#!/data/data/com.termux/files/usr/bin/bash
# ATV Remote — 手机独立运行一键配置（在 Termux 里执行一次）
# 用法：
#   1. Termux 里:  termux-setup-storage   （允许访问手机存储）
#   2. 把整个 atv-remote 目录放到手机（如 Download/atv-remote），然后:
#      bash /data/data/com.termux/files/home/storage/downloads/atv-remote/termux-setup.sh
set -e

echo "== [1/6] 安装基础工具（python / adb / 编译链）=="
pkg update -y
pkg install -y python clang libffi openssl android-tools

echo "== [2/6] 安装预编译 cryptography（避免本地编译 rust）=="
pkg install -y tur-repo
pkg install -y python-cryptography || pip install cryptography

echo "== [3/6] 安装 pyatv 及依赖 =="
pip install --upgrade pip wheel
pip install pyatv

echo "== [4/6] 复制项目到 Termux 主目录 =="
SRC="$(cd "$(dirname "$0")" && pwd)"
DST="$HOME/atv-remote"
mkdir -p "$DST"
cp -r "$SRC"/server.py "$SRC"/atv_backend.py "$SRC"/static "$DST"/
# 复用 Mac 上已配对的凭据（可选，存在才复制）
[ -f "$SRC/state.json" ] && cp "$SRC/state.json" "$DST"/ && echo "  已导入 Mac 上的配对记录"

echo "== [5/6] 允许 App 一键拉起（RUN_COMMAND）+ 创建启动脚本 =="
mkdir -p "$HOME/.termux"
if ! grep -q "allow-external-apps" "$HOME/.termux/termux.properties" 2>/dev/null; then
  echo "allow-external-apps=true" >> "$HOME/.termux/termux.properties"
fi

cat > "$DST/start.sh" << 'EOF'
#!/data/data/com.termux/files/usr/bin/bash
cd "$(dirname "$0")"
# 后台被 App 拉起时无界面，日志写文件方便排错
exec >> "$HOME/atv-remote/server.log" 2>&1
echo "---- $(date) ----"
# termux-wake-lock: 防止手机息屏杀进程
command -v termux-wake-lock >/dev/null && termux-wake-lock
ADB_BIN="$(command -v adb || echo /data/data/com.termux/files/usr/bin/adb)"
echo "服务启动: 127.0.0.1:8300 (adb=$ADB_BIN)"
python server.py --host 127.0.0.1 --adb "$ADB_BIN" --no-open
EOF
chmod +x "$DST/start.sh"

echo "== [6/6] 完成 ✅ =="
echo ""
echo "以后每次使用：直接打开 ATVRemote App 点「🚀 独立模式」即可"
echo "（也可 Termux 里手动: ~/atv-remote/start.sh，日志在 ~/atv-remote/server.log）"
