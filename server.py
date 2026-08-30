#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ATV Remote — Android TV 遥控器（本地 Web UI + adb）
仿 atvremote：方向键 / 按键 / 键盘输入 / 触摸板 / 应用启动 / 截屏

用法:
    python3 server.py [--host 127.0.0.1] [--port 8300] [--adb adb路径] [--no-open]

    # 局域网访问加令牌（不传则行为与以前完全一致：无鉴权）
    python3 server.py --token 你的令牌
"""

import argparse
import hmac
import json
import mimetypes
import os
import re
import select
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parent
STATIC = Path(os.environ.get("ATV_STATIC", str(ROOT / "static")))
STATE_FILE = Path(os.environ.get("ATV_STATE", str(ROOT / "state.json")))

# `adb devices` / `adb version` 的缓存秒数：按键路径每次 fork 进程是延迟大头，
# 遥控器场景可接受 1.5s 的陈旧度（连接/断开等关键操作会主动失效缓存）
DEVICES_CACHE_TTL = 1.5
# 单条命令允许的参数上限，防止构造超长指令（adb 命令行长度也有限制）
MAX_TEXT_LEN = 5000
MAX_KEYCODES = 32

# 允许的设备地址写法：IPv4(:port) 或 主机名(:port)；禁止空格与 shell 元字符
TARGET_RE = re.compile(r"^[A-Za-z0-9.\-]+(:\d+)?$")

# 可选访问令牌：为空表示不鉴权（与历史行为一致）。
# 设置后仅局域网来源需要令牌，本机回环（127.0.0.1 / ::1）直接放行 ——
# 威胁模型就是「同一网段的其它设备」，没必要给本机加门槛。
AUTH_TOKEN = os.environ.get("ATV_TOKEN", "").strip()
TOKEN_COOKIE = "atv_token"
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "::ffff:127.0.0.1")

# 未带令牌时给浏览器看的引导页（表单用 GET，提交后变成 /?token=xxx 自带令牌）
LOGIN_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ATV Remote · 需要访问令牌</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#12141a;
     color:#e7e9ee;font:15px/1.6 -apple-system,"PingFang SC",system-ui,sans-serif}
.box{width:min(92vw,380px);padding:28px;border-radius:14px;background:#1b1e26;
     box-shadow:0 10px 40px rgba(0,0,0,.4)}
h1{margin:0 0 6px;font-size:19px} p{margin:0 0 18px;color:#9aa3b2;font-size:13px}
input{width:100%;padding:11px 12px;border:1px solid #333a49;border-radius:9px;
      background:#12141a;color:#e7e9ee;font-size:15px;box-sizing:border-box}
button{margin-top:10px;width:100%;padding:11px;border:0;border-radius:9px;
       background:#3b7cff;color:#fff;font-size:15px;cursor:pointer}
code{color:#ffd479}
</style></head><body><div class="box">
<h1>🔒 需要访问令牌</h1>
<p>这台服务器开了 <code>--token</code>。令牌在启动它的终端窗口里能找到。</p>
<form method="get" action="/">
  <input name="token" placeholder="输入访问令牌" autocomplete="off" autofocus>
  <button type="submit">进入遥控器</button>
</form></div></body></html>
"""


def token_query() -> str:
    """追加到 URL 上的令牌查询串；未启用令牌时为空串（行为与历史版本一致）"""
    return ("?token=" + quote(AUTH_TOKEN, safe="")) if AUTH_TOKEN else ""

# Android KeyEvent 键码（AOSP keycode.h 子集）
KEYCODES = {
    "home": 3, "back": 4, "menu": 82, "power": 26, "wakeup": 224, "sleep": 223,
    "dpad_up": 19, "dpad_down": 20, "dpad_left": 21, "dpad_right": 22, "dpad_center": 23,
    "vol_up": 24, "vol_down": 25, "mute": 164,
    "play_pause": 85, "stop": 86, "next": 87, "prev": 88, "rewind": 89, "forward": 90,
    "enter": 66, "del": 67, "info": 165, "settings": 176, "app_switch": 187,
    "page_up": 92, "page_down": 93, "escape": 111,
}


class AdbError(Exception):
    pass


def sh_quote(s: str) -> str:
    """给设备端 shell 用的单引号转义"""
    return "'" + s.replace("'", "'\\''") + "'"


def clamp_coord(v) -> int:
    """触摸坐标夹到合理区间，避免超大数值进入 adb 命令行"""
    return min(100000, max(-100000, int(v)))


class Adb:
    """adb 封装：常驻一条 `adb shell` 长连接，按键命令低延迟"""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._shell = None          # type: subprocess.Popen | None
        self._shell_serial = None
        self._seq = 0
        self._dev_lock = threading.Lock()
        self._devices_cache = (0.0, [])   # (时间戳, [{serial, state}])
        self._version_cache = None

    # ---------- 缓存 ----------
    def invalidate_devices(self):
        """连接/断开后调用，强制下次 devices() 重新查询"""
        with self._dev_lock:
            self._devices_cache = (0.0, [])

    def reset_shell(self):
        """丢弃常驻 shell（设备掉线时），下次命令会重新建立"""
        with self._lock:
            self._kill_shell_locked()

    def _kill_shell_locked(self):
        if self._shell is not None:
            try:
                self._shell.kill()
            except Exception:
                pass
            self._shell = None
            self._shell_serial = None

    # ---------- 基础 ----------
    def run(self, *args, timeout=10, binary=False):
        try:
            p = subprocess.run([self.path, *args], capture_output=True, timeout=timeout)
        except FileNotFoundError:
            raise AdbError("未找到 adb，请先安装：brew install android-platform-tools")
        except subprocess.TimeoutExpired:
            raise AdbError("adb {} 超时".format(" ".join(args[:3])))
        if binary:
            return p.stdout
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        return (out or err).strip()

    def exists(self) -> bool:
        return shutil.which(self.path) is not None or Path(self.path).exists()

    def version(self) -> str:
        """adb 版本不会变，只查一次"""
        if self._version_cache is not None:
            return self._version_cache
        try:
            self._version_cache = (self.run("version").splitlines() or [""])[0]
        except AdbError:
            self._version_cache = "unknown"
        return self._version_cache

    def devices(self, fresh: bool = False):
        """返回 [{serial, state}]；默认走 1.5s 缓存，fresh=True 强制重新查询"""
        now = time.time()
        with self._dev_lock:
            ts, cached = self._devices_cache
            if not fresh and cached and now - ts < DEVICES_CACHE_TTL:
                return [dict(d) for d in cached]

        out = self.run("devices")
        result = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                result.append({"serial": parts[0], "state": parts[1]})
        with self._dev_lock:
            self._devices_cache = (now, result)
        return [dict(d) for d in result]

    def connect(self, target: str):
        out = self.run("connect", target, timeout=12)
        if "connected" not in out and "already connected" not in out:
            raise AdbError("无法连接 {}: {}".format(target, out))
        self.invalidate_devices()
        return out

    def disconnect(self, target: str):
        try:
            self.run("disconnect", target, timeout=8)
        except AdbError:
            pass
        self.reset_shell()
        self.invalidate_devices()

    # ---------- 常驻 shell ----------
    def _ensure_shell(self, serial):
        if self._shell is not None and self._shell.poll() is None and self._shell_serial == serial:
            return
        self._kill_shell_locked()
        self._shell = subprocess.Popen(
            [self.path, "-s", serial, "shell"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self._shell_serial = serial

    def shell(self, serial, cmd, timeout=6):
        """在常驻 shell 上执行命令，返回 stdout；退出码非 0 抛 AdbError"""
        with self._lock:
            self._ensure_shell(serial)
            self._seq += 1
            token = "__ATVR{}__".format(self._seq)
            try:
                self._shell.stdin.write("{}; echo {}=$?\n".format(cmd, token).encode())
                self._shell.stdin.flush()
            except (BrokenPipeError, OSError):
                self._kill_shell_locked()
                raise AdbError("adb shell 已断开，请重试")

            buf = b""
            deadline = time.time() + timeout
            while time.time() < deadline:
                rl, _, _ = select.select([self._shell.stdout], [], [], 0.2)
                if not rl:
                    continue
                chunk = os.read(self._shell.stdout.fileno(), 65536)
                if not chunk:
                    self._kill_shell_locked()
                    self.invalidate_devices()
                    raise AdbError("设备连接中断，请重新连接")
                buf += chunk
                text = buf.decode("utf-8", "replace")
                m = re.search(r"^" + token + r"=(\d+)\s*$", text, re.M)
                if m:
                    body = text[:m.start()].replace("\r\n", "\n").strip()
                    if int(m.group(1)) != 0:
                        tail = "; ".join(l for l in body.splitlines()[-2:] if l.strip())
                        raise AdbError(tail or "命令执行失败（退出码 {}）".format(m.group(1)))
                    return body
            self._kill_shell_locked()
            self.invalidate_devices()
            raise AdbError("命令超时：设备可能未授权（请在电视上点“允许”）或已离线")


# ---------------- 全局状态 ----------------
from atv_backend import AppleTvError, AppleTvManager
from atv_backend import PYATV_AVAILABLE as ATV_AVAILABLE

adb = None            # type: Adb
atv_mgr = AppleTvManager()
state = {"recent_android": [], "appletvs": [], "current": None, "info": {}}
# 可重入：save_state 会在已持锁的分支里被调用
state_lock = threading.RLock()


def load_state():
    global state
    try:
        data = json.loads(STATE_FILE.read_text("utf-8"))
        if isinstance(data.get("current"), str):  # 旧版（仅 Android）格式迁移
            data["recent_android"] = data.pop("recent", None) or (
                [data["current"]] if data["current"] else [])
            data["current"] = ({"type": "android", "target": data["current"]}
                               if data["current"] else None)
        state.update({k: data.get(k, state[k]) for k in state})
    except Exception:
        pass


def save_state():
    """原子写盘：先写 .tmp 再 rename，避免写一半崩溃后 state.json（含配对凭据）损坏"""
    try:
        with state_lock:
            snapshot = json.dumps(state, ensure_ascii=False, indent=2)
        tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
        tmp.write_text(snapshot, "utf-8")
        os.replace(str(tmp), str(STATE_FILE))
    except Exception:
        pass


def normalize_target(t: str) -> str:
    t = t.strip()
    if not t:
        raise AdbError("请填写电视 IP")
    # 只允许 IP 或主机名（可带端口），杜绝空格与 shell 元字符进入 adb 命令行
    if not TARGET_RE.match(t):
        raise AdbError("IP / 主机名格式不正确：{}".format(t))
    if ":" not in t:
        t += ":5555"
    return t


def fetch_device_info(serial):
    """连接后取：型号 / 安卓版本 / 屏幕分辨率"""
    info = {}
    try:
        out = adb.shell(serial, "getprop ro.product.brand; getprop ro.product.model; "
                                "getprop ro.build.version.release; wm size", timeout=8)
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        if len(lines) >= 3:
            info["brand"], info["model"], info["android"] = lines[0], lines[1], lines[2]
        for l in lines:
            m = re.search(r"(?:Override|Physical) size:\s*(\d+)x(\d+)", l)
            if m:
                if "Override" in l or "w" not in info:
                    info["w"], info["h"] = int(m.group(1)), int(m.group(2))
    except AdbError as e:
        info["error"] = str(e)
    return info


def make_status():
    with state_lock:
        cur = dict(state.get("current") or {})
        info = dict(state.get("info", {}))
        recent = list(state.get("recent_android", []))
        appletvs = list(state.get("appletvs", []))

    devices = []
    if adb.exists():
        try:
            devices = adb.devices()
        except AdbError:
            devices = []
    ctype = cur.get("type")
    cur_state = None
    if ctype == "android":
        cur_state = next((d["state"] for d in devices if d["serial"] == cur.get("target")), None)
    return {
        "adb_found": adb.exists(),
        "adb_path": adb.path,
        "adb_version": adb.version() if adb.exists() else "",
        "cur_type": ctype,
        "current": cur.get("target") if ctype == "android" else cur.get("id"),
        "current_state": cur_state,
        "info": info,
        "devices": devices,
        "recent": recent,
        "appletv": {
            "available": ATV_AVAILABLE,
            "devices": appletvs,
            "connected": bool(atv_mgr and atv_mgr.connected),
            "kb_focus": (atv_mgr.keyboard_focus() if atv_mgr and atv_mgr.connected else None),
        },
    }


# ---------------- 命令处理 ----------------
def run_shell(serial, cmd, timeout=6):
    """执行命令；设备端偶发挂起（如模拟器 input text）时重试一次"""
    try:
        return adb.shell(serial, cmd, timeout=timeout)
    except AdbError as e:
        if "超时" in str(e):
            return adb.shell(serial, cmd, timeout=timeout)
        raise


def handle_cmd(body):
    """按当前设备类型分发：android → adb，appletv → pyatv"""
    cur = state.get("current") or {}
    if cur.get("type") == "appletv":
        return handle_cmd_appletv(body)
    return handle_cmd_android(body)


def handle_cmd_android(body):
    t = body.get("type")
    with state_lock:
        cur = (state.get("current") or {}).get("target")
    if not cur:
        raise AdbError("未连接电视：请在「Android TV」页输入电视 IP 并连接")
    dev_state = None
    for d in adb.devices():
        if d["serial"] == cur:
            dev_state = d["state"]
    if dev_state != "device":
        adb.reset_shell()
        adb.invalidate_devices()
        raise AdbError("设备 {} 状态异常（{}），请重新连接".format(cur, dev_state or "offline"))

    if t == "key":
        raw = body.get("codes") or ([body["code"]] if "code" in body else None)
        if not raw:
            raise AdbError("缺少键码")
        raw = list(raw)[:MAX_KEYCODES]
        codes = [int(c) for c in raw if str(c).lstrip("-").isdigit()]
        if not codes:
            raise AdbError("键码不合法")
        run_shell(cur, "input keyevent " + " ".join(map(str, codes)))
        return {"ok": True, "sent": codes}

    if t == "text":
        text = str(body.get("text", ""))[:MAX_TEXT_LEN]
        if not text.strip():
            raise AdbError("内容为空")
        bad = sorted({ch for ch in text if ord(ch) > 0x7E})
        if bad:
            raise AdbError("adb input 不支持中文等非 ASCII 字符（{}…），"
                           "中文输入需在电视端安装 ADBKeyboard 输入法".format("".join(bad[:4])))
        run_shell(cur, "input text " + sh_quote(text))
        if body.get("enter"):
            run_shell(cur, "input keyevent 66")
        return {"ok": True}

    if t == "tap":
        x, y = clamp_coord(body["x"]), clamp_coord(body["y"])
        run_shell(cur, "input tap {} {}".format(x, y))
        return {"ok": True}

    if t == "swipe":
        args = [clamp_coord(body[k]) for k in ("x1", "y1", "x2", "y2")]
        dur = min(2000, max(100, int(body.get("duration", 300))))
        run_shell(cur, "input swipe {} {} {} {} {}".format(*args, dur))
        return {"ok": True}

    if t == "app":
        pkg = str(body.get("pkg", "")).strip()
        if not re.match(r"^[A-Za-z][A-Za-z0-9_.]+$", pkg):
            raise AdbError("包名不合法：{}".format(pkg))
        run_shell(cur, "monkey -p {} -c android.intent.category.LAUNCHER 1".format(pkg), timeout=10)
        return {"ok": True}

    if t == "settings":
        run_shell(cur, "am start -a android.settings.SETTINGS")
        return {"ok": True}

    raise AdbError("Android TV 不支持该命令: {}".format(t))


def handle_cmd_appletv(body):
    if not atv_mgr.connected:
        raise AppleTvError("未连接 Apple TV：请在「Apple TV」页连接")
    t = body.get("type")
    if t == "key":
        codes = body.get("codes") or ([body["code"]] if "code" in body else None)
        if not codes:
            raise AppleTvError("缺少键码")
        atv_mgr.send_keys(codes)
        return {"ok": True, "sent": codes}
    if t == "text":
        atv_mgr.send_text(body.get("text", ""), enter=body.get("enter"))
        return {"ok": True}
    if t == "tap":
        atv_mgr.tap()  # Apple TV 无坐标点击，等价轻点
        return {"ok": True}
    if t == "swipe":
        # 前端传归一化坐标 0.0~1.0
        atv_mgr.swipe(body["x1"], body["y1"], body["x2"], body["y2"],
                      body.get("duration", 300))
        return {"ok": True}
    if t == "app":
        pkg = str(body.get("pkg", "")).strip()
        if not pkg:
            raise AppleTvError("缺少应用标识")
        atv_mgr.launch_app(pkg)
        return {"ok": True}
    raise AppleTvError("Apple TV 不支持该命令: {}".format(t))


# ---------------- Apple TV 接口 ----------------
def handle_atv_scan(body):
    hosts = [body["ip"]] if body.get("ip") else None
    found = atv_mgr.scan(hosts)
    with state_lock:
        known = {d["id"] for d in state["appletvs"]}
    for f in found:
        f["paired"] = f["id"] in known
    return {"devices": found}


def handle_atv_pair(body):
    action = body.get("action")
    dev = {k: body[k] for k in ("id", "name", "ip", "proto", "mrp_port",
                                "companion_port", "airplay_port") if body.get(k)}
    if not dev.get("id") or not dev.get("ip"):
        raise AppleTvError("缺少设备信息（id/ip）")
    if action == "begin":
        atv_mgr.pair_begin(dev)
        return {"ok": True, "hint": "电视屏幕上应已显示 4 位 PIN 码，请填入后点「完成配对」"}
    if action == "finish":
        entry = atv_mgr.pair_finish(str(body.get("pin", "")))
        with state_lock:
            state["appletvs"] = [d for d in state["appletvs"] if d["id"] != entry["id"]]
            state["appletvs"].append(entry)
            save_state()
        atv_mgr.pair_stop()
        return {"ok": True, "device": entry}
    if action == "stop":
        atv_mgr.pair_stop()
        return {"ok": True}
    raise AppleTvError("未知配对动作: {}".format(action))


def handle_atv_connect(body):
    with state_lock:
        entry = next((d for d in state["appletvs"] if d.get("id") == body.get("id")), None)
    if entry is None:  # 未保存过的扫描结果（未配对会在 connect 内提示）
        entry = {k: body[k] for k in ("id", "name", "ip") if k in body}
    atv_mgr.connect(entry)
    with state_lock:
        state["current"] = {"type": "appletv", "id": entry["id"]}
        state["info"] = {"brand": "Apple", "model": entry.get("name") or "Apple TV"}
        save_state()
    return {"ok": True, "device": atv_mgr.current_device()}


def handle_atv_disconnect(_body):
    atv_mgr.disconnect()
    with state_lock:
        if (state.get("current") or {}).get("type") == "appletv":
            state["current"] = None
            state["info"] = {}
            save_state()
    return {"ok": True}


def handle_atv_forget(body):
    with state_lock:
        state["appletvs"] = [d for d in state["appletvs"] if d.get("id") != body.get("id")]
        if (state.get("current") or {}).get("id") == body.get("id"):
            state["current"] = None
            state["info"] = {}
        save_state()
    return {"ok": True}


def handle_atv_apps(_body):
    return {"apps": atv_mgr.apps()}


def handle_connect(body):
    target = normalize_target(str(body.get("target", "")))
    adb.connect(target)
    time.sleep(0.3)
    st = None
    for d in adb.devices(fresh=True):
        if d["serial"] == target:
            st = d["state"]
    if st is None:
        raise AdbError("adb 已连接但设备列表中未出现，请确认电视 IP 正确")
    info = fetch_device_info(target) if st == "device" else {"error": "设备未授权，请在电视上点“允许 USB 调试”"}
    with state_lock:
        state["current"] = {"type": "android", "target": target}
        state["info"] = info
        if target not in state["recent_android"]:
            state["recent_android"] = ([target] + state["recent_android"])[:5]
        save_state()
    return {"ok": True, "target": target, "state": st, "info": info,
            "warning": "" if st == "device" else "设备未授权，请在电视屏幕上确认“允许 USB 调试”"}


def handle_disconnect(_body):
    with state_lock:
        cur = dict(state.get("current") or {})
    target = cur.get("target") if cur.get("type") == "android" else None
    if target:
        adb.disconnect(target)  # 涉及子进程调用，不要持锁执行
    with state_lock:
        state["current"] = None
        state["info"] = {}
        save_state()
    return {"ok": True}


def handle_forget(body):
    target = str(body.get("target", ""))
    with state_lock:
        state["recent_android"] = [r for r in state["recent_android"] if r != target]
        if (state.get("current") or {}).get("target") == target:
            state["current"] = None
            state["info"] = {}
        save_state()
    return {"ok": True}


def handle_switch(body):
    """切换到 adb devices 里已有的设备"""
    target = str(body.get("target", ""))
    devices = {d["serial"]: d["state"] for d in adb.devices(fresh=True)}
    if target not in devices:
        raise AdbError("设备不在线: {}".format(target))
    info = fetch_device_info(target) if devices[target] == "device" else {}
    with state_lock:
        state["current"] = {"type": "android", "target": target}
        state["info"] = info
        if target not in state["recent_android"]:
            state["recent_android"] = ([target] + state["recent_android"])[:5]
        save_state()
    return {"ok": True, "info": info}


# ---------------- 分发：手机一键安装 ----------------
INSTALL_SCRIPT = r"""#!/data/data/com.termux/files/usr/bin/bash
# ATV Remote 手机引擎一键安装（在 Termux 里: curl -sL __HOST__/install | bash）
set -e
echo "📱 ATV Remote 引擎安装中（3-8 分钟，仅需这一次）..."
pkg update -y >/dev/null 2>&1 || true
pkg install -y python clang libffi openssl android-tools
# 预编译 cryptography（避免本地编 rust）
pkg install -y tur-repo >/dev/null 2>&1 && pkg install -y python-cryptography || true
pip install --upgrade pip wheel >/dev/null
pip install pyatv || echo "⚠️ pyatv 安装失败（Apple TV 暂不可用，Android TV 正常），可稍后重试本命令"

# 拉取项目（含 start.sh；若 Mac 上有配对记录会一并同步）
curl -sL __HOST__/bundle.tgz__TOKENQ__ -o /data/data/com.termux/files/usr/tmp/atv.tgz
tar xzf /data/data/com.termux/files/usr/tmp/atv.tgz -C "$HOME"
rm -f /data/data/com.termux/files/usr/tmp/atv.tgz
chmod +x "$HOME"/atv-remote/start.sh

# 允许 ATVRemote App 一键拉起
mkdir -p "$HOME/.termux"
grep -q allow-external-apps "$HOME/.termux/termux.properties" 2>/dev/null || \
  echo "allow-external-apps=true" >> "$HOME/.termux/termux.properties"

echo ""
echo "✅ 完成！打开 ATVRemote App 点「🚀 独立模式」即可遥控电视"
"""


def bundle_signature():
    """打包内容的文件签名（路径 + mtime + 大小），用于判断缓存是否过期"""
    sig = []
    for name in ("server.py", "atv_backend.py", "start.sh", "state.json"):
        p = ROOT / name
        if p.is_file():
            st = p.stat()
            sig.append((name, st.st_mtime_ns, st.st_size))
    static_dir = ROOT / "static"
    for p in sorted(static_dir.rglob("*")):
        if p.is_file():
            st = p.stat()
            sig.append((p.relative_to(ROOT).as_posix(), st.st_mtime_ns, st.st_size))
    return tuple(sig)


_bundle_cache = {"sig": None, "data": b""}
_bundle_lock = threading.Lock()


def cached_bundle() -> bytes:
    """带缓存的打包：手机反复拉取 bundle.tgz 时不必每次重新压缩"""
    sig = bundle_signature()
    with _bundle_lock:
        if _bundle_cache["sig"] == sig and _bundle_cache["data"]:
            return _bundle_cache["data"]
        data = build_bundle()
        _bundle_cache["sig"] = sig
        _bundle_cache["data"] = data
        return data


def build_bundle() -> bytes:
    """打包 Termux 引擎需要的文件（含 Mac 上的配对记录）"""
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in ("server.py", "atv_backend.py", "start.sh", "state.json"):
            p = ROOT / name
            if p.is_file():
                tar.add(str(p), arcname="atv-remote/" + name)
        static_dir = ROOT / "static"
        for p in static_dir.rglob("*"):
            if p.is_file():
                tar.add(str(p), arcname="atv-remote/static/" + p.relative_to(static_dir).as_posix())
    return buf.getvalue()


# ---------------- HTTP ----------------
# 模块级路由表：不要每次 POST 都重建
ROUTES = {
    "/api/connect": handle_connect,
    "/api/disconnect": handle_disconnect,
    "/api/cmd": handle_cmd,
    "/api/forget": handle_forget,
    "/api/switch": handle_switch,
    "/api/atv/scan": handle_atv_scan,
    "/api/atv/pair": handle_atv_pair,
    "/api/atv/connect": handle_atv_connect,
    "/api/atv/disconnect": handle_atv_disconnect,
    "/api/atv/forget": handle_atv_forget,
    "/api/atv/apps": handle_atv_apps,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ATVRemote/1.0"
    protocol_version = "HTTP/1.1"  # 长连接：连按方向键不必每次重开 TCP
    timeout = 15                   # 慢客户端兜底，避免长期占用工作线程

    def log_message(self, fmt, *args):
        pass  # 静默访问日志

    # ---- 可选令牌鉴权 ----
    def _from_loopback(self) -> bool:
        return (self.client_address[0] if self.client_address else "") in LOOPBACK_HOSTS

    def _provided_token(self):
        """令牌来源优先级：X-ATV-Token 头 > ?token= 查询串 > Cookie"""
        h = self.headers.get("X-ATV-Token")
        if h:
            return h.strip()
        q = parse_qs(urlparse(self.path).query).get(TOKEN_COOKIE) or \
            parse_qs(urlparse(self.path).query).get("token")
        if q:
            return (q[0] or "").strip()
        raw = self.headers.get("Cookie")
        if raw:
            jar = SimpleCookie()
            try:
                jar.load(raw)
            except Exception:
                return None
            morsel = jar.get(TOKEN_COOKIE)
            if morsel:
                return morsel.value
        return None

    def _authorized(self) -> bool:
        if not AUTH_TOKEN:
            return True
        if self._from_loopback():
            return True                      # 本机访问免令牌
        provided = self._provided_token()
        return bool(provided) and hmac.compare_digest(provided, AUTH_TOKEN)

    def _unauthorized(self):
        if self.path.split("?")[0] in ("/", "/index.html"):
            return self._send(401, LOGIN_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        return self._send(401, {"error": "需要访问令牌：请带 X-ATV-Token 头，"
                                         "或在地址后加 ?token=<令牌>（令牌见启动终端）"})

    def _check_auth(self) -> bool:
        """每个请求开头调用；不通过时已自行返回 401"""
        self._issue_cookie = False
        if not self._authorized():
            self._unauthorized()
            return False
        if AUTH_TOKEN and not self._from_loopback():
            self._issue_cookie = True
        return True

    def _send(self, code, data, ctype="application/json; charset=utf-8"):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # 令牌校验通过后种 cookie，后续请求（fetch / 静态资源 / APK）就不用再拼 ?token=
        if AUTH_TOKEN and not self._from_loopback() and getattr(self, "_issue_cookie", False):
            self.send_header("Set-Cookie", "{}={}; Path=/; SameSite=Lax".format(
                TOKEN_COOKIE, AUTH_TOKEN))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > 100_000:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8", "replace") or "{}")

    # ---- GET ----
    def do_GET(self):
        path = self.path.split("?")[0]
        if not self._check_auth():
            return
        try:
            if path in ("/", "/index.html"):
                html = (STATIC / "index.html").read_bytes()
                # 把令牌注入页面：前端要拼进安装命令与 API 请求
                html = html.replace(b"__ATV_TOKEN__", AUTH_TOKEN.encode())
                return self._send(200, html, "text/html; charset=utf-8")
            if path == "/api/status":
                return self._send(200, make_status())
            if path == "/api/screenshot":
                with state_lock:
                    cur = dict(state.get("current") or {})
                if not cur:
                    return self._send(400, {"error": "未连接电视"})
                if cur.get("type") == "appletv":
                    data, mime = atv_mgr.artwork()
                    return self._send(200, data, mime)
                png = adb.run("-s", cur["target"], "exec-out", "screencap", "-p", timeout=15, binary=True)
                if not png.startswith(b"\x89PNG"):
                    return self._send(500, {"error": "截屏失败：设备可能未授权或已锁屏"})
                return self._send(200, png, "image/png")
            if path == "/install":
                host = self.headers.get("Host") or "127.0.0.1:8300"
                script = (INSTALL_SCRIPT
                          .replace("__HOST__", "http://" + host)
                          .replace("__TOKENQ__", token_query()))
                return self._send(200, script.encode(), "text/plain; charset=utf-8")
            if path == "/bundle.tgz":
                return self._send(200, cached_bundle(), "application/gzip")
            if path == "/app.apk":
                apk = ROOT / "android" / "ATVRemote.apk"
                if not apk.is_file():
                    return self._send(404, {"error": "APK 不存在，请先在电脑上执行 android/build.sh"})
                return self._send(200, apk.read_bytes(),
                                  "application/vnd.android.package-archive")
            if path == "/api/qr.svg":
                text = (parse_qs(urlparse(self.path).query).get("text") or [""])[0][:512]
                if not text:
                    return self._send(400, {"error": "缺少 text 参数"})
                try:
                    import io
                    import qrcode
                    import qrcode.image.svg
                except ImportError:
                    return self._send(503, {"error": "未安装 qrcode：.venv/bin/pip install qrcode"})
                img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=2)
                buf = io.BytesIO()
                img.save(buf)
                return self._send(200, buf.getvalue(), "image/svg+xml")
            if path.startswith("/static/"):
                root = STATIC.resolve()
                f = (STATIC / path[len("/static/"):]).resolve()
                # 目录前缀比较必须补分隔符，否则 /a/static 会放行 /a/static-secret/x
                if not (str(f) + os.sep).startswith(str(root) + os.sep) or not f.is_file():
                    return self._send(404, {"error": "not found"})
                ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                return self._send(200, f.read_bytes(), ctype)
            return self._send(404, {"error": "not found"})
        except (AdbError, AppleTvError) as e:
            return self._send(400, {"error": str(e)})
        except BrokenPipeError:
            pass
        except Exception as e:
            return self._send(500, {"error": "内部错误: {}".format(e)})

    # ---- POST ----
    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._check_auth():
            return
        fn = ROUTES.get(path)
        if not fn:
            return self._send(404, {"error": "not found"})
        try:
            body = self._body()
            return self._send(200, fn(body) or {"ok": True})
        except json.JSONDecodeError:
            return self._send(400, {"error": "请求体不是合法 JSON"})
        except (AdbError, AppleTvError) as e:
            return self._send(400, {"error": str(e)})
        except BrokenPipeError:
            pass
        except Exception as e:
            return self._send(500, {"error": "内部错误: {}".format(e)})


def resolve_adb(path: str) -> str:
    """LaunchAgent 等精简 PATH 环境下自动探测 adb 常见位置"""
    if path != "adb":
        return path
    found = shutil.which("adb")
    if found:
        return found
    for p in ("/opt/homebrew/bin/adb", "/usr/local/bin/adb",
              str(Path.home() / "Library/Android/sdk/platform-tools/adb")):
        if Path(p).is_file():
            return p
    return "adb"


def lan_ip() -> str:
    """取本机局域网 IP（不真正发包，仅用于显示）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "本机局域网IP"


def main():
    global adb, AUTH_TOKEN
    ap = argparse.ArgumentParser(description="ATV Remote — Android TV / Apple TV 遥控器")
    ap.add_argument("--host", default="0.0.0.0",
                    help="默认 0.0.0.0（手机可直接访问）；只允许本机则传 127.0.0.1")
    ap.add_argument("--port", type=int, default=8300)
    ap.add_argument("--adb", default=os.environ.get("ADB_PATH", "adb"), help="adb 可执行文件路径")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--token", default=AUTH_TOKEN,
                    help="局域网访问令牌；不传则不鉴权（默认）。本机 127.0.0.1 访问始终免令牌")
    args = ap.parse_args()
    AUTH_TOKEN = (args.token or "").strip()

    adb = Adb(resolve_adb(args.adb))
    load_state()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True

    url = "http://{}:{}".format("127.0.0.1" if args.host == "0.0.0.0" else args.host, args.port)
    print("=" * 46)
    print("  📺 ATV Remote — Android TV / Apple TV 遥控器")
    print("  网页地址 : {}".format(url))
    if adb.exists():
        print("  adb      : {} ({})".format(adb.path, adb.version()))
    else:
        print("  adb      : ⚠️ 未安装！请执行 brew install android-platform-tools")
    print("  Apple TV : {}".format("pyatv 已就绪" if ATV_AVAILABLE else
                                   "⚠️ pyatv 未加载（用 .venv/bin/python 启动可启用）"))
    if args.host == "0.0.0.0":
        lan = "http://{}:{}{}".format(lan_ip(), args.port, token_query())
        print("  手机访问 : {}  （App 或浏览器直接打开）".format(lan))
        if AUTH_TOKEN:
            print("  🔒 已启用令牌，局域网设备需带 ?token= 或用页面上的表单登录")
    print("  Ctrl+C 停止")
    print("=" * 46)

    cur = state.get("current") or {}
    if cur.get("type") == "android" and cur.get("target"):
        print("  恢复连接 : {} (Android TV)".format(cur["target"]))
        try:
            adb.connect(cur["target"])
        except AdbError as e:
            print("  恢复失败 : {}".format(e))
    elif cur.get("type") == "appletv":
        entry = next((d for d in state.get("appletvs", []) if d.get("id") == cur.get("id")), None)
        if entry:
            print("  恢复连接 : {} (Apple TV)".format(entry.get("name") or entry.get("ip")))
            try:
                atv_mgr.connect(entry)
            except AppleTvError as e:
                print("  恢复失败 : {}".format(e))

    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n再见！")
        httpd.server_close()


if __name__ == "__main__":
    main()
