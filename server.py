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
        "version": app_version(),
        "auto_reconnect": {
            "active": _auto_reconn["active"],
            "stopped": _auto_reconn["stopped"],
            "fails": _auto_reconn["fails"],
        },
    }


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
RECONNECT_COOLDOWN = 30.0
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
        _auto_reconn["next_retry"] = time.time() + RECONNECT_COOLDOWN


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
        atv_mgr.swipe(body["x1"], body["y1"], body["x2"], body["y2"],
                      body.get("duration", 300))
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
set -e
echo "📱 ATV Remote 引擎安装中（3-8 分钟，仅需这一次）..."
pkg update -y >/dev/null 2>&1 || true
pkg install -y python clang libffi openssl android-tools
# 预编译 cryptography（避免本地编 rust）
pkg install -y tur-repo >/dev/null 2>&1 && pkg install -y python-cryptography || true
pip install --upgrade pip wheel >/dev/null
pip install "pyatv==0.18.0" || echo "⚠️ pyatv 安装失败（Apple TV 暂不可用，Android TV 正常），可稍后重试本命令"

# 拉取项目（含 start.sh；仅当 Mac 启用了 --token 时才一并同步配对记录）
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
_macro_running = threading.Event()
_macro_stop = threading.Event()

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
    return {"running": _macro_running.is_set()}


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
    handle_cmd(payload)


def _macro_worker(name, steps):
    try:
        for i, step in enumerate(steps):
            if _macro_stop.is_set():
                print("宏「{}」已取消（第 {} 步）".format(name, i), file=sys.stderr)
                return
            wait = _macro_step_delay(step) / 1000.0
            if wait and _macro_stop.wait(wait):
                print("宏「{}」在延时中被取消".format(name), file=sys.stderr)
                return
            try:
                _macro_exec_step(step)
            except (AdbError, AppleTvError) as e:
                # 单步失败不终止整条宏：比如「观影模式」里 Netflix 没装，
                # 后面的音量调整仍然该跑。失败原因进服务端日志。
                print("宏「{}」第 {} 步失败：{}".format(name, i + 1, e), file=sys.stderr)
    except Exception:
        traceback.print_exc()
    finally:
        _macro_running.clear()


def handle_macro(body):
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
    _macro_running.set()
    threading.Thread(target=_macro_worker, args=(name, steps),
                     daemon=True, name="macro").start()
    return {"ok": True, **macro_state(), "steps": len(steps)}


def handle_macros(_body):
    return {"presets": PRESET_MACROS, **macro_state()}


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
        path = self.path.split("?")[0]
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
                apk = ROOT / "android" / "ATVRemote.apk"
                if not apk.is_file():
                    return self._send(404, {"error": "APK 不存在，请先在电脑上执行 android/build.sh"})
                return self._send(200, apk.read_bytes(),
                                  "application/vnd.android.package-archive")
            if path == "/api/macros":
                return self._send(200, handle_macros(None))
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
    """取本机局域网 IP（不真正发包，仅用于显示）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "本机局域网IP"


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
    AUTH_TOKEN = resolve_token(args.token, args.host, args.no_token, state, AUTH_TOKEN)
    if AUTH_TOKEN and str(state.get("token") or "") == AUTH_TOKEN:
        save_state()   # 首启生成的令牌必须落盘，否则重启就换、手机要重新配

    adb = Adb(resolve_adb(args.adb))
    load_state()
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
