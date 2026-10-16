#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apple TV 后端 — 基于 pyatv（MediaRemote 协议，与 iOS「遥控器」App 同款）
提供：扫描 / PIN 配对 / 连接 / 按键 / 键盘文字输入 / 触摸滑动 / 应用 / 画面

依赖 .venv 中的 pyatv 0.18+，导入失败时由 server.py 优雅降级。
"""

import asyncio
import ipaddress
import os
import threading
import time
from concurrent import futures

try:
    import pyatv
    from pyatv import conf as atv_conf
    from pyatv.const import InputAction, OperatingSystem, Protocol
    from pyatv.exceptions import (AuthenticationError, BlockedStateError,
                                  CommandError, ConnectionFailedError,
                                  InvalidCredentialsError, InvalidStateError,
                                  NoCredentialsError, NotSupportedError,
                                  PairingError)
    try:
        from pyatv.exceptions import ConnectionLostError
        _CONN_LOST_ERRORS = (ConnectionFailedError, ConnectionLostError)
    except ImportError:  # pyatv < 0.14 无 ConnectionLostError
        _CONN_LOST_ERRORS = (ConnectionFailedError,)
    PYATV_AVAILABLE = True
except ImportError:  # venv / pyatv 不可用时禁用 Apple TV 功能
    PYATV_AVAILABLE = False

if PYATV_AVAILABLE:
    # 0.18 起 pyatv 异常没有公共基类，用元组统一捕获
    PYATV_ERRORS = (AuthenticationError, BlockedStateError, CommandError,
                    InvalidCredentialsError, InvalidStateError,
                    NoCredentialsError, NotSupportedError, PairingError)
else:
    PYATV_ERRORS = ()


# 上一条命令超时后的快速失败窗口：电视休眠时一条命令能占住遥控器锁 12s，
# 之后每条按键都排在那把锁后面干等。窗口内直接失败，用户立刻知道电视没响应。
FAST_FAIL_AFTER_TIMEOUT = 8.0


class AppleTvError(Exception):
    pass


class AppleTvConnError(AppleTvError):
    """连接断开类错误（可自动重连重试）"""


# Android 风格键码 → Apple TV 动作（与前端/Android 侧共用同一套键码）
# 旧 Apple TV（tvOS ≤15，广播 MRP）走 MRP；新 Apple TV（tvOS 16+，广播 Companion）走 Companion
KEY_MAP_MRP = {
    19: ("remote_control", "up"), 20: ("remote_control", "down"),
    21: ("remote_control", "left"), 22: ("remote_control", "right"),
    23: ("remote_control", "select"),
    4: ("remote_control", "menu"), 3: ("remote_control", "top_menu"),
    82: ("remote_control", "home"),
    85: ("remote_control", "play_pause"), 126: ("remote_control", "play"),
    127: ("remote_control", "pause"), 86: ("remote_control", "stop"),
    87: ("remote_control", "next"), 88: ("remote_control", "previous"),
    223: ("remote_control", "screensaver"),
    24: ("audio", "volume_up"), 25: ("audio", "volume_down"),
    26: ("power", "turn_off"), 224: ("power", "turn_on"),
    165: ("remote_control", "guide"),
}

KEY_MAP_COMPANION = {
    19: ("remote_control", "up"), 20: ("remote_control", "down"),
    21: ("remote_control", "left"), 22: ("remote_control", "right"),
    23: ("remote_control", "select"),
    4: ("remote_control", "menu"), 3: ("remote_control", "home"),
    82: ("remote_control", "control_center"),
    85: ("remote_control", "play_pause"), 126: ("remote_control", "play"),
    127: ("remote_control", "pause"), 86: ("remote_control", "pause"),
    87: ("remote_control", "next"), 88: ("remote_control", "previous"),
    92: ("remote_control", "skip_backward"), 93: ("remote_control", "skip_forward"),
    223: ("remote_control", "screensaver"), 165: ("remote_control", "guide"),
    24: ("remote_control", "volume_up"), 25: ("remote_control", "volume_down"),
    26: ("power", "turn_off"), 224: ("power", "turn_on"),
}


# ---------------- 调用时间线钩子 ----------------
# pyatv 调用的耗时由 server 侧统一记录（这里不能 import server：循环 import），
# server 启动时用 set_perf_recorder 把 recorder 注入进来；没注入就静默跳过。
_RECORDER = None


def set_perf_recorder(fn):
    global _RECORDER
    _RECORDER = fn


def friendly_error(msg: str) -> str:
    """把 pyatv 的原始异常文案翻成用户能动手的那句话。

    外提成模块级纯函数是为了可测：它原本是 AppleTvManager 的静态方法，而整个类
    定义在 `if PYATV_AVAILABLE` 之外、`_friendly` 却在类体里被 pyatv 缺失的早退路径
    一起吞掉（实测系统 python3 下 `dir(AppleTvManager)` 里没有 `_friendly`），于是
    「错误信息好不好懂」这条在无 pyatv 的环境里零覆盖。搬出来后不 import pyatv 也能测。
    """
    if not msg:
        return msg or ""
    low = msg.lower()
    if "credentials" in low or "auth" in low:
        return "需要配对或配对已失效：{}".format(msg)
    if "keyboard" in low:
        return "电视端没有聚焦的输入框：请先用方向键打开输入框，再发送文字"
    return msg


def _perf(label, ms, err=""):
    fn = _RECORDER
    if fn:
        try:
            fn("pyatv", label, ms, False, err)
        except Exception:
            pass



class KeyboardFocusListener:  # pylint: disable=too-few-public-methods
    """pyatv 键盘聚焦状态推送的接口实现。

    参考来源：反编译 macOS 系统 /usr/libexec/tvremoted（Apple TV 遥控的系统守护进程）得到的
    TVRXKeyboardControllerDelegate 协议——原生实现里聚焦变化全部是推送（beganTextEditing /
    didUpdateAttributes / didUpdateText / endedTextEditing），没有任何轮询。
    pyatv 对应的就是 KeyboardListener.focusstate_update。

    为什么不纯推送：监听器注册在连接建立时才能做（atv.keyboard 此时才存在），
    而断线重连、电视侧静默变化都可能让推送丢失。所以是“推送置值 + 轮询抵底”：
    推送到就更新缓存，轮询只在缓存过期时才真去问一次电视。
    """

    def __init__(self, on_update):
        self._on_update = on_update

    def focusstate_update(self, old_state, new_state) -> None:
        """pyatv 的回调：聚焦状态变化时立即通知。"""
        try:
            name = new_state.name if hasattr(new_state, "name") else str(new_state)
            self._on_update(name)
        except Exception:  # pylint: disable=broad-except
            # 推送回调里抛异常会拆 pyatv 的事件循环，必须吞掉
            pass


class AppleTvManager:
    """pyatv 的线程安全封装：后台 asyncio loop，HTTP 线程同步调用"""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run, daemon=True, name="atv-loop").start()
        self._atv = None            # 已连接的 pyatv interface.AppleTV
        self._dev = None            # 当前设备信息 dict（state 里的条目）
        self._key_map = KEY_MAP_MRP  # 按设备协议选择（MRP / Companion）
        self._pairing = None        # 进行中的 PairingHandler
        self._pair_proto = None     # 正在配对的协议名
        self._pair_dev = None       # 正在配对的设备条目
        self._lock = threading.RLock()
        self._last_timeout = 0.0    # 上次超时的时刻（0 = 很久以前 / 无）
        self._kb_focus = None       # 推送置值的聚焦态（None = 还没收到过）
        self._kb_listener = None    # 已注册的 KeyboardFocusListener

    # ---------------- loop 基础 ----------------
    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout=12, label=""):
        # 计时外壳：label 可显式指定（scan/connect…），缺省取协程函数名。
        # 异常一律记录后原样透传——不改调用方看到的错误，只让瀑布看得见。
        name = label or getattr(getattr(coro, "cr_code", None), "co_name", "") or "pyatv"
        t0 = time.monotonic()
        try:
            fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
            try:
                out = fut.result(timeout)
            except futures.TimeoutError:
                self._last_timeout = time.time()   # 供 _call 的快速失败窗口判断
                raise AppleTvError("Apple TV 响应超时（电视休眠？）")
            except _CONN_LOST_ERRORS as e:
                raise AppleTvConnError("连接已断开：{}".format(e))
            except PYATV_ERRORS as e:
                raise AppleTvError(self._friendly("{}: {}".format(type(e).__name__, e)))
            except OSError as e:
                raise AppleTvConnError("网络错误: {}".format(e))
            _perf(name, (time.monotonic() - t0) * 1000)
            return out
        except Exception as e:
            _perf(name, (time.monotonic() - t0) * 1000, str(e))
            raise

    @staticmethod
    def _friendly(msg: str) -> str:
        # 薄委托：真身在模块级 friendly_error()（那里可脱离 pyatv 测试）
        return friendly_error(msg)

    # ---------------- 扫描 ----------------
    async def _scan_coro(self, hosts):
        return await pyatv.scan(self.loop, timeout=5, hosts=hosts)

    def scan(self, hosts=None):
        """扫描局域网 Apple TV（保留支持 MRP 或 Companion 遥控协议的设备）"""
        found = self.run(self._scan_coro(hosts), label="scan")
        result = []
        for c in found:
            mrp = c.get_service(Protocol.MRP)
            comp = c.get_service(Protocol.Companion)
            if mrp is None and comp is None:
                continue  # HomePod / 第三方电视等不能遥控的设备
            if c.device_info.operating_system != OperatingSystem.TvOS:
                continue  # Mac 等也会广播 Companion，只保留 Apple TV
            ap = c.get_service(Protocol.AirPlay)
            result.append({
                "id": c.identifier,
                "name": c.name,
                "ip": str(c.address),
                "proto": "mrp" if mrp else "companion",
                "mrp_port": mrp.port if mrp else None,
                "companion_port": comp.port if comp else None,
                "airplay_port": ap.port if ap else None,
            })
        return result

    # ---------------- 连接 ----------------
    async def _config_coro(self, dev):
        configs = await pyatv.scan(self.loop, timeout=5, hosts=[dev["ip"]])
        for c in configs:
            if c.identifier == dev["id"]:
                for proto, cred_key in ((Protocol.MRP, "mrp_cred"),
                                        (Protocol.Companion, "companion_cred"),
                                        (Protocol.AirPlay, "airplay_cred")):
                    svc = c.get_service(proto)
                    if svc and dev.get(cred_key):
                        svc.credentials = dev[cred_key]
                return c
        return None

    async def _connect_coro(self, cfg):
        return await pyatv.connect(cfg, self.loop)

    def connect(self, dev):
        """dev: state['appletvs'] 中的条目（含 id/ip/凭据）"""
        with self._lock:
            self._close_locked()
            cfg = self.run(self._config_coro(dev), label="config")
            if cfg is None:
                raise AppleTvError("找不到 {}（{}）：请确认电视已唤醒且与本机同网段".format(dev.get("name"), dev.get("ip")))
            if not (dev.get("mrp_cred") or dev.get("companion_cred")):
                raise AppleTvError("尚未配对：请先点击「配对」")
            # 按设备广播的协议选择按键映射（新 tvOS 只有 Companion）
            self._key_map = (KEY_MAP_MRP if cfg.get_service(Protocol.MRP)
                             else KEY_MAP_COMPANION)
            self._atv = self.run(self._connect_coro(cfg), label="connect")
            self._dev = dict(dev)
            self._register_keyboard_listener_locked()

    def disconnect(self):
        with self._lock:
            self._close_locked()

    def _close_locked(self):
        if self._atv is not None:
            # 先拆推送监听器再关连接：关闭过程中 pyatv 仍然可能往回调里写值
            self._unregister_keyboard_listener_locked()
            try:
                self.run(self._atv.close(), timeout=5, label="close")
            except Exception:
                pass
            self._atv = None
            self._dev = None

    @property
    def connected(self):
        with self._lock:
            return self._atv is not None

    def current_device(self):
        with self._lock:
            return dict(self._dev) if self._dev else None

    def _require(self):
        atv = self._atv
        if atv is None:
            raise AppleTvError("未连接 Apple TV")
        return atv

    def _call(self, coro_factory):
        """执行命令；连接断开类错误自动重连重试一次"""
        # 进锁之前先看快速失败窗口：超时刚发生过就别再去锁上排 12s 队
        # （锁横跨 run() 的等待，这是「第二键被第一键堵住」的直接原因）
        if self._last_timeout and time.time() - self._last_timeout < FAST_FAIL_AFTER_TIMEOUT:
            raise AppleTvError("上一条命令无响应（电视休眠？），稍等几秒再试")
        with self._lock:
            try:
                result = self.run(coro_factory(self._require()))
            except AppleTvConnError as e:
                if self._dev is None:
                    raise
                dev = dict(self._dev)
                self._close_locked()
                try:
                    self.connect(dev)
                except AppleTvError:
                    raise e  # 重连失败 → 返回原始连接错误
                result = self.run(coro_factory(self._require()))
        self._last_timeout = 0.0   # 成功过就解除快速失败，别让一次抖动毒化后续
        return result

    # ---------------- 配对 ----------------
    def pair_begin(self, dev):
        """开始配对：电视屏幕会显示 PIN 码（按设备广播的协议选 MRP/Companion）"""
        with self._lock:
            self.pair_stop()
            cfg = self.run(self._config_coro({**dev, "mrp_cred": None, "companion_cred": None,
                                              "airplay_cred": None}))
            if cfg is None:
                raise AppleTvError("找不到 {}（{}），无法配对".format(dev.get("name"), dev.get("ip")))
            proto = Protocol.MRP if cfg.get_service(Protocol.MRP) else Protocol.Companion

            async def _pair_coro(cfg):
                return await pyatv.pair(cfg, proto, self.loop)
            handler = self.run(_pair_coro(cfg))
            self.run(handler.begin(), timeout=25)
            self._pairing = handler
            self._pair_proto = proto.name  # "MRP" / "Companion"
            self._pair_dev = dict(dev)

    def pair_finish(self, pin: str):
        with self._lock:
            if self._pairing is None:
                raise AppleTvError("没有进行中的配对，请先点「配对」")
            pin = str(pin).strip()
            if not (pin and pin.isdigit() and len(pin) == 4):
                raise AppleTvError("请输入电视屏幕上显示的 4 位 PIN 码")
            try:
                self._pairing.pin(int(pin))
                self.run(self._pairing.finish(), timeout=20)
                creds = self._pairing.service.credentials
                if not creds:
                    raise AppleTvError("配对未返回凭据，请重试")
                cred_key = "mrp_cred" if self._pair_proto == "MRP" else "companion_cred"
                self._pair_dev[cred_key] = creds
                self._pair_dev["proto"] = self._pair_proto.lower()
                return dict(self._pair_dev)
            except AppleTvError:
                raise
            except Exception as e:
                raise AppleTvError("配对失败（PIN 码不对？请重新配对）：{}".format(e))

    def pair_stop(self):
        if self._pairing is not None:
            try:
                self.run(self._pairing.close(), timeout=5)
            except Exception:
                pass
            self._pairing = None
            self._pair_proto = None
            self._pair_dev = None

    # ---------------- 遥控命令 ----------------
    def send_keys(self, codes, hold=False):
        """发按键。

        hold=True 时用 InputAction.Hold（按住一秒再松）而不是默认的 SingleTap。
        两者语义不同：前端的长按连发是快速点击多次（滚列表），
        而 Hold 能触发电视端自己的“长按菜单”（如长按 OK 弹上下文菜单）——
        这个能力点击多少次都换不来。

        协议差异：MRP（旧转发器）不指 Hold，安装时会抛异常，那就回到 SingleTap。
        """
        for code in codes:
            code = int(code)
            action = self._key_map.get(code)
            if action is None:
                raise AppleTvError("Apple TV 不支持键码 {}".format(code))
            iface_name, method = action

            def factory(_n=iface_name, _m=method, _h=hold):
                def build(atv):
                    iface = getattr(atv, _n)
                    fn = getattr(iface, _m)
                    if _h:
                        # MRP 旧转发器不指 Hold：抛异常就回到点击，
                        # 否则“长按”在那些设备上会直接失效。
                        try:
                            return fn(InputAction.Hold)
                        except Exception:  # pylint: disable=broad-except
                            return fn()
                    return fn()
                return build
            self._call(factory)

    def send_text(self, text, enter=False, append=False):
        """向电视端输入框发送文本。

        append=只有 Companion 协议（tvOS 16+）支持：它的 text_append 是追加语义，
        而 text_set 是整段替换——搞干清空副本就有的旧关键词。
        Android 侧 input text 同样是追加，所以默认走追加才能两端一致。
        MRP（旧转发器）没有 Keyboard 实现，没有 text_append，安装时会抛
        NotSupportedError——那就回到 text_set（至少还能用）。
        """
        text = str(text)
        if not text:
            raise AppleTvError("内容为空")

        def factory(atv):
            async def _do():
                kb = atv.keyboard
                sender = kb.text_append if append else kb.text_set
                try:
                    await sender(text)
                except Exception:
                    if not append:
                        raise
                    # 追加不支持（MRP / 旧 pyatv）：退回替换语义，不让整条发送失败
                    await kb.text_set(text)
                if enter:
                    await atv.remote_control.select()
            return _do()
        self._call(factory)

    def tap(self):
        def factory(atv):
            if not hasattr(atv, "touch"):
                raise AppleTvError("此 pyatv 版本不支持触控，请用方向键 + OK")
            return atv.touch.click(InputAction.SingleTap)
        self._call(factory)

    def swipe(self, x1, y1, x2, y2, duration_ms):
        # 归一化坐标 0.0 ~ 1.0
        def clamp(v):
            return min(1.0, max(0.0, float(v)))
        def factory(atv):
            if not hasattr(atv, "touch"):
                raise AppleTvError("此 pyatv 版本不支持触控，请用方向键 + OK")
            return atv.touch.swipe(clamp(x1), clamp(y1), clamp(x2), clamp(y2),
                                   min(2000, max(100, int(duration_ms))))
        self._call(factory)

    # ---------------- 应用 / 画面 ----------------
    def apps(self):
        def factory(atv):
            return atv.apps.app_list()
        apps = self._call(factory) or []
        return [{"id": a.identifier, "name": a.name} for a in apps]

    def launch_app(self, bundle):
        def factory(atv):
            return atv.apps.launch_app(bundle)
        self._call(factory)

    def artwork(self):
        """返回 (bytes, mimetype)：正在播放内容的画面（非截屏）"""
        def factory(atv):
            return atv.metadata.artwork(640)
        info = self._call(factory)
        if info is None or not info.bytes:
            raise AppleTvError("当前没有可获取的画面（无内容播放或已休眠）")
        return info.bytes, info.mimetype or "image/png"


    def _register_keyboard_listener_locked(self):
        """连接建立后把聚焦推送接进来（名字带 locked 是因为只能在持锁时调）。

        不支持推送的协议（MRP 旧转发器）安 listener 时会抛异常，那就安上也没用——
        吞掉异常继续跑，抵底还有 keyboard_focus() 的轮询。"""
        if self._atv is None:
            return
        try:
            kb = self._atv.keyboard
            if kb is None:
                return
            self._kb_listener = KeyboardFocusListener(self._on_kb_focus_push)
            kb.listener = self._kb_listener
        except Exception:  # pylint: disable=broad-except
            self._kb_listener = None

    def _unregister_keyboard_listener_locked(self):
        """断开时把监听器拆下来；不拆的话旧连接的回调会继续往新缓存里写。"""
        if self._kb_listener is not None and self._atv is not None:
            try:
                kb = self._atv.keyboard
                if kb is not None and getattr(kb, "listener", None) is self._kb_listener:
                    kb.listener = None
            except Exception:  # pylint: disable=broad-except
                pass
        self._kb_listener = None
        self._kb_focus = None

    def _on_kb_focus_push(self, name):
        """pyatv 推送到的聚焦态：只更新缓存，不做任何 adb / 网络开销。

        这个回调跑在 pyatv 的 asyncio 线程上，而 keyboard_focus() 可能同时被 HTTP 线程调用——
        两边都持锁，否则会读到写一半的值。"""
        with self._lock:
            self._kb_focus = name

    def keyboard_focus(self):
        """电视端输入框聚焦状态：Focused / Unfocused / Unknown

        推送值优先：收到过 focusstate_update 就直接返回，不再往电视发一次命令。
        没收到过（新连接 / 协议不支持推送）才回到轮询，抵底仍然可用。"""
        with self._lock:
            pushed = self._kb_focus
        if pushed:
            return pushed
        def factory(atv):
            async def _do():
                return atv.keyboard.text_focus_state
            return _do()
        try:
            st = self._call(factory)
            name = st.name if hasattr(st, "name") else str(st)
            with self._lock:
                self._kb_focus = name   # 轮询结果也写进缓存，下次就不用再问
            return name
        except Exception:
            return "Unknown"


if not PYATV_AVAILABLE:
    # Mac 与 Termux 装 pyatv 的命令不一样：Termux 里没有 .venv。按平台给出能直接
    # 粘贴执行的那一条，别让用户拿着 Mac 的命令去 Android 上跑。
    _IS_TERMUX = os.path.isdir("/data/data/com.termux/files/usr")

    def _pyatv_hint():
        if _IS_TERMUX:
            return "在 Termux 里执行：pip install pyatv==0.18.0"
        return "在 ~/atv-remote 下执行：.venv/bin/pip install pyatv（或用 .venv/bin/python 启动）"

    class AppleTvManager:  # noqa: F811 — 降级占位
        connected = False

        def scan(self, hosts=None):
            raise AppleTvError("pyatv 未安装，" + _pyatv_hint())

        def pair_begin(self, dev):
            raise AppleTvError("pyatv 未安装，" + _pyatv_hint())

        def pair_finish(self, pin):
            raise AppleTvError("pyatv 未安装，" + _pyatv_hint())

        def pair_stop(self):
            pass

        def connect(self, dev):
            raise AppleTvError("pyatv 未安装，" + _pyatv_hint())

        def disconnect(self):
            pass

        def current_device(self):
            return None

        def send_keys(self, codes, hold=False):
            raise AppleTvError("pyatv 未安装，" + _pyatv_hint())

        def send_text(self, text, enter=False, append=False):
            raise AppleTvError("pyatv 未安装，" + _pyatv_hint())

        def tap(self):
            raise AppleTvError("pyatv 未安装，" + _pyatv_hint())

        def swipe(self, *a, **kw):
            raise AppleTvError("pyatv 未安装，" + _pyatv_hint())

        def apps(self):
            raise AppleTvError("pyatv 未安装，" + _pyatv_hint())

        def launch_app(self, bundle):
            raise AppleTvError("pyatv 未安装，" + _pyatv_hint())

        def artwork(self):
            raise AppleTvError("pyatv 未安装，" + _pyatv_hint())

        def keyboard_focus(self):
            return "Unknown"
