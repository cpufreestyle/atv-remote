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
import base64
import hmac
import json
import mimetypes
import os
import platform
import re
import select
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from collections import deque
from html import escape as html_escape
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

ROOT = Path(__file__).resolve().parent
STATIC = Path(os.environ.get("ATV_STATIC", str(ROOT / "static")))
STATE_FILE = Path(os.environ.get("ATV_STATE", str(ROOT / "state.json")))

# .webmanifest 等新式后缀在部分平台（Android / Chaquopy 机没有 /etc/mime.types）
# 会被 mimetypes 降级成 application/octet-stream，PWA 直接装不上；显式映射兜底
_STATIC_CTYPE = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".webmanifest": "application/manifest+json",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def static_ctype(path: Path) -> str:
    """静态文件 Content-Type：显式映射优先，回落系统表，再回落八位组流"""
    return (_STATIC_CTYPE.get(path.suffix.lower())
            or mimetypes.guess_type(str(path))[0]
            or "application/octet-stream")

# `adb devices` / `adb version` 的缓存秒数：按键路径每次 fork 进程是延迟大头，
# 遥控器场景可接受 1.5s 的陈旧度（连接/断开等关键操作会主动失效缓存）
DEVICES_CACHE_TTL = 1.5

# 服务是否跑在手机 App 的 Chaquopy 引擎里（boot.py 设的）。页面据此隐藏
# 「把遥控器装到手机」卡片：手机上已经装好了，而且 APK 文件不在包里，
# 那张卡片的下载链接只会拿到 404「APK 不存在」。
EMBEDDED = os.environ.get("ATV_EMBEDDED") == "1"
# 单条命令允许的参数上限，防止构造超长指令（adb 命令行长度也有限制）
MAX_TEXT_LEN = 5000
MAX_KEYCODES = 32
# 应用标识白名单：Android 包名与 Apple TV bundle id 共用，首字符必须是字母
APP_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]+$")

# 允许的设备地址写法：IPv4(:port) 或 主机名(:port)；禁止空格与 shell 元字符
TARGET_RE = re.compile(r"^[A-Za-z0-9.\-]+(:\d+)?$")
# /install 里的 Host 头：同上，另外放行 [IPv6] 字面量写法
HOST_RE = re.compile(r"^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.\-]+)(:\d{1,5})?$")

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
<p>这台服务器开启了访问令牌。令牌在启动它的终端窗口里（重启不变），
电脑上打开本页面 →「📱 装到手机」里有带令牌的二维码，扫一次即可。</p>
<form method="get" action="/">
  <input name="token" placeholder="输入访问令牌" autocomplete="off" autofocus>
  <button type="submit">进入遥控器</button>
</form></div></body></html>
"""


LOOPBACK_ONLY_HOSTS = ("127.0.0.1", "::1", "localhost")


def resolve_token(explicit, host: str, no_token: bool, st, env_token="") -> str:
    """决定本次运行的访问令牌（单一决策点，main() 与测试都走这里）：
    - 显式 --token：以它为准（空串 = 明确不鉴权，保持历史行为；None = 没传）
    - 环境变量 ATV_TOKEN：等效显式传入
    - 只绑回环，或 --no-token：不鉴权
    - 绑 0.0.0.0 / :: 且什么都没指定：首启自动生成一个并写进 state.json
      （重启不变 → 手机只需首次输入或扫一次码；要退回无鉴权用 --no-token）
    """
    if explicit is not None:
        return explicit.strip()
    env = (env_token or "").strip()
    if env:
        return env
    if no_token or host in LOOPBACK_ONLY_HOSTS:
        return ""
    tok = str((st or {}).get("token") or "").strip() if isinstance(st, dict) else ""
    if tok:
        return tok
    tok = secrets.token_urlsafe(9)     # 12 字符，URL 安全，碰不中
    if isinstance(st, dict):
        st["token"] = tok
    return tok


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

# ---- ADBKeyboard：Android TV 上输入中文 / Emoji 的唯一可行方案 ----
# adb 的 `input text` 只吃 ASCII（非 ASCII 会静默丢弃），中文必须借道第三方输入法：
# ADBKeyboard 监听广播 intent，把文本交给「当前输入法」的 InputConnection 提交，
# 所以只有当它是电视的当前输入法时文本才会落到输入框里。
ADBKB_IME = "com.android.adbkeyboard/.AdbIME"
# Android 16 必须用 v2.5-dev（旧版装不上）；其它版本用 v2.4-dev
ADBKB_APK_A16 = ("https://github.com/senzhk/ADBKeyBoard/releases/download/"
                 "v2.5-dev/keyboardservice-debug.apk")
ADBKB_APK = ("https://github.com/senzhk/ADBKeyBoard/releases/download/"
             "v2.4-dev/keyboardservice-debug.apk")
# EditorInfo.imeOptions 动作码：搜索框里点「搜索」比发回车更准
ADBKB_ACTIONS = {"go": 2, "search": 3, "send": 4, "next": 5, "done": 6, "previous": 7}


class AdbError(Exception):
    pass


def sh_quote(s: str) -> str:
    """给设备端 shell 用的单引号转义"""
    return "'" + s.replace("'", "'\\''") + "'"


def clamp_coord(v) -> int:
    """触摸坐标夹到合理区间，避免超大数值进入 adb 命令行"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        # 畸形 body 的经典去处：缺字段 / 坏值在这一层挡住报 400，
        # 否则 int() 的异常会一路逃到 do_POST 兜底，回一个没有信息量的 500
        raise AdbError("坐标不合法：{!r}".format(v))
    return min(100000, max(-100000, n))


def coord_of(body, key) -> int:
    """从请求体取一个坐标：缺字段与坏值都立刻 400，别让 body[key] 炸成 KeyError"""
    if body.get(key) is None:
        raise AdbError("缺少坐标参数 {}".format(key))
    return clamp_coord(body[key])


def norm_coord(body, key) -> float:
    """Apple TV 触控的归一化坐标 0.0~1.0：缺字段 / 坏值提前报 400，
    越界照旧夹到边界（与 atv_backend 内部钳制一致，不新增拒绝路径）"""
    v = body.get(key)
    if v is None:
        raise AppleTvError("缺少坐标参数 {}".format(key))
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise AppleTvError("坐标不合法：{!r}".format(v))
    return min(1.0, max(0.0, f))


def clamp_duration(v, default=300) -> int:
    """手势时长（ms）：缺省用 default，坏值报 400（以前 int() 会炸成 500）"""
    if v is None:
        return default
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise AdbError("手势时长不合法：{!r}".format(v))
    return min(2000, max(100, n))


# ---------------- 调用时间线（perf） ----------------
# 学 Chrome DevTools 的 Network 面板与 Android Studio App Inspection：把每条昂贵的外部调用
# （fork 一个 adb 进程、常驻 shell 上的一条命令、pyatv 一次协议调用、devices 缓存命中）
# 当一等事件记下来——耗时多少、有没有命中缓存、失败原因是什么。在此之前「按下去有延迟」
# 只能猜：不知道慢在哪一步、慢多少、是不是每次都在重新 fork adb。环形缓冲固定长度、
# 只在被问到时才给快照，正常链路零额外开销。
PERF_MAX = 60                 # 环形缓冲长度：再多前端也看不完
PERF_WINDOW_MS = 30000        # 瀑布窗口上限：离得再久的旧事件也不参与画布
PERF_ERR_LEN = 120
PERF_KINDS = ("adb", "shell", "pyatv", "devices")
_perf_lock = threading.Lock()
_perf_seq = 0
_perf_events = deque(maxlen=PERF_MAX)   # 尾部是最新


def perf_record(kind, label, ms, cache=False, err=""):
    """记一条调用。label 截到 80 字符（adb 命令行能很长），err 截到 PERF_ERR_LEN。"""
    global _perf_seq
    now = time.time()
    with _perf_lock:
        _perf_seq += 1
        _perf_events.append({
            "seq": _perf_seq,
            "kind": kind if kind in PERF_KINDS else "adb",
            "label": str(label)[:80],
            "ms": max(0, int(ms)),
            "cache": bool(cache),
            "err": str(err)[:PERF_ERR_LEN],
            "t": now,
        })


def _perf_pct(sorted_vals, p):
    """百分位（线性插值）；样本只有一个时直接给它，别把 p50 报得比 max 还小。"""
    if not sorted_vals:
        return 0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def perf_snapshot():
    """快照：每条事件带 ago（距今多少毫秒前结束），前端据此画瀑布。

    用「多久以前」而不是绝对时间戳，是为了让前端完全不依赖时钟对齐：画布右端永远
    是「现在」，横条长度就是耗时。窗口上限 PERF_WINDOW_MS，超出去的旧事件不计入
    （否则用户离开半小时再回来，最近的几条会被挤成一根针）。
    """
    now = time.time()
    with _perf_lock:
        evs = [dict(e) for e in _perf_events]
    kept, hidden = [], 0
    for e in evs:
        ago = max(0, int((now - e["t"]) * 1000))
        e.pop("t", None)
        if ago > PERF_WINDOW_MS:
            hidden += 1
            continue
        e["ago"] = ago
        kept.append(e)
    mss = sorted(e["ms"] for e in kept)
    hits = sum(1 for e in kept if e["cache"])
    oldest = max((e["ago"] for e in kept), default=0)
    return {
        "events": kept,
        "window_ms": max(2000, oldest + 1000),
        "stats": {
            "n": len(kept),
            "p50": round(_perf_pct(mss, 0.5), 1),
            "p95": round(_perf_pct(mss, 0.95), 1),
            "max": mss[-1] if mss else 0,
            "errs": sum(1 for e in kept if e["err"]),
            "cache": hits,
            "cache_rate": round(hits / len(kept), 3) if kept else 0.0,
            "hidden": hidden,
        },
    }


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
        perf_record("adb", "invalidate_devices", 0.0)
        with self._dev_lock:
            self._devices_cache = (0.0, [])

    def reset_shell(self):
        """丢弃常驻 shell（设备掉线时），下次命令会重新建立"""
        perf_record("adb", "reset_shell", 0.0)
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
        t0 = time.monotonic()
        label = " ".join(str(a) for a in args[:3])
        try:
            p = subprocess.run([self.path, *args], capture_output=True, timeout=timeout)
        except FileNotFoundError as e:
            perf_record("adb", label, (time.monotonic() - t0) * 1000, err=str(e))
            raise AdbError("未找到 adb，请先安装：brew install android-platform-tools")
        except subprocess.TimeoutExpired as e:
            perf_record("adb", label, (time.monotonic() - t0) * 1000, err=str(e))
            raise AdbError("adb {} 超时".format(" ".join(args[:3])))
        if binary:
            perf_record("adb", label, (time.monotonic() - t0) * 1000)
            return p.stdout
        out = p.stdout.decode("utf-8", "replace")
        err = p.stderr.decode("utf-8", "replace")
        perf_record("adb", label, (time.monotonic() - t0) * 1000)
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
                perf_record("devices", "devices", 0.0, cache=True)
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
        t0 = time.monotonic()
        label = cmd[:60]
        with self._lock:
            self._ensure_shell(serial)
            self._seq += 1
            token = "__ATVR{}__".format(self._seq)
            try:
                self._shell.stdin.write("{}; echo {}=$?\n".format(cmd, token).encode())
                self._shell.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                perf_record("shell", label, (time.monotonic() - t0) * 1000, err=str(e))
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
                    perf_record("shell", label, (time.monotonic() - t0) * 1000, err="设备连接中断")
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
                        msg = tail or "命令执行失败（退出码 {}）".format(m.group(1))
                        perf_record("shell", label, (time.monotonic() - t0) * 1000, err=msg)
                        raise AdbError(msg)
                    perf_record("shell", label, (time.monotonic() - t0) * 1000)
                    return body
            self._kill_shell_locked()
            self.invalidate_devices()
            perf_record("shell", label, (time.monotonic() - t0) * 1000, err="命令超时")
            raise AdbError("命令超时：设备可能未授权（请在电视上点“允许”）或已离线")


# ---------------- 全局状态 ----------------
from atv_backend import AppleTvError, AppleTvManager
from atv_backend import PYATV_AVAILABLE as ATV_AVAILABLE
import atv_backend
atv_backend.set_perf_recorder(perf_record)

adb = None            # type: Adb
atv_mgr = AppleTvManager()
state = {"recent_android": [], "appletvs": [], "current": None, "info": {},
         "token": ""}   # token：首启自动生成的访问令牌，落盘才跨重启稳定
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


# ---------------- 正在播放（Now Playing） ----------------
# 学习源：Google TV 官方遥控 / Kodi Remote —— 遥控器顶部常驻「正在播放」信息条，
# 用户不用切回电视画面就知道片名和进度。数据源是 dumpsys media_session：唯一
# 不依赖 App 主动配合的系统级接口。
# 各家 ROM 的 dump 格式有差异（state=PLAYING / state=3 / PlaybackState {state=2…}、
# pos= 与 position= 混用、duration 单位为 ms），所以解析器刻意宽松：认不出的字段
# 就缺省，前端据此隐藏卡片，绝不因为格式变化把整个遥控器搞挂。
MEDIA_STATE_NAMES = {
    0: "none", 1: "stopped", 2: "paused", 3: "playing",
    4: "fast_forwarding", 5: "rewinding", 6: "buffering",
    7: "error", 8: "connecting", 9: "skipping_prev", 10: "skipping_next",
}

NOWPLAYING_TTL = 3.5     # dumpsys media_session 不便宜，3.5s 内复用同一结果
_nowplaying_cache = {"ts": 0.0, "serial": None, "val": None}


def parse_media_session(text):
    """从 dumpsys media_session 输出抠「正在播放」。字段取不到就缺省（≠ 出错）：
    app / title / artist / duration(ms) / position(ms) / playing / empty"""
    t = text or ""
    r = {"playing": False, "title": "", "artist": "", "app": "",
         "duration": 0, "position": 0, "empty": True}

    def first(pat):
        m = re.search(pat, t, re.IGNORECASE | re.MULTILINE)
        return m.group(1).strip() if m else ""

    # 会话栈是按活跃度排序的，取首个记录 = 当前前台会话；每个字段也只取首个命中
    r["app"] = (first(r"^\s*packageName\s*=\s*(\S+)")
                or first(r"^\s*pkg\s*=\s*(\S+)"))
    r["title"] = (first(r"^\s*title\s*=\s*(.+)$")
                  or first(r"^\s*displayTitle\s*=\s*(.+)$"))
    r["artist"] = first(r"^\s*artist\s*=\s*(.+)$")
    dur = first(r"(?<![A-Za-z])duration\s*=\s*(\d+)")
    pos = first(r"\b(?:pos|position)\s*=\s*(\d+)")
    state = (first(r"\bstate\s*=\s*([A-Za-z0-9_]+)")
             or first(r"\bmState\s*=\s*(\d+)"))
    if dur.isdigit():
        r["duration"] = int(dur)
    if pos.isdigit():
        r["position"] = int(pos)
    if state:
        if state.isdigit():
            r["playing"] = MEDIA_STATE_NAMES.get(int(state)) == "playing"
        else:
            r["playing"] = state.upper() == "PLAYING"
    if r["title"] or r["app"] or r["playing"]:
        r["empty"] = False
    return r


def now_playing(serial):
    """当前设备正在播放的内容。结果带 3.5s 缓存（同一设备）；休眠时不查——
    屏幕关着一般也没在放，dumpsys 虽然照常响应，但纯属浪费。"""
    now = time.time()
    c = _nowplaying_cache
    if c["serial"] == serial and now - c["ts"] < NOWPLAYING_TTL:
        return c["val"]
    val = {"connected": True, "playing": False, "empty": True,
           "title": "", "artist": "", "app": "", "duration": 0, "position": 0}
    if screen_awake(serial):
        try:
            val.update(parse_media_session(
            adb.shell(serial, "dumpsys media_session", timeout=6)))
        except AdbError as e:
            val["error"] = str(e)
    _nowplaying_cache.update(ts=now, serial=serial, val=val)
    return val


# ---------------- 音量 ----------------
# 按音量键后手机端要立刻有反馈（学 Google TV 官方遥控的本地 OSD）：电视自己也会弹
# 系统音量条，但 adb 发键是「发完就走」，手机端一片安静——用户不知道键送到没有。
# 两条读通道，都不保证存在，认不出就安静降级（supported: False），绝不抛：
#   1. `media volume --stream 3 --get`（新系统：volume is 7 in range [0..15]）
#   2. `dumpsys audio` 的 STREAM_MUSIC 段（Current: 7，老 ROM 兜底）
#   `muted` 三态：True/False 只有 dumpsys audio 读得出来；`media volume --get`
#   不带静音位，走这条通道时 muted=None（「不知道」）。前端据此保留本地乐观值——
#   否则按静音后 220ms 的真值校正会把「静音」抹成格数，反馈等于没有。
# 音量只有我们的按键和电视遥控会改，0.8s TTL 扛得住连按（同一次查询多人共享）。
VOLUME_TTL = 0.8
STREAM_MUSIC = 3                      # Android 媒体流固定 3（音乐/视频/游戏都走它）
_volume_cache = {"ts": 0.0, "serial": None, "val": None}


def parse_media_volume(text) -> dict:
    """`media volume --get` 输出 → {"level", "max"}；认不出返回 {}。"""
    m = re.search(r"volume\s+is\s+(\d+)\s+in\s+range\s*\[0\.\.(\d+)\]",
                  text or "", re.IGNORECASE)
    return {"level": int(m.group(1)), "max": int(m.group(2))} if m else {}


def parse_dumpsys_audio(text) -> dict:
    """dumpsys audio 的 STREAM_MUSIC 段 → {"level", "max"?, "muted"?}。

    段头 `- STREAM_MUSIC:`（各 ROM 缩进不一），块内 `Current: 7, Latest: 7`；
    部分 ROM 还带 `Max: 15` / `Muted: true`（或 `Mute count: 1`）。字段缺省就缺，
    别抛——这条通道本身就是给新系统 `media` 命令兜底的。"""
    m = re.search(r"-\s*STREAM_MUSIC\s*:(.*?)(?=\n\s*-\s*STREAM_[A-Z]|\Z)",
                  text or "", re.DOTALL | re.IGNORECASE)
    if not m:
        return {}
    seg = m.group(1)
    cur = re.search(r"\bCurrent\s*:\s*(\d+)", seg)
    if not cur:
        return {}
    r = {"level": int(cur.group(1))}
    mx = re.search(r"\bMax\s*:\s*(\d+)", seg)
    if mx:
        r["max"] = int(mx.group(1))
    if re.search(r"\bMuted\s*[:=]\s*true\b", seg, re.IGNORECASE) or \
            re.search(r"\bMute\s+count\s*:\s*[1-9]", seg):
        r["muted"] = True
    return r


def volume(serial):
    """当前媒体音量。套路同 now_playing：同设备 TTL 缓存、休眠也照查
    （settings/dumpsys 类命令不挂，和 input 相反；sleeping 只管电视屏幕亮不亮）。"""
    now = time.time()
    c = _volume_cache
    if c["serial"] == serial and now - c["ts"] < VOLUME_TTL:
        return c["val"]
    val = {"connected": True, "supported": False, "level": -1, "max": 15, "muted": None}
    got = {}
    try:
        got = parse_media_volume(
            adb.shell(serial, "media volume --stream %d --get" % STREAM_MUSIC, timeout=5))
    except AdbError:
        got = {}
    if not got:
        try:
            got = parse_dumpsys_audio(adb.shell(serial, "dumpsys audio", timeout=6))
        except AdbError as e:
            val["error"] = str(e)
    if got:
        val.update(got)
        val["supported"] = True
    _volume_cache.update(ts=now, serial=serial, val=val)
    return val


# ---------------- 音量精确设置（按格） ----------------
# 学习源：androidtv 0.0.75（MIT，Copyright (c) 2020 Jeff Irion，commit 343b74e）——
# Home Assistant 遥控 Android TV 的协议库。此前本项目音量链路是「按键 + 只读 OSD」，
# 想一步调到某一格只能连按；按格设置的完整答案 androidtv 已经踩过一遍，三条口径直接搬：
#   1) constants.py:145/148 两条 set 命令：旧系统
#      `media volume --show --stream 3 --set N`，Android 11+ 是
#      `cmd media_session volume --show --stream 3 --set N`。它按 sw_version 二选一
#      （basetv.py:280-284）；我们不多花一次 build.prop 查询，改成有序降级：读通道
#      已在用的 `media volume` 先试，抛 AdbError 再试 `cmd media_session volume`。
#   2) basetv_async.py:830 的夹取公式 int(min(max(round(x), 0.0), max_volume))：
#      先 round 再夹。只夹不舍会把 7.6 卡成 7 再跳 8，只舍不夹会放出 16 / -1，
#      电视侧命令直接报错。max 也按不可信输入处理（读通道挂掉时前端兜底 15）。
#   3) basetv_async.py:825-828：max_volume 拿不到就先去 volume() 读一次，仍拿不到
#      直接返回 None（放弃）。同思路：读通道全废时 POST 报错，让前端退回音量键——
#      24/25 在任何 ROM 上都认，这才是老设备上的降级方向，不是猜一个格数发出去。
VOLUME_MAX_FALLBACK = 15   # 读不到 max 时的兜底格数（STREAM_MUSIC 常见上限）

def _vol_finite_int(value):
    """把入参 round 成整数；非数字 / NaN / inf 一律返回 None（调用方按「不可信」处理）。

    JSON body 里的 level 理论上只会是数字，但网关/脚本可能塞进任意类型；
    int(float("inf")) 抛的是 OverflowError 而不是 ValueError，单独兜住。
    舍入口径是**半点向上**（int(x + 0.5)）：Python3 内建 round 是银行家舍入
    （8.5→8），前端 Math.round 是 8.5→9，直接用内建 round 会造出「滑条停在 9、
    电视设成 8」的错位；直接 int() 截断则会把 7.6 卡成 7（先 round 后夹的另一半）。"""
    try:
        num = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if num != num or num == float("inf") or num == float("-inf"):
        return None
    return int(num + 0.5) if num >= 0 else -int(-num + 0.5)


def clamp_volume_level(level, max_level):
    """把目标格数 round 后夹到 [0, max_level]，返回 (level, max)。

    round 与 clamp 的顺序照抄 androidtv basetv_async.py:830 的
    int(min(max(round(...), 0.0), max_volume))。max_level 不可信（缺省 / 0 /
    非数字）时退到 VOLUME_MAX_FALLBACK——滑条的 max 来自上一次回读，可能是旧值。
    level 不可信时按 0 处理（不抛，让调用方按业务决定）。"""
    top = _vol_finite_int(max_level)
    if top is None or top <= 0:
        top = VOLUME_MAX_FALLBACK
    lvl = _vol_finite_int(level)
    if lvl is None:
        # NaN / inf / 非数字：合法 JSON 到不了这里，但绝不猜一个格数发出去
        lvl = 0
    return max(0, min(lvl, top)), top


def volume_set_cmd(level: int):
    """设置音量的两条候选命令（有序降级链）。学 androidtv constants.py:145/148：
    `media volume` 是读通道已在用的同款命令先试，Android 11+ 的
    `cmd media_session volume` 兜底。"""
    return [
        "media volume --show --stream %d --set %d" % (STREAM_MUSIC, level),
        "cmd media_session volume --show --stream %d --set %d" % (STREAM_MUSIC, level),
    ]


def volume_set(serial, level):
    """把 STREAM_MUSIC 音量设到绝对格数 level。返回 {ok, level, max, cmd}。

    流程：volume() 拿当前值与 max（0.8s TTL 内免费）→ 夹取 → 按序试两条 set
    命令。读通道全废（supported=False）或两条命令都失败时抛 AdbError，前端据此
    退回按键模式。成功后主动失效 _volume_cache：音量是被我们亲手改掉的真状态，
    不清缓存下一次读会返回旧格数（AGENTS.md 的缓存失效红线）。"""
    cur = volume(serial)
    if not cur.get("supported"):
        raise AdbError("该设备不支持精确设置音量，请用音量键")
    lvl, top = clamp_volume_level(level, cur.get("max"))
    for cmd in volume_set_cmd(lvl):
        try:
            adb.shell(serial, cmd, timeout=5)
        except AdbError:
            continue                      # 换下一条（Android 11+ 的新命令）
        _volume_cache.update(ts=0.0)      # 立刻作废，下一次读拿到新格数
        return {"ok": True, "level": lvl, "max": top, "cmd": cmd}
    raise AdbError("设置音量失败：设备不支持该命令")


# ---------------- ADBKeyboard 中文键盘 ----------------
def current_android_target() -> str:
    with state_lock:
        cur = state.get("current") or {}
    if cur.get("type") != "android" or not cur.get("target"):
        raise AdbError("未连接 Android TV")
    return cur["target"]


def ime_current(serial) -> str:
    """当前输入法组件名。只取 default_input_method，一次 shell —— 走中文输入路径时每次都要确认"""
    try:
        v = adb.shell(serial, "settings get secure default_input_method", timeout=5).strip()
    except AdbError:
        return ""
    return "" if v in ("null", "None", "0") else v


def ime_status(serial):
    """ADBKeyboard 的 安装 / 已启用 / 是当前 三态。切换输入法后必须重新取"""
    st = {
        "ime": ADBKB_IME,
        "installed": False,   # APK 装了没（ime list -a 里能查到）
        "enabled": False,     # 在「语言和输入法」里勾上了没
        "current": False,     # 当前正在用（决定了中文能不能打进去）
        "default_ime": ime_current(serial),
        "apk": ADBKB_APK,
        "apk_a16": ADBKB_APK_A16,
    }
    try:
        enabled = adb.shell(serial, "settings get secure enabled_input_methods", timeout=5).strip()
        st["enabled"] = ADBKB_IME in enabled
    except AdbError:
        pass
    try:
        st["installed"] = ADBKB_IME in adb.shell(serial, "ime list -a", timeout=8)
    except AdbError:
        pass
    st["current"] = st["default_ime"] == ADBKB_IME
    return st


def require_adbkb(serial):
    """广播在没有接收器时同样返回 result=0（静默失效），所以每个中文操作前都要确认
    ADBKeyboard 就是电视的当前输入法 —— 否则用户点了没反应还以为是坏了"""
    cur_ime = ime_current(serial)
    if cur_ime != ADBKB_IME:
        raise AdbError("该操作需要电视的当前输入法是 ADBKeyboard（现在是{}），"
                       "点键盘区的「启用」".format(cur_ime or "未知输入法"))


def adbkb_text(serial, text):
    """base64 广播输入任意 Unicode（ADB_INPUT_TEXT 在 Oreo+ 传 UTF-8 会坏，必须用 B64）"""
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    adb.shell(serial, "am broadcast -a ADB_INPUT_B64 --es msg " + sh_quote(b64), timeout=8)


def adbkb_clear(serial):
    adb.shell(serial, "am broadcast -a ADB_CLEAR_TEXT", timeout=8)


def adbkb_action(serial, code: int):
    adb.shell(serial, "am broadcast -a ADB_EDITOR_CODE --ei code {}".format(int(code)), timeout=8)


# 键盘焦点态要经 pyatv 的 asyncio loop + 遥控器锁（最坏等 12s），不能真的挂进
# 前端 8s 轮询：电视卡住时按键会被它排在后面。取上次值 + 短 TTL 足以驱动那个徽标。
KB_FOCUS_TTL = 10.0
_kb_focus_cache = {"ts": 0.0, "value": None}


def kb_focus_cached():
    now = time.time()
    if now - _kb_focus_cache["ts"] < KB_FOCUS_TTL:
        return _kb_focus_cache["value"]
    value = atv_mgr.keyboard_focus()
    _kb_focus_cache["ts"] = now
    _kb_focus_cache["value"] = value
    return value


def make_status():
    with state_lock:
        cur = dict(state.get("current") or {})
        info = dict(state.get("info", {}))
        recent = list(state.get("recent_android", []))
        appletvs = list(state.get("appletvs", []))

    devices = []
    if adb and adb.exists():   # adb 只在 main() 里构造；被当库 import 时先别崩
        try:
            devices = adb.devices()
        except AdbError:
            devices = []
    ctype = cur.get("type")
    cur_state = None
    if ctype == "android":
        cur_state = next((d["state"] for d in devices if d["serial"] == cur.get("target")), None)
    return {
        "adb_found": bool(adb and adb.exists()),
        "adb_path": adb.path if adb else "",
        "adb_version": adb.version() if adb and adb.exists() else "",
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
            "kb_focus": (kb_focus_cached() if atv_mgr and atv_mgr.connected else None),
        },
        "sleep_timer": timer_state(),
        "macro": macro_state(),
        "macro": macro_state(),
        "macro": macro_state(),
        "macro": macro_state(),
        "version": app_version(),
        # 页面用它隐藏「把遥控器装到手机」卡片（手机 App 内嵌引擎时）
        "embedded": EMBEDDED,
        "auto_reconnect": {
            "active": _auto_reconn["active"],
            "stopped": _auto_reconn["stopped"],
            "fails": _auto_reconn["fails"],
            # 距下次重试还剩几秒（0 = 现在就会试 / 没在等）；前端拿它做倒计时，
            # 不然页面上只有一个「自动重连中」，用户不知道还要等多久
            "next_in": (max(0, round(_auto_reconn["next_retry"] - time.time()))
                        if _auto_reconn["next_retry"] else 0),
        },
    }


# ---------------- 一键体检报告（第三十四轮） ----------------
# 学 Home Assistant：util/redact.py 把「哪些键的值不能外泄」显式列成集合，递归遍历到时
# 整值替换成 **REDACTED**（常量沿用它的原值），不靠「记得别加这个字段」这种自觉。
# 我们比 HA 再退一步：报告字段全部白名单现场拼装，state.json 的配对凭据从头就不进门；
# 递归脱敏是纵深防御——将来谁手滑加了名叫 token / credentials 的键也漏不出去。
# 分歧（有意）：HA 允许 to_redact 传 key→callable 做「留头去尾」的部分掩码；这里只整值
# 替换。报告会被用户原样贴进群/issue，「前 4 后 4 位」同样是明文，掩了等于没掩。
DIAG_SENSITIVE_KEYS = ("token", "credentials", "credential", "pairing", "password",
                       "secret", "private_key", "authorization")
DIAG_REDACTED = "**REDACTED**"


def redact_diagnostics(data, sensitive=DIAG_SENSITIVE_KEYS):
    """递归脱敏（对齐 homeassistant/util/redact.async_redact_data，去掉它的 asyncio 装饰器）。

    HA 的语义照抄：dict 逐键处理，命中 sensitive 的字符串值换掉，嵌套 dict / list 递归；
    None 与空串原样保留（它们不是值，删了反而破坏形状）。输入不被修改——调用方随后要
    json.dumps，mutate 了会连带污染内存里的 state 视图。
    """
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            # HA 的顺序：先放行 None / 空串（它们不是值），再判键名；命中就整值替换，
            # 不限字符串类型——token 类的键哪怕混进数字 / 子结构也不该出去。
            if value is None or (isinstance(value, str) and not value):
                out[key] = value
            elif key in sensitive:
                out[key] = DIAG_REDACTED
            else:
                out[key] = redact_diagnostics(value, sensitive)
        return out
    if isinstance(data, list):
        return [redact_diagnostics(v, sensitive) for v in data]
    return data


def make_diagnostics():
    """一键体检（提案 E3）：用户报「连不上」时，不用再来回问版本 / adb / IME 三态。

    字段白名单现场拼：版本、平台与 Python、adb（在不在 / 路径 / 版本 / 常驻 shell 存活 /
    设备列表与状态）、当前设备与 IME 三态、pyatv 可用性与已配对台数（只报数量，条目里
    有配对凭据）、令牌只报「开没开」不报值、mDNS 可用性。
    绝不整体序列化 state —— 那里面有 Apple TV 配对凭据（AGENTS.md 安全约束）。
    """
    with state_lock:
        cur = dict(state.get("current") or {})
        recent = list(state.get("recent_android", []))
        appletvs = list(state.get("appletvs", []))

    adb_ok = bool(adb and adb.exists())
    dev_list = []
    if adb_ok:
        try:
            dev_list = adb.devices()
        except AdbError:
            dev_list = []

    ime = None
    # IME 三态只对 Android TV 有意义（ADBKeyboard 是它的输入法）
    target = cur.get("target") if cur.get("type") == "android" else None
    if target and adb_ok:
        try:
            ime = ime_status(target)
        except AdbError:
            ime = None

    report = {
        "version": app_version(),
        "embedded": EMBEDDED,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "adb": {
            "found": adb_ok,
            "path": adb.path if adb else "",
            "version": adb.version() if adb_ok else "",
            "shell_alive": bool(adb and adb._shell is not None
                                and adb._shell.poll() is None),
            "devices_cache_ttl": DEVICES_CACHE_TTL,
            "devices": [{"serial": d.get("serial", ""), "state": d.get("state", "")}
                        for d in dev_list],
        },
        "appletv": {
            "pyatv_available": bool(ATV_AVAILABLE),
            "paired_count": len(appletvs),
            "connected": bool(atv_mgr and atv_mgr.connected),
        },
        "current": {
            "type": cur.get("type"),
            # 与 make_status 同口径：Android TV 在 state 里叫 target，Apple TV 叫 id，
            # 合成一个键（前端按 type 渲染「Android TV / Apple TV」前缀）
            "target": cur.get("target") if cur.get("type") == "android" else cur.get("id"),
            # 选中的那台此刻的在线态（device / offline / unauthorized…），「连不上」时最先看它
            "state": (next((d["state"] for d in dev_list
                            if d.get("serial") == target), None) if target else None),
            "recent_android_count": len(recent),
        },
        "ime": ime,
        "auth": {
            # 只报开关与模式，值永远不进报告——报告是要被贴出去的
            "token_enabled": bool(AUTH_TOKEN),
            "mode": ("lan" if AUTH_TOKEN else "off"),
            "loopback_hosts": list(LOOPBACK_HOSTS),
        },
        "mdns": {"dns_sd_available": bool(shutil.which("dns-sd"))},
    }
    return redact_diagnostics(report)


# ---------------- 命令处理 ----------------
WAKE_KEYCODES = (26, 223, 224)   # POWER / SLEEP / WAKEUP


def is_wake_cmd(cmd: str) -> bool:
    """是否是唤醒 / 电源键。这类按键本就是用来解除休眠的，超时后不能再报「休眠」"""
    m = re.match(r"input keyevent ([\d \-]+)$", (cmd or "").strip())
    if not m:
        return False
    try:
        return bool(set(WAKE_KEYCODES) & {int(x) for x in m.group(1).split()})
    except ValueError:
        return False


def device_asleep(serial) -> bool:
    """设备是否在休眠。只在超时路径上调用（罕见），不影响正常按键延迟"""
    try:
        out = adb.shell(serial, "dumpsys power", timeout=5)
    except AdbError:
        return False
    return "mWakefulness=Asleep" in out or "mWakefulness=Dozing" in out


# 屏幕状态短 TTL 缓存：连按方向键时每键都查一轮 dumpsys 太贵（它比 input 便宜，
# 但还不至于免费）；休眠时 input 会一直挂住，发命令前必须先确认屏幕是亮着的
SCREEN_CACHE_TTL = 1.0
_screen_cache = {"ts": 0.0, "awake": True}
# 会注入按键 / 启动界面的命令类型：这些在屏幕熄灭时都会阻塞到超时
INPUT_TYPES = ("key", "text", "tap", "swipe", "app", "settings")


def screen_awake(serial, fresh=False) -> bool:
    """电视屏幕是否点亮。dumpsys power 在休眠时也正常响应（input 会挂，它不会）"""
    now = time.time()
    if not fresh and now - _screen_cache["ts"] < SCREEN_CACHE_TTL:
        return _screen_cache["awake"]
    try:
        out = adb.shell(serial, "dumpsys power", timeout=5)
    except AdbError:
        return _screen_cache["awake"]   # 查不到就按老样子发，让命令自己的错误说话
    awake = not ("mWakefulness=Asleep" in out or "mWakefulness=Dozing" in out)
    _screen_cache.update(ts=now, awake=awake)
    return awake


def wake_codes_in_body(body) -> bool:
    """本次按键里是否含唤醒 / 电源键（26 / 223 / 224）：这类键本就是用来解除
    休眠的，发出去就又变成「用户已经手动唤醒」的语义，不能再抢着代发"""
    raw = body.get("codes") or ([body["code"]] if "code" in body else [])
    try:
        return bool({int(c) for c in raw} & set(WAKE_KEYCODES))
    except (TypeError, ValueError):
        return False


def ensure_awake(serial):
    """屏幕熄灭时 input 类命令会一直挂到超时：先代发唤醒键，再轮询到屏幕点亮（≤2s）。
    唤醒键发出去后 input 等回执照样会挂住，所以短超时后靠 dumpsys 轮询确认。"""
    if screen_awake(serial):
        return
    try:
        adb.shell(serial, "input keyevent 224", timeout=1.5)
    except AdbError:
        pass   # 唤醒键多半已注入，超时只是等不到回执；交给下面轮询确认
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if screen_awake(serial, fresh=True):
            return
        time.sleep(0.25)


def run_shell(serial, cmd, timeout=6):
    """执行命令；设备端偶发挂起（如模拟器 input text）时重试一次"""
    try:
        return adb.shell(serial, cmd, timeout=timeout)
    except AdbError as e:
        if "超时" not in str(e):
            raise
        # 屏幕熄灭时 `input` 会一直阻塞（实测 keyevent 也一样）。此时报「未授权/离线」
        # 是误导，用户会去查授权 —— 先看一眼休眠状态，给出能直接行动的原因。
        if device_asleep(serial):
            if is_wake_cmd(cmd):
                return ""   # 唤醒键已注入，只是 input 等回执时挂住了；别再报错吓人
            raise AdbError("电视处于休眠状态，命令送不进去：先点「☀ 唤醒」"
                           "（或按一下电视遥控器的电源键）")
        return adb.shell(serial, cmd, timeout=timeout)


# ---------------- 睡眠定时 ----------------
# 定时只存内存：服务重启即失效（重新设一次即可），不把这种易变状态写进 state.json
SLEEP_TIMER_MAX_MIN = 12 * 60
_timer_lock = threading.Lock()
_timer_stop = threading.Event()   # 停机信号：测试要能确定地停掉这个循环
_sleep_until = None    # epoch 秒；None = 未设置


def timer_state():
    with _timer_lock:
        until = _sleep_until
    if not until:
        return {"active": False, "until": None, "remaining": 0}
    return {"active": True, "until": until, "remaining": max(0, int(until - time.time()))}


def handle_timer(body):
    global _sleep_until
    action = body.get("action", "set")
    if action == "cancel":
        with _timer_lock:
            _sleep_until = None
        return {"ok": True, "sleep_timer": timer_state()}
    if action != "set":
        raise AdbError("未知定时操作: {}（可用 set / cancel）".format(action))
    minutes = body.get("minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or \
            not (0 < minutes <= SLEEP_TIMER_MAX_MIN):
        raise AdbError("定时分钟数不合法：{}（1 ~ {}）".format(minutes, SLEEP_TIMER_MAX_MIN))
    with _timer_lock:
        _sleep_until = time.time() + minutes * 60
    return {"ok": True, "sleep_timer": timer_state()}


def _fire_sleep_timer():
    """到点执行：Android 发 SLEEP 键（223，幂等——不像电源键会翻转状态），
    Apple TV 走电源关闭。屏幕本来就黑着就等于已完成，不再发命令。"""
    with state_lock:
        cur = dict(state.get("current") or {})
    try:
        if cur.get("type") == "android" and cur.get("target"):
            if screen_awake(cur["target"]):
                run_shell(cur["target"], "input keyevent 223")
        elif cur.get("type") == "appletv" and atv_mgr and atv_mgr.connected:
            atv_mgr.send_keys([26])
    except Exception as e:
        traceback.print_exc()
        print("睡眠定时执行失败: {}".format(e), file=sys.stderr)


def _sleep_timer_loop():
    """每秒看一眼；到点后清掉再执行（清掉与点火之间持锁，避免并发重复触发）。
    注意 global 声明：下面给 _sleep_until 赋值会让它成为局部变量，
    没有 global 时第一次读就 UnboundLocalError（重启实例时实测过）。"""
    global _sleep_until
    while not _timer_stop.is_set():
        try:
            with _timer_lock:
                until = _sleep_until
            if until and time.time() >= until:
                with _timer_lock:
                    if _sleep_until == until:
                        _sleep_until = None
                _fire_sleep_timer()
        except Exception:
            traceback.print_exc()
        _timer_stop.wait(1.0)   # Event.wait：停机时立刻醒，不等满 1s


# ---------------- Android 掉线自动重连 ----------------
# 在线过又掉了才自动重连（从未连上过的是用户还没连，不去碰）；
# 连续失败 RECONNECT_MAX 次就停并提示手动，用户手动连上后自动重新武装
RECONNECT_MAX = 3
RECONNECT_BASE = 6.0	# 首次重试前等（秒）
RECONNECT_MAX_DELAY = 60.0	# 退避上限（秒）


def reconnect_delay(fails: int, base: float = RECONNECT_BASE,
                    cap: float = RECONNECT_MAX_DELAY) -> float:
    """第 fails 次失败之后、下一次重试之前该等多久（秒）。

    学习源：tenacity 9.1.4（Apache-2.0，© Julien Danjou）的 wait_exponential ——
    result = multiplier * exp_base ** (attempt_number - 1)，再钳进 [min, max]。
    核实到的事实（读 wheel 里的 tenacity/wait.py）：它的 docstring 明确区分两种场景 ——
    「资源不可用、时长未知」用固定指数的 wait_exponential（无抖动）；
    「多个无协调进程争用同一资源」才用 wait_random_exponential（即 AWS 那篇讲的
    Full Jitter）。本项目是单进程后台循环在等一台正在重启的电视，属于前者，
    所以刻意不加抖动：这里没有第二个进程竞争，抖动只会让「还要等多久」
    变得不可预期。

    原来是固定 30 秒冷却：电视重启通常几十秒，前两次重试间隔完全一样，
    既可能在电视还没起来时白白打一次，也可能在电视早就能连时还在干等。
    """
    if fails < 1 or base <= 0:
        return 0.0
    exp = 2 ** (fails - 1)
    # exp 长到一定程度后 base * exp 会 OverflowError，而那时结果必然是 cap：先判再乘
    if exp >= cap / base:
        return cap
    return max(0.0, min(cap, base * exp))


_auto_reconn = {"was_online": False, "fails": 0, "next_retry": 0.0,
                "active": False, "stopped": False}


def auto_reconn_reset():
    """连接成功（手动 / 自动 / 开机恢复）后重新武装自动重连"""
    _auto_reconn.update(was_online=True, fails=0, next_retry=0.0,
                        active=False, stopped=False)


def _auto_reconnect_tick():
    """单次探测：当前 Android 设备在线过又掉了 → 重连一次（带退避与次数上限）"""
    with state_lock:
        cur = state.get("current") or {}
    target = cur.get("target") if cur.get("type") == "android" else None
    if not target:
        return
    st = next((d["state"] for d in adb.devices() if d["serial"] == target), None)
    if st == "device":
        _auto_reconn.update(was_online=True, fails=0, next_retry=0.0,
                            active=False, stopped=False)
        return
    if not (_auto_reconn["was_online"] and not _auto_reconn["stopped"]
            and time.time() >= _auto_reconn["next_retry"]):
        return
    _auto_reconn["active"] = True
    adb.reset_shell()
    adb.invalidate_devices()
    try:
        adb.connect(target)
    except AdbError:
        _auto_reconn["fails"] += 1
        if _auto_reconn["fails"] >= RECONNECT_MAX:
            _auto_reconn.update(stopped=True, active=False)
        _auto_reconn["next_retry"] = time.time() + reconnect_delay(_auto_reconn["fails"])


def _auto_reconnect_loop():
    while not _timer_stop.is_set():
        try:
            _auto_reconnect_tick()
        except Exception:
            traceback.print_exc()
        _timer_stop.wait(3.0)


# ---------------- Android 无线调试：mDNS 扫描 / 配对 ----------------
def _mdns_hosts():
    """`adb mdns services`：无线调试设备广播的配对 / 连接端口。
    只开「网络调试 5555」的老电视不广播 mDNS，扫不到仍要手输 IP。"""
    try:
        out = adb.run("mdns", "services", timeout=5)
    except AdbError as e:
        raise AdbError("扫描失败：{}".format(e))
    found = {}
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        svc, addr = parts[0], parts[1]
        if "adb-tls-pairing" in svc:
            kind = "pairing"
        elif "adb-tls-connect" in svc:
            kind = "connect"
        else:
            continue
        host, _, port = addr.rpartition(":")
        if host and port.isdigit():
            found.setdefault((host, kind), int(port))
    hosts = {}
    for (host, kind), port in found.items():
        hosts.setdefault(host, {})[kind] = port
    return [{"host": h, "pairing": p.get("pairing"), "connect": p.get("connect")}
            for h, p in sorted(hosts.items())]


def handle_android_scan(_body):
    return {"hosts": _mdns_hosts()}


PAIR_CODE_RE = re.compile(r"^\d{6}$")


def handle_android_pair(body):
    """Android 11+ 无线调试首次使用要先配对：码在电视「网络调试」页面显示"""
    host = str(body.get("host", "")).strip()
    port = body.get("port")
    code = str(body.get("code", "")).strip()
    if not TARGET_RE.match(host):
        raise AdbError("主机地址不合法：{}".format(host))
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        raise AdbError("配对端口不合法：{}".format(port))
    if not PAIR_CODE_RE.match(code):
        raise AdbError("配对码须为 6 位数字")
    out = adb.run("pair", "{}:{}".format(host, port), code, timeout=15)
    if "success" not in out.lower():
        raise AdbError("配对失败：{}".format(out))
    adb.invalidate_devices()
    return {"ok": True}


def handle_cmd(body):
    """按当前设备类型分发：android → adb，appletv → pyatv；
    timer 与当前连的是什么设备无关，在这里先拦掉"""
    if body.get("type") == "timer":
        return handle_timer(body)
    if body.get("type") == "macro":
        return handle_macro(body)
    with state_lock:
        ctype = (state.get("current") or {}).get("type")
    if ctype == "appletv":
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

    # 屏幕熄灭时 input 会阻塞到超时：先探一次屏幕，熄了代发唤醒键再等点亮。
    # 唤醒 / 电源键豁免（它本身就是解除休眠的手段）
    if t in INPUT_TYPES and not (t == "key" and wake_codes_in_body(body)):
        ensure_awake(cur)

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
        if all(ord(ch) <= 0x7E for ch in text):
            run_shell(cur, "input text " + sh_quote(text))   # ASCII 走原生通道，最快
        else:
            # 非 ASCII：广播收不到时字符是静默丢失的，先确认输入法再发
            require_adbkb(cur)
            adbkb_text(cur, text)
        if body.get("enter"):
            run_shell(cur, "input keyevent 66")
        return {"ok": True}

    if t == "clear":
        require_adbkb(cur)
        adbkb_clear(cur)
        return {"ok": True}

    if t == "editor":
        code = body.get("code", 3)
        code = int(code) if str(code).lstrip("-").isdigit() else -1
        if code not in ADBKB_ACTIONS.values():
            raise AdbError("编辑器动作码不合法：{}（可用 {}）".format(
                body.get("code"), "/".join(ADBKB_ACTIONS)))
        require_adbkb(cur)
        adbkb_action(cur, code)
        return {"ok": True}

    if t == "tap":
        x, y = coord_of(body, "x"), coord_of(body, "y")
        run_shell(cur, "input tap {} {}".format(x, y))
        return {"ok": True}

    if t == "swipe":
        args = [coord_of(body, k) for k in ("x1", "y1", "x2", "y2")]
        dur = clamp_duration(body.get("duration"))
        run_shell(cur, "input swipe {} {} {} {} {}".format(*args, dur))
        return {"ok": True}

    if t == "app":
        pkg = str(body.get("pkg", "")).strip()
        if not APP_ID_RE.match(pkg):
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
        # 每条命令最坏阻塞 12s，不限量等于让一个请求长期占死遥控器通道
        codes = list(codes)[:MAX_KEYCODES]
        # 与 Android 分支同款过滤：pyatv 的 send_keys 只吃整数键码，
        # 客户端塞进 null / "abc" 时必须在这里挡住（以前一路炸成 500）
        codes = [int(c) for c in codes if str(c).lstrip("-").isdigit()]
        if not codes:
            raise AppleTvError("键码不合法")
        atv_mgr.send_keys(codes)
        return {"ok": True, "sent": codes}
    if t == "text":
        # 与 Android 分支同样限长：长文本会一直卡在 pyatv 的往返里
        text = str(body.get("text", ""))[:MAX_TEXT_LEN]
        if not text:
            raise AppleTvError("内容为空")
        atv_mgr.send_text(text, enter=body.get("enter"))
        return {"ok": True}
    if t == "tap":
        atv_mgr.tap()  # Apple TV 无坐标点击，等价轻点
        return {"ok": True}
    if t == "swipe":
        # 前端传归一化坐标 0.0~1.0
        atv_mgr.swipe(norm_coord(body, "x1"), norm_coord(body, "y1"),
                      norm_coord(body, "x2"), norm_coord(body, "y2"),
                      clamp_duration(body.get("duration")))
        return {"ok": True}
    if t == "app":
        pkg = str(body.get("pkg", "")).strip()
        if not APP_ID_RE.match(pkg):
            raise AppleTvError("应用标识不合法：{}".format(pkg))
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
    if not entry.get("id") or not entry.get("ip"):
        # 空 body / 只带 ip：以前走到这里 entry["id"] 炸 KeyError 回 500
        raise AppleTvError("缺少设备信息（id/ip）")
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


def handle_ime(body):
    """ADBKeyboard 中文键盘：status / enable / reset"""
    action = body.get("action") or "status"
    serial = current_android_target()
    if action == "status":
        return ime_status(serial)

    if action == "enable":
        st = ime_status(serial)
        if not st["installed"]:
            # 不抛异常：长 APK 链接进 toast 会糊成一团，交给前端渲染成可点链接
            st["ok"] = False
            st["hint"] = "电视上未安装 ADBKeyboard，请先按提示装 APK（Android 16 必须用 v2.5-dev）"
            return st
        if not st["enabled"]:
            adb.shell(serial, "ime enable " + ADBKB_IME, timeout=8)
        adb.shell(serial, "ime set " + ADBKB_IME, timeout=8)
        time.sleep(0.3)   # 切换输入法有延迟，等一下再回读
        st = ime_status(serial)
        st["ok"] = st["current"]
        if not st["current"]:
            st["hint"] = ("切换命令已发出，但电视当前仍是「{}」。请看电视屏幕："
                          "若弹出「选择输入法」确认框，用遥控器点「确定」。"
                          .format(st["default_ime"] or "未知输入法"))
        return st

    if action == "reset":
        adb.shell(serial, "ime reset", timeout=8)
        time.sleep(0.3)
        return ime_status(serial)

    raise AdbError("未知的键盘动作: {}".format(action))


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
    auto_reconn_reset()
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
    auto_reconn_reset()
    return {"ok": True, "info": info}


# ---------------- 分发：手机一键安装 ----------------
INSTALL_SCRIPT = r"""#!/data/data/com.termux/files/usr/bin/bash
# ATV Remote 手机引擎一键安装（在 Termux 里: curl -sL __HOST__/install | bash）
# 顺序很关键：先把项目（含 start.sh）拉下来，再装依赖。依赖装不上只影响 Apple TV
# 遥控；项目没拉到，手机连手动启动都做不到。用 set -u 而不是 set -e：依赖失败必须
# 能继续。上一版 set -e 下一条 pip 失败就把整个安装静默中断了，用户只看到「无目录」。
set -u
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
echo "📱 ATV Remote 引擎安装中（3-8 分钟，仅需这一次）..."
pkg update -y >/dev/null 2>&1 || true
# libc++ 要先跟上：android-tools 的 adb 按新版 libc++ 链接，落后会
# CANNOT LINK EXECUTABLE adb: cannot locate symbol _ZNSt6__ndk113__hash_memoryEPKVm
pkg install -y libc++ || echo "⚠️ libc++ 更新失败，adb 可能起不来"
pkg install -y python clang libffi openssl android-tools \
  || echo "⚠️ 部分依赖装失败，继续拉取项目（Android TV 遥控不受影响）"
# 预编译 cryptography（避免本地编 rust）
pkg install -y tur-repo >/dev/null 2>&1 && pkg install -y python-cryptography || true

# 拉取项目（含 start.sh；仅当 Mac 启用了 --token 时才一并同步配对记录）
# -f：HTTP 报错时别把错误页当压缩包写进去；--retry：局域网抖一下不至于整轮重来
mkdir -p "$PREFIX/tmp"
if ! curl -fsSL --retry 3 --retry-delay 2 -o "$PREFIX/tmp/atv.tgz" __HOST__/bundle.tgz__TOKENQ__; then
  echo "❌ 项目包下载失败：确认手机与电脑在同一 Wi-Fi，然后重跑本命令"
  exit 1
fi
tar xzf "$PREFIX/tmp/atv.tgz" -C "$HOME" || { echo "❌ 解压失败，重跑本命令"; exit 1; }
rm -f "$PREFIX/tmp/atv.tgz"
chmod +x "$HOME"/atv-remote/start.sh

# 允许 ATVRemote App 一键拉起
mkdir -p "$HOME/.termux"
grep -q allow-external-apps "$HOME/.termux/termux.properties" 2>/dev/null || \
  echo "allow-external-apps=true" >> "$HOME/.termux/termux.properties"

# 依赖放最后：失败只影响 Apple TV。stdout 静默、stderr 保留，出错能看见原因
pip install --upgrade pip wheel >/dev/null \
  || echo "⚠️ pip 升级失败，继续（不影响项目，也不影响 Android TV）"
# pyatv 0.18 依赖 pydantic 2 → pydantic-core，而 PyPI 上没有 Android/aarch64 的预编译
# wheel（只有 manylinux / musllinux / win / macOS），所以在 Termux 上必然要在本地用 Rust
# 编一遍。不先装 rust，pip 只会甩一句
#   ERROR: Failed to build pydantic-core when installing build dependencies
# 看不出缺什么。先把同样逃不掉的依赖换成 Termux 预编译包，再上 rust 工具链。
echo "🔧 装本地编译工具链（pydantic-core 在 Android 上没有预编译包，要用 Rust 编）..."
pkg install -y rust python-cryptography \
  || echo "⚠️ 工具链没装全，pip 可能要自己编，慢一些甚至失败"
PYATV_LOG="$HOME/atv-remote/pyatv-install.log"
if pip install "pyatv==0.18.0" >"$PYATV_LOG" 2>&1; then
  echo "✅ pyatv 就绪（Apple TV 遥控可用，首次编译要等几分钟）"
else
  echo "⚠️ pyatv 安装失败（Apple TV 暂不可用，Android TV 正常）——失败原因："
  tail -5 "$PYATV_LOG" | sed "s/^/    /"
  echo "    补救（可反复跑）：pkg install rust clang libffi openssl python-cryptography && pip install pyatv==0.18.0"
  echo "    完整日志：$PYATV_LOG"
fi

if ! python -c "import pyatv" 2>/dev/null; then
  echo "⚠️ pyatv 仍未装好：pkg install rust clang libffi openssl python-cryptography && pip install pyatv==0.18.0（只影响 Apple TV）"
fi

echo ""
if [ -x "$HOME/atv-remote/start.sh" ]; then
  echo "✅ 完成！打开 ATVRemote App 点「🚀 独立模式」即可遥控电视"
else
  echo "❌ 安装不完整：$HOME/atv-remote/start.sh 不存在，请重跑本命令"
  exit 1
fi
"""


BUNDLE_FILES = ("server.py", "atv_backend.py", "start.sh")


def bundle_files():
    """安装包内容清单。

    state.json 含 Apple TV 配对凭据（mrp/companion/airplay），拿到就等于拿到遥控器。
    /bundle.tgz 在启用令牌时只发给持令牌者，所以可随包同步配对记录；
    未启用令牌（默认）时局域网里任何设备都能直接下载，此时决不能把凭据打进包里。
    """
    return BUNDLE_FILES + (("state.json",) if AUTH_TOKEN else ())


def bundle_signature():
    """打包内容的文件签名（路径 + mtime + 大小），用于判断缓存是否过期"""
    sig = []
    for name in bundle_files():
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
    """打包 Termux 引擎需要的文件（启用令牌时一并同步配对记录）"""
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in bundle_files():
            p = ROOT / name
            if p.is_file():
                tar.add(str(p), arcname="atv-remote/" + name)
        static_dir = ROOT / "static"
        for p in static_dir.rglob("*"):
            if p.is_file():
                tar.add(str(p), arcname="atv-remote/static/" + p.relative_to(static_dir).as_posix())
    return buf.getvalue()


# ---------------- 一键宏（场景） ----------------
# 宏 = 一串 /api/cmd 指令 + 步间延时，server 串行执行；前端只负责编排、命名与保存。
# 为什么放线程里而不是阻塞 HTTP：20 步 × 每步最多 10s 延时，轻松超过前端 fetch 的耐心；
# 执行与失败都写 stderr（LaunchAgent 收进 server.log），取消用 stop 事件。
MACRO_STEP_TYPES = ("key", "text", "app")   # 都与当前设备类型无关，仍走 handle_cmd 分发
MACRO_MAX_STEPS = 20
MACRO_MAX_DELAY = 10000      # 单步延时上限 ms
MACRO_ERR_LEN = 120          # 分步失败原因进 trace 前的截断长度：轮询包要小
_macro_running = threading.Event()
_macro_stop = threading.Event()
# 进度快照：宏在线程里跑，轮询方只读、不推断。持 _macro_lock 写，macro_state() 同样持锁读。
_macro_lock = threading.Lock()
_macro_seq = 0        # 递增运行代号：前端据此分辨「新的一次刚结束」和「从没跑过」
_macro_prog = {"run": 0, "name": "", "total": 0, "index": 0, "done": 0,
               "failed": 0, "cancelled": False, "trace": []}

# 预置宏：开箱即用。pkg 一给多是因为「同一个 App 在 Android TV / tvOS 上包名不同」，
# 按顺序试，成功即停；自定义宏保存在浏览器 localStorage，不写 state.json（敏感文件）。
PRESET_MACROS = [
    {"id": "movie", "icon": "🎬", "name": "观影模式",
     "steps": [{"type": "app", "pkgs": ["com.netflix.ninja", "com.netflix.Netflix"]},
               {"delay": 2500},
               {"type": "key", "codes": [25, 25, 25]}]},
    {"id": "youtube", "icon": "▶️", "name": "看 YouTube",
     "steps": [{"type": "app", "pkgs": ["com.google.android.youtube.tv",
                                        "com.google.ios.youtube"]},
               {"delay": 2500}]},
    {"id": "mute", "icon": "🔇", "name": "静音 / 取消",
     "steps": [{"type": "key", "code": 164}]},
    {"id": "home", "icon": "🏠", "name": "回主页",
     "steps": [{"type": "key", "code": 3}]},
    {"id": "volume-down3", "icon": "🔉", "name": "音量降 3 格",
     "steps": [{"type": "key", "codes": [25, 25, 25]}]},
]


def app_version():
    """根目录 VERSION 的 versionName；读不到就返回空串（前端隐藏版本行）。
    设置弹窗显示它，用户报障时能直接读出在跑哪个版本。"""
    try:
        for line in (ROOT / "VERSION").read_text("utf-8").splitlines():
            if line.startswith("versionName="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def macro_state():
    """宏执行进度快照 + running 标志 + 分步 trace。
    学 Home Assistant 的 script 实体（components/script）和它每条 automation 的 trace：
    把「第几步 / 完成几步 / 失败几步 / 是否被取消 / 每步多久、为什么失败」当一等状态
    暴露，前端只做投影——既不用推断跑到哪一步，也不用翻服务端日志找失败原因。"""
    with _macro_lock:
        s = dict(_macro_prog)
        s["trace"] = [dict(t) for t in _macro_prog["trace"]]
    s["running"] = _macro_running.is_set()
    return s


def _macro_mark(**kw):
    """工作线程里批量更新进度快照。"""
    with _macro_lock:
        _macro_prog.update(kw)


def _macro_fail():
    """失败计数自增：dict[k] += 1 是读-改-写，必须在锁内一次做完。"""
    with _macro_lock:
        _macro_prog["failed"] += 1


def _macro_trace(i, ms, err=None, cancel=False):
    """记一条分步结果：第几步、耗时多久、失败原因（可选）。
    失败原因以前只进 stderr，手机上没人知道哪一步为什么挂；耗时也是同理——
    「等了 2.5s」和「卡了 2.5s」在进度条上长得一模一样。"""
    entry = {"i": i, "ms": max(0, int(ms)), "ok": not err and not cancel}
    if cancel:
        entry["cancel"] = True
    if err:
        entry["err"] = str(err)[:MACRO_ERR_LEN]
    with _macro_lock:
        _macro_prog["trace"].append(entry)


def _macro_step_delay(step):
    """步间延时 ms；非法值按 0 处理（上层已校验过，这里是二道闸）"""
    d = step.get("delay", 0)
    if isinstance(d, bool) or not isinstance(d, (int, float)):
        return 0
    return min(MACRO_MAX_DELAY, max(0, int(d)))


def validate_macro_steps(steps, name="宏"):
    """校验并原样返回 steps；任何不合法都抛 AdbError（调用方转 400）。
    在请求线程里同步校验：拼错的宏要当场 400 报错，不能等线程里悄悄失败。"""
    if not isinstance(steps, list) or not (1 <= len(steps) <= MACRO_MAX_STEPS):
        raise AdbError("宏步骤须为 1~{} 项的数组".format(MACRO_MAX_STEPS))
    for i, step in enumerate(steps):
        where = "宏「{}」第 {} 步".format(name, i + 1)
        if not isinstance(step, dict):
            raise AdbError("{}：步骤必须是对象".format(where))
        t = step.get("type") or "delay"   # 只带 delay 的步骤 = 纯等待
        if t not in MACRO_STEP_TYPES and t != "delay":
            raise AdbError("{}：类型 {} 不支持（可用 {}）".format(
                where, t, "/".join(MACRO_STEP_TYPES)))
        if t == "key":
            raw = step.get("codes") if "codes" in step else step.get("code")
            raw = [raw] if not isinstance(raw, list) else raw
            if not raw or len(raw) > MAX_KEYCODES:
                raise AdbError("{}：键码数量不合法".format(where))
            if not all(str(c).lstrip("-").isdigit() for c in raw):
                raise AdbError("{}：键码必须是数字".format(where))
        elif t == "text":
            text = step.get("text")
            if not isinstance(text, str) or not text.strip():
                raise AdbError("{}：文本为空".format(where))
            if len(text) > MAX_TEXT_LEN:
                raise AdbError("{}：文本超长（{}）".format(where, MAX_TEXT_LEN))
        elif t == "app":
            pkgs = ([step["pkg"]] if step.get("pkg") else []) + list(step.get("pkgs") or [])
            pkgs = [p for p in pkgs if isinstance(p, str) and APP_ID_RE.match(p)]
            if not pkgs:
                raise AdbError("{}：包名不合法".format(where))
            step["pkgs"] = pkgs
        _macro_step_delay(step)   # 触发上面的整形钳制
    return steps


def _macro_exec_step(step):
    """执行单步。app 支持 pkgs 多候选：Android/tvOS 包名不同，成功一个就停。"""
    t = step.get("type")
    if t == "app":
        last = None
        for p in step.get("pkgs") or [step.get("pkg")]:
            try:
                handle_cmd({"type": "app", "pkg": p})
                return
            except (AdbError, AppleTvError) as e:
                last = e
        if last:
            raise last
        return
    payload = {k: v for k, v in step.items() if k not in ("delay", "pkgs")}
    if "type" not in payload:
        # 纯延时步：等待在 _macro_step_delay 里已经做完了，没有可执行的命令。
        # 以前会漏进 handle_cmd({}) 报「Android TV 不支持该命令: None」——
        # trace 上每个延时步都变成一条假失败（预置宏全带延时步，等于全红）。
        return
    handle_cmd(payload)


def _macro_worker(name, steps):
    try:
        for i, step in enumerate(steps):
            if _macro_stop.is_set():
                _macro_mark(cancelled=True)
                _macro_trace(i + 1, 0, cancel=True)
                print("宏「{}」已取消（第 {} 步）".format(name, i), file=sys.stderr)
                return
            wait = _macro_step_delay(step) / 1000.0
            t0 = time.monotonic()
            if wait and _macro_stop.wait(wait):
                _macro_mark(cancelled=True)
                _macro_trace(i + 1, (time.monotonic() - t0) * 1000, cancel=True)
                print("宏「{}」在延时中被取消".format(name), file=sys.stderr)
                return
            _macro_mark(index=i + 1, name=name)
            try:
                _macro_exec_step(step)
            except (AdbError, AppleTvError) as e:
                # 单步失败不终止整条宏：比如「观影模式」里 Netflix 没装，
                # 后面的音量调整仍然该跑。失败原因既进服务端日志也进 trace。
                _macro_fail()
                _macro_trace(i + 1, (time.monotonic() - t0) * 1000, e)
                print("宏「{}」第 {} 步失败：{}".format(name, i + 1, e), file=sys.stderr)
            else:
                _macro_trace(i + 1, (time.monotonic() - t0) * 1000)
            _macro_mark(done=i + 1)
    except Exception:
        traceback.print_exc()
    finally:
        _macro_running.clear()


def handle_macro(body):
    global _macro_seq
    action = body.get("action", "run")
    if action == "cancel":
        _macro_stop.set()
        return {"ok": True, **macro_state()}
    if action != "run":
        raise AdbError("未知宏操作: {}（可用 run / cancel）".format(action))
    name = str(body.get("name", "") or "宏")[:24]
    steps = validate_macro_steps(body.get("steps"), name)
    if _macro_running.is_set():
        raise AdbError("已有一条宏在执行，等它跑完再试")
    _macro_stop.clear()
    with _macro_lock:
        _macro_seq += 1
        _macro_prog.update({"run": _macro_seq, "name": name, "total": len(steps),
                            "index": 0, "done": 0, "failed": 0, "cancelled": False,
                            "trace": []})
    _macro_running.set()
    threading.Thread(target=_macro_worker, args=(name, steps),
                     daemon=True, name="macro").start()
    return {"ok": True, **macro_state(), "steps": len(steps)}


def handle_macros(_body):
    return {"presets": PRESET_MACROS, **macro_state()}


# ===== wake-on-lan:begin =====
# 冷开机是遥控器唯一的真盲区：电视关机后 adb 直接掉线，设备列表里连这台都没有，
# 这时方向键 / 截屏 / 宏全都无从谈起。Wake-on-LAN（魔法包）是局域网内唤醒关机状态
# 主板的通用手段——电视的有线 / 无线网卡在关机后仍保持低功耗监听二层广播。
#
# 学习源：Linux 的 wakeonlan CLI 与 Home Assistant 的 wake_on_lan 集成。
# 移植两条方法论：
#   1) MAC 宽容解析——用户是从路由器后台 / 电视「关于」页手抄来的，冒号 / 连字符 /
#      点分 / 无分隔 / 大小写混用全都该收；解析不了要能说清是哪一段不对；
#   2) 失败必须可区分——socket 起不来、MAC 写法不合法、压根没在 ARP 表里发现它，
#      是三件不同的事，对应三种不同的用户动作（查防火墙 / 改地址 / 先开一次机）。
WOL_PORTS = (9, 7)                       # 9=discard（业界惯例），7=echo（部分固件只听 7）
WOL_BCAST_DEFAULT = "255.255.255.255"    # 受限广播：同一二层网络内必然可达
WOL_ARP_FILE = Path("/proc/net/arp")     # Linux / Android 的 ARP 表；macOS 没有，走 arp -an
WOL_IP_MAX = 20                          # 一次最多问这么多 IP（每个都在表里扫一遍）
WOL_MAC_MAX = 64                         # 单个入参长度上限，防超长串喂进正则
WOL_MAC_RE = re.compile(r"^[0-9a-f]{12}$")
WOL_IPV4_RE = re.compile(r"^[0-9]{1,3}([.][0-9]{1,3}){3}$")
# macOS arp -an：? (192.168.0.52) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]
WOL_ARP_LINE_RE = re.compile(r"[^ ]+ +[(]([0-9.]+)[)] +at +([0-9a-f:]{17})")


def wol_norm_mac(value):
    """宽容解析 MAC → aa:bb:cc:dd:ee:ff；解析不了返回 None（由调用方决定文案）。

    用户在路由器后台抄到的 MAC 什么格式都有：aa:bb:cc:dd:ee:ff / aa-bb-cc-dd-ee-ff /
    aabb.ccdd.eeff / AABBCCDDEEFF。这里全收，但不猜：位数不对就是 None，绝不去补位
    ——补出来的地址发出去只会唤醒另一台机器。"""
    if not isinstance(value, str):
        return None
    s = value.strip().lower().replace("-", ":").replace(".", ":")
    mac = "".join(p for p in s.split(":") if p)
    if not WOL_MAC_RE.match(mac):
        return None
    return ":".join(mac[i:i + 2] for i in range(0, 12, 2))


def wol_build_packet(mac):
    """标准魔法包：6 字节 0xFF + 目标 MAC 重复 16 遍，共 102 字节。"""
    return bytes((0xFF,)) * 6 + bytes.fromhex(mac.replace(":", "")) * 16


def wol_ip_of(target):
    """adb 无线调试目标带端口（192.168.0.52:5555）；ARP 表里只有 IP。"""
    return str(target if target is not None else "").split(":")[0].strip().lower()


def wol_parse_arp(text):
    """解析 ARP 表文本 → [(ip, mac)]。

    /proc/net/arp 每行 6 列：IP HWtype Flags MAC Mask Device。flags=0x0 的行是
    「已知此 IP 但没解析出 MAC」的占位，MAC 是全 0——不过滤它，前端就会冒出一排
    空 MAC 的按钮，点一个失败一个，而失败原因还查不出来。"""
    rows = []
    for line in (text or "").splitlines():
        f = line.split()
        if len(f) >= 6 and WOL_IPV4_RE.match(f[0]):
            if f[2] in ("0x0", "0"):        # 占位行，见 docstring
                continue
            mac = wol_norm_mac(f[3])
            if mac:
                rows.append((f[0], mac))
            continue
        m = WOL_ARP_LINE_RE.match(line)
        if m:
            mac = wol_norm_mac(m.group(2))
            if mac:
                rows.append((m.group(1), mac))
    return rows


def wol_read_arp():
    """读本机 ARP 表。读不到返回 []——发现不到不是错误（可能压根没通信过）。"""
    try:
        if WOL_ARP_FILE.is_file():
            return wol_parse_arp(WOL_ARP_FILE.read_text(errors="replace"))
    except OSError:
        pass
    exe = shutil.which("arp")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "-an"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    return wol_parse_arp(out.stdout)


def wol_targets(ips, rows):
    """只回答被问到且真查到了 MAC 的 IP；一条 IP 至多一行，顺序跟随询问顺序。"""
    want = []
    for ip in ips or []:
        if ip not in want:
            want.append(ip)
    return [{"ip": ip, "mac": m} for ip in want
            for m in [next((m for r, m in (rows or []) if r == ip), None)] if m]


def wol_ips(value):
    """ips 入参兼容三种形态：逗号串（GET）、数组（POST）、以及二者的嵌套——
    parse_qs 返回的 list 里躺着一条逗号串，不展开的话整串会被当成一个非法 IP 丢掉。"""
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, (list, tuple)):
        raw_items = list(value)
    else:
        return []
    ips = []
    for raw in raw_items:
        for part in str(raw).replace("，", ",").split(","):
            piece = part.split()      # 容忍 "192.168.0.52 5555" 这类带空格的粘贴
            if not piece or len(piece[0]) > WOL_MAC_MAX:
                continue
            ip = wol_ip_of(piece[0])
            if WOL_IPV4_RE.match(ip) and ip not in ips:
                ips.append(ip)
                if len(ips) >= WOL_IP_MAX:   # 到顶就收，别再往后扫
                    return ips
    return ips


def wol_send(mac, bcast, sock_factory=None):
    """往两个端口逐发广播包。单个 socket 失败只收集，全失败才抛 AdbError——
    与其在一条链路上抛一串异常，不如一次说清「两个端口都没发出去」。"""
    factory = sock_factory or socket.socket
    pkt = wol_build_packet(mac)
    sent, errs = [], []
    for port in WOL_PORTS:
        try:
            sk = factory(socket.AF_INET, socket.SOCK_DGRAM)
        except OSError as e:
            errs.append("端口 {} 建不了 socket：{}".format(port, e))
            continue
        try:
            sk.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sk.sendto(pkt, (bcast, port))
            sent.append(port)
        except OSError as e:
            errs.append("端口 {}：{}".format(port, e))
        finally:
            try:
                sk.close()
            except OSError:
                pass
    if not sent:
        raise AdbError("魔法包发送失败（{}）；常见原因是本机防火墙拦截 UDP 广播"
                       .format("，".join(errs) or "没有可用 socket"))
    return sent


def handle_volume_set(body):
    """POST /api/volume：把媒体音量设到绝对格数（第三十八轮，学 androidtv 0.0.75）。

    读通道（GET /api/volume）早就有了，但只能看不能设，想调到某一格只能连按音量键。
    这里补写通道：level 由滑条/预设档给出，缺失时 400，设备不对时 400，其余异常走
    do_POST 统一的异常映射。成功后返回实际格数，前端用它纠正「夹取后和我滑的不一样」。
    """
    body = body or {}
    if "level" not in body:
        raise AdbError("缺少 level 字段")
    target = current_android_target()
    return volume_set(target, body["level"])


def handle_wol(body):
    """discover：从本机 ARP 表反查 MAC（省得用户手抄）；send：发魔法包。"""
    body = body or {}
    action = str(body.get("action") or "discover")
    if action == "discover":
        ips = wol_ips(body.get("ips"))
        rows = wol_read_arp()
        return {"ok": True, "asked": ips, "targets": wol_targets(ips, rows)}
    if action == "send":
        mac = wol_norm_mac(body.get("mac"))
        if not mac:
            raise AdbError("MAC 地址不合法：{}（应为 aa:bb:cc:dd:ee:ff）"
                           .format(body.get("mac")))
        bcast = str(body.get("bcast") or WOL_BCAST_DEFAULT).strip()
        if not WOL_IPV4_RE.match(bcast):
            raise AdbError("广播地址不合法：{}".format(bcast))
        return {"ok": True, "mac": mac, "sent": wol_send(mac, bcast)}
    raise AdbError("未知操作: {}（可用 discover / send）".format(action))
# ===== wake-on-lan:end =====


# ---------------- HTTP ----------------
# 模块级路由表：不要每次 POST 都重建
ROUTES = {
    "/api/connect": handle_connect,
    "/api/disconnect": handle_disconnect,
    "/api/android/scan": handle_android_scan,
    "/api/android/pair": handle_android_pair,
    "/api/cmd": handle_cmd,
    "/api/ime": handle_ime,
    "/api/forget": handle_forget,
    "/api/switch": handle_switch,
    "/api/atv/scan": handle_atv_scan,
    "/api/atv/pair": handle_atv_pair,
    "/api/atv/connect": handle_atv_connect,
    "/api/atv/disconnect": handle_atv_disconnect,
    "/api/atv/forget": handle_atv_forget,
    "/api/atv/apps": handle_atv_apps,
    "/api/volume": handle_volume_set,
    "/api/wol": handle_wol,
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

    def _reachable_host(self) -> str:
        """本次连接本机这一侧的 host:port —— 客户端确实能连上的那个地址"""
        try:
            ip = self.request.getsockname()[0]
        except OSError:
            ip = lan_ip()
        if ip in ("0.0.0.0", "::", ""):
            ip = lan_ip()
        if ":" in ip and not ip.startswith("["):
            ip = "[" + ip + "]"
        return "{}:{}".format(ip, self.server.server_address[1])

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

    def _send(self, code, data, ctype="application/json; charset=utf-8", extra_headers=None):
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        # HEAD 只回头：Content-Length 必须是「本来会发的字节数」，但一个字节都不写，
        # 否则客户端会按长度等 body，等到超时（下载管理器普遍先发 HEAD 探测）。
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        # 令牌校验通过后种 cookie，后续请求（fetch / 静态资源 / APK）就不用再拼 ?token=
        if AUTH_TOKEN and not self._from_loopback() and getattr(self, "_issue_cookie", False):
            self.send_header("Set-Cookie", "{}={}; Path=/; SameSite=Lax; HttpOnly".format(
                TOKEN_COOKIE, AUTH_TOKEN))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if getattr(self, "_head_only", False):
            return
        self.wfile.write(body)

    def _body(self):
        raw = self.headers.get("Content-Length")
        try:
            n = int(raw) if raw else 0
        except ValueError:
            self.close_connection = True
            raise AdbError("Content-Length 不合法")
        if n <= 0:
            return {}
        if n > 100_000:
            # 声明的 body 太大时若直接返回、不把字节读干净，这条长连接上的下一个
            # 请求就会从半截 body 开始解析 —— 帧错位比报错严重得多。
            self.close_connection = True
            raise AdbError("请求体过大（{} 字节）".format(n))
        return json.loads(self.rfile.read(n).decode("utf-8", "replace") or "{}")

    # ---- GET ----
    def do_GET(self):
        self._serve(head_only=False)

    def do_HEAD(self):
        """只回头不回体。

        别小看它：下载管理器（浏览器内置的、IDM 一类）、以及「点链接后先探测」的
        浏览器都会先发 HEAD。以前没实现，服务器回 501 Unsupported method，
        表现就是「点了下载没反应 / 无法下载」——而直接敲 URL 又是好的，很难查。
        """
        self._serve(head_only=True)

    def _serve(self, head_only):
        path = self.path.split("?")[0]
        self._head_only = head_only
        if not self._check_auth():
            return
        try:
            if path in ("/", "/index.html"):
                html = (STATIC / "index.html").read_bytes()
                # 把令牌注入页面：前端要拼进安装命令与 API 请求
                html = html.replace(b"__ATV_TOKEN__",
                                    html_escape(AUTH_TOKEN, quote=True).encode())
                return self._send(200, html, "text/html; charset=utf-8")
            if path == "/api/status":
                return self._send(200, make_status())
            if path == "/api/perf":
                # 调用时间线快照（学 DevTools Network 面板）：最近 30s 的 adb/pyatv 调用
                payload = perf_snapshot()
                payload["adb"] = {
                    "version": adb.version() if adb else "",
                    "path": adb.path if adb else "",
                    "shell": bool(adb and adb._shell is not None and adb._shell.poll() is None),
                    "ttl": DEVICES_CACHE_TTL,
                }
                return self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            if path == "/api/diagnostics":
                # 一键体检（提案 E3）。鉴权已在 _serve 开头的 _check_auth 统一处理，这里
                # 不自己判权限；字段是 make_diagnostics() 白名单拼的，凭据不外泄。
                return self._send(200, make_diagnostics())
            if path == "/api/nowplaying":
                # 只覆盖 Android TV：Apple TV 的播放元数据依赖 pyatv 的 metadata.playing()，
                # 多数 App 不填，字段稀疏，暂不做（前端对该类型直接隐藏卡片）。
                with state_lock:
                    cur = dict(state.get("current") or {})
                if cur.get("type") != "android" or not cur.get("target"):
                    return self._send(200, {"connected": False})
                return self._send(200, now_playing(cur["target"]))
            if path == "/api/volume":
                # 只覆盖 Android TV：Apple TV 侧 pyatv 读不到音量（前端收 supported: False
                # 就走「图标模式」，照样给送达反馈，只是不出具体格数）
                with state_lock:
                    cur = dict(state.get("current") or {})
                if cur.get("type") != "android" or not cur.get("target"):
                    return self._send(200, {"connected": False})
                return self._send(200, volume(cur["target"]))
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
                host = (self.headers.get("Host") or "").strip()
                # Host 头可被伪造，而它会被拼进用户复制到手机里执行的 curl 命令：
                # 不合法就退回本机「被这次访问用到的那个地址」—— lan_ip() 走默认路由探测，
                # 有 VPN/代理网卡时会挑错网卡，给出一个手机根本连不上去的 IP。
                host_match = HOST_RE.match(host)
                if not host_match:
                    host = self._reachable_host()
                elif not host_match.group(2):
                    # Host 头没带端口（如反向代理、或手写 Host: [::1]）时补上真实监听端口，
                    # 否则生成的地址指向 80，手机照着 curl 一定连不上。
                    host = "{}:{}".format(host, self.server.server_address[1])
                script = (INSTALL_SCRIPT
                          .replace("__HOST__", "http://" + host)
                          .replace("__TOKENQ__", token_query()))
                return self._send(200, script.encode(), "text/plain; charset=utf-8")
            if path == "/bundle.tgz":
                return self._send(200, cached_bundle(), "application/gzip")
            if path == "/app.apk":
                # 优先发原生版（自带 Python 引擎 + adb 客户端，装完即用，不需要 Mac / Termux）；
                # 只有它不在时才退回 WebView 壳（那个必须 Mac 上跑着服务才能用）。
                # 注意：手机 App 内嵌引擎里这两个文件都不存在（APK 不会把自己装进自己），
                # 所以那种情况下页面会先隐藏这张卡片；真被请求到就给一句人话。
                apk = ROOT / "ATVRemote-native.apk"
                if not apk.is_file():
                    apk = ROOT / "android-native" / "ATVRemote-native.apk"
                if not apk.is_file():
                    apk = ROOT / "android" / "ATVRemote.apk"
                if not apk.is_file():
                    return self._send(404, {"error": ("APK 不存在：" +
                                            ("手机 App 里没有这个文件（已经装好了，不用再下载）；"
                                             if EMBEDDED else "") +
                                            "请在电脑上跑 android-native 的构建后重试")})
                # Content-Disposition 不能少：没有它，Chrome 会按 URL 最后一段命名
                # （app.apk），而且下载管理器把它当「网页」而不是「安装包」，
                # 手机上会出现「无法下载」/ 下成 .bin 之类。
                # 用 attachment 明确告诉浏览器「这是个要存盘的文件」。
                return self._send(200, apk.read_bytes(),
                                  "application/vnd.android.package-archive",
                                  {"Content-Disposition":
                                   'attachment; filename="ATVRemote.apk"'})
            if path == "/api/macros":
                return self._send(200, handle_macros(None))
            if path == "/api/wol":
                # GET 也要能发现：卡片可以只靠 GET 刷新，省掉一次 POST 的哨兵。
                # parse_qs 给的是 list，先 join 成逗号串——wol_ips 虽然也能吃嵌套，
                # 但「GET 侧的合法形态就是逗号串」这个口径写在这里更直白。
                q = parse_qs(urlparse(self.path).query)
                return self._send(200, handle_wol({
                    "action": (q.get("action") or ["discover"])[0],
                    "ips": ",".join(q.get("ips") or []),
                }))
            if path == "/api/setup":
                # 给已授权用户（通常是本机浏览器）看「手机首次接入」用的令牌和带令牌的
                # 页面地址，用于渲染二维码。能把令牌给已授权客户端是已知取舍：持令牌者
                # 本来就每次请求都带着它（cookie），并没有放大暴露面。
                # 二维码是给「另一台设备」扫的，回环地址对手机没意义，直接用局域网 IP
                # （与启动横幅里的「手机访问」同源；VPN 多网卡时同 lan_ip 的已知局限）
                return self._send(200, {
                    "token": AUTH_TOKEN,
                    "url": "http://{}:{}".format(lan_ip(), self.server.server_address[1]),
                })
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
                ctype = static_ctype(f)
                extra = None
                if f.name == "sw.js":
                    # SW 脚本默认只能控制自己所在目录（/static/）；带这个头才允许
                    # scope 放到 /，把整个 app shell 纳入离线壳，否则注册直接
                    # SecurityError、离线能力静默缺失
                    extra = {"Service-Worker-Allowed": "/"}
                return self._send(200, f.read_bytes(), ctype, extra_headers=extra)
            return self._send(404, {"error": "not found"})
        except (AdbError, AppleTvError) as e:
            return self._send(400, {"error": str(e)})
        except ConnectionError:
            pass  # 客户端半路断开（BrokenPipe / ConnectionReset），不是故障
        except Exception:
            # 堆栈只进 stderr（LaunchAgent 收进 server.log）：访问者拿不到内部细节，
            # 运维又能在出问题时看到根因。
            traceback.print_exc()
            return self._send(500, {"error": "服务器内部错误，请查看服务端日志"})

    # ---- POST ----
    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._check_auth():
            return
        fn = ROUTES.get(path)
        try:
            # 先读干净 body 再判 404：未匹配的路由也要把请求体消耗掉，
            # 否则这条长连接上的下一个请求会从半截 body 开始解析。
            body = self._body()
            if not isinstance(body, dict):
                # JSON 数组 / 标量：所有路由都按对象取字段，以前在这里炸
                # AttributeError 回 500（例如 POST /api/cmd 的 body 为 []）
                return self._send(400, {"error": "请求体必须是 JSON 对象"})
            if not fn:
                return self._send(404, {"error": "not found"})
            return self._send(200, fn(body) or {"ok": True})
        except json.JSONDecodeError:
            return self._send(400, {"error": "请求体不是合法 JSON"})
        except (AdbError, AppleTvError) as e:
            return self._send(400, {"error": str(e)})
        except ConnectionError:
            pass  # 客户端半路断开（BrokenPipe / ConnectionReset），不是故障
        except Exception:
            # 堆栈只进 stderr（LaunchAgent 收进 server.log）：访问者拿不到内部细节，
            # 运维又能在出问题时看到根因。
            traceback.print_exc()
            return self._send(500, {"error": "服务器内部错误，请查看服务端日志"})


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
    # 常见「不是局域网」的 IPv4 前缀：回环、APIPA 链路本地、运营商级 NAT，以及 TUN /
    # 代理客户端（Clash、Surge、OpenVPN 一类）默认租用的 198.18.0.0/15 假路由段。
    # 这些段一旦抢走默认路由，UDP 探测就会把「隧道自己的地址」当本机局域网 IP 回上来。
    _NOT_LAN_PREFIXES = ("127.", "169.254.", "100.64.", "198.18.", "198.19.", "0.")

    def is_lan(ip):
        """是否像「手机扫完二维码后真能连上」的那个地址"""
        return bool(ip) and ":" not in ip and not ip.startswith(_NOT_LAN_PREFIXES)

    def local_ipv4s():
        """枚举本机全部 IPv4，不依赖默认路由 —— VPN / 多网卡下也不会被骗。

        零第三方依赖：优先 SIOCGIFADDR（macOS 与 Linux 都有，常量同为 0xC0206921），
        拿不到再退回去解析 ifconfig / ip -o -4 addr 的文本输出。
        """
        out = []
        try:
            import fcntl
            import struct
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                for _, name in socket.if_nameindex():
                    try:
                        packed = struct.pack("256s", name.encode()[:15])
                        res = fcntl.ioctl(sock.fileno(), 0xC0206921, packed)
                    except OSError:
                        continue  # 无地址的虚拟接口：gif0 / stf0 / anpi* / 未启用的 utun*
                    ip = socket.inet_ntoa(res[20:24])
                    if ip and ip not in out:
                        out.append(ip)
            finally:
                sock.close()
        except Exception:
            pass
        if not out:
            for cmd in (["ifconfig"], ["ip", "-o", "-4", "addr"]):
                try:
                    txt = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=5).stdout or ""
                except Exception:
                    continue  # 命令不存在（Windows / 精简 PATH）就试下一条
                for m in re.finditer(r"(?:inet\s+|addr:)((?:\d{1,3}\.){3}\d{1,3})", txt):
                    ip = m.group(1)
                    if ip not in out:
                        out.append(ip)
                if out:
                    break
        return out

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        probed = s.getsockname()[0]
        s.close()
        # 无 VPN 时探测结果就是网卡地址，直接返回（历史行为不变）；
        # 只有探测结果根本不像局域网地址（TUN 抢走默认路由）才继续往下找。
        if is_lan(probed):
            return probed
    except Exception:
        probed = ""
    for ip in local_ipv4s():
        if is_lan(ip):
            return ip
    return probed or "本机局域网IP"


def start_mdns(port):
    """Bonjour 广播 _atv-remote._tcp，让局域网的「发现类」App 能找到本服务。
    dns-sd 只有 macOS 自带；Linux/Termux 没有就静默跳过 —— 广播只是可发现性增强，
    网页与 App 照旧按 IP/二维码访问，少了它不影响任何现有路径。"""
    exe = shutil.which("dns-sd")
    if not exe:
        print("  mDNS     : ⚠️ 未找到 dns-sd，跳过服务广播（不影响访问）")
        return None
    try:
        proc = subprocess.Popen(
            [exe, "-R", "ATV Remote", "_atv-remote._tcp", ".", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print("  mDNS     : ⚠️ 广播失败：{}（不影响访问）".format(e))
        return None
    print("  mDNS     : 已广播 _atv-remote._tcp :{}（局域网可发现，关窗口即停）".format(port))
    return proc


def main():
    global adb, AUTH_TOKEN
    ap = argparse.ArgumentParser(description="ATV Remote — Android TV / Apple TV 遥控器")
    ap.add_argument("--host", default="0.0.0.0",
                    help="默认 0.0.0.0（手机可直接访问）；只允许本机则传 127.0.0.1")
    ap.add_argument("--port", type=int, default=8300)
    ap.add_argument("--adb", default=os.environ.get("ADB_PATH", "adb"), help="adb 可执行文件路径")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    ap.add_argument("--token", default=None,
                    help="局域网访问令牌。绑 0.0.0.0/:: 且不传时首启自动生成"
                         "（存 state.json，重启不变）；传空串 = 显式不鉴权")
    ap.add_argument("--no-token", action="store_true",
                    help="关掉自动生成的令牌，恢复无鉴权（仅建议可信内网）")
    args = ap.parse_args()
    # 必须先 load_state()：resolve_token 靠 state["token"] 认出已生成过的令牌。反过来的话
    # 内存里永远读不到盘上的值，每轮启动都当「首启」重新生成，手机存的链接全失效
    # （表现为扫完码 / 打开页面 401，看着像「页面无法加载」）。
    load_state()
    AUTH_TOKEN = resolve_token(args.token, args.host, args.no_token, state, AUTH_TOKEN)
    if AUTH_TOKEN and str(state.get("token") or "") == AUTH_TOKEN:
        save_state()   # 首启生成的令牌必须落盘，否则重启就换、手机要重新配

    adb = Adb(resolve_adb(args.adb))
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=_sleep_timer_loop, daemon=True, name="sleep-timer").start()
    threading.Thread(target=_auto_reconnect_loop, daemon=True, name="auto-reconn").start()

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
            print("  🔒 令牌     : {}（{}）".format(
                AUTH_TOKEN, "首启自动生成，已存 state.json" if args.token is None else "来自 --token"))
            print("              局域网设备首次访问需输入令牌；网页「📱 装到手机」里有带令牌的二维码")
            print("              退回无鉴权：加 --no-token 启动（不推荐，同网段任何人都能遥控）")
    print("  Ctrl+C 停止")
    print("=" * 46)

    cur = state.get("current") or {}
    if cur.get("type") == "android" and cur.get("target"):
        print("  恢复连接 : {} (Android TV)".format(cur["target"]))
        try:
            adb.connect(cur["target"])
            auto_reconn_reset()
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
    mdns = start_mdns(args.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n再见！")
        httpd.server_close()
    finally:
        if mdns:
            mdns.terminate()


if __name__ == "__main__":
    main()
