#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apple TV 后端 — 基于 pyatv（MediaRemote 协议，与 iOS「遥控器」App 同款）
提供：扫描 / PIN 配对 / 连接 / 按键 / 键盘文字输入 / 触摸滑动 / 应用 / 画面

依赖 .venv 中的 pyatv 0.18+，导入失败时由 server.py 优雅降级。
"""

import asyncio
import ipaddress
import threading
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

    # ---------------- loop 基础 ----------------
    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro, timeout=12):
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout)
        except futures.TimeoutError:
            raise AppleTvError("Apple TV 响应超时（电视休眠？）")
        except _CONN_LOST_ERRORS as e:
            raise AppleTvConnError("连接已断开：{}".format(e))
        except PYATV_ERRORS as e:
            raise AppleTvError(self._friendly("{}: {}".format(type(e).__name__, e)))
        except OSError as e:
            raise AppleTvConnError("网络错误: {}".format(e))

    @staticmethod
    def _friendly(msg: str) -> str:
        if "credentials" in msg.lower() or "auth" in msg.lower():
            return "需要配对或配对已失效：{}".format(msg)
        if "keyboard" in msg.lower():
            return "电视端没有聚焦的输入框：请先用方向键打开输入框，再发送文字"
        return msg

    # ---------------- 扫描 ----------------
    async def _scan_coro(self, hosts):
        return await pyatv.scan(self.loop, timeout=5, hosts=hosts)

    def scan(self, hosts=None):
        """扫描局域网 Apple TV（保留支持 MRP 或 Companion 遥控协议的设备）"""
        found = self.run(self._scan_coro(hosts))
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
            cfg = self.run(self._config_coro(dev))
            if cfg is None:
                raise AppleTvError("找不到 {}（{}）：请确认电视已唤醒且与本机同网段".format(dev.get("name"), dev.get("ip")))
            if not (dev.get("mrp_cred") or dev.get("companion_cred")):
                raise AppleTvError("尚未配对：请先点击「配对」")
            # 按设备广播的协议选择按键映射（新 tvOS 只有 Companion）
            self._key_map = (KEY_MAP_MRP if cfg.get_service(Protocol.MRP)
                             else KEY_MAP_COMPANION)
            self._atv = self.run(self._connect_coro(cfg))
            self._dev = dict(dev)

    def disconnect(self):
        with self._lock:
            self._close_locked()

    def _close_locked(self):
        if self._atv is not None:
            try:
                self.run(self._atv.close(), timeout=5)
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
        with self._lock:
            try:
                return self.run(coro_factory(self._require()))
            except AppleTvConnError as e:
                if self._dev is None:
                    raise
                dev = dict(self._dev)
                self._close_locked()
                try:
                    self.connect(dev)
                except AppleTvError:
                    raise e  # 重连失败 → 返回原始连接错误
                return self.run(coro_factory(self._require()))

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
    def send_keys(self, codes):
        for code in codes:
            code = int(code)
            action = self._key_map.get(code)
            if action is None:
                raise AppleTvError("Apple TV 不支持键码 {}".format(code))
            iface_name, method = action
            self._call(lambda atv, _n=iface_name, _m=method: getattr(getattr(atv, _n), _m)())

    def send_text(self, text, enter=False):
        text = str(text)
        if not text:
            raise AppleTvError("内容为空")

        def factory(atv):
            async def _do():
                await atv.keyboard.text_set(text)
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

    def keyboard_focus(self):
        """电视端输入框聚焦状态：Focused / Unfocused / Unknown"""
        def factory(atv):
            async def _do():
                return atv.keyboard.text_focus_state
            return _do()
        try:
            st = self._call(factory)
            return st.name if hasattr(st, "name") else str(st)
        except Exception:
            return "Unknown"


if not PYATV_AVAILABLE:
    class AppleTvManager:  # noqa: F811 — 降级占位
        connected = False

        def scan(self, hosts=None):
            raise AppleTvError("pyatv 未安装：请在 ~/atv-remote 下执行 "
                               ".venv/bin/pip install pyatv（或用 .venv/bin/python 启动）")

        def pair_begin(self, dev):
            raise AppleTvError("pyatv 未安装")

        def pair_finish(self, pin):
            raise AppleTvError("pyatv 未安装")

        def pair_stop(self):
            pass

        def connect(self, dev):
            raise AppleTvError("pyatv 未安装")

        def disconnect(self):
            pass

        def current_device(self):
            return None

        def send_keys(self, codes):
            raise AppleTvError("pyatv 未安装")

        def send_text(self, text, enter=False):
            raise AppleTvError("pyatv 未安装")

        def tap(self):
            raise AppleTvError("pyatv 未安装")

        def swipe(self, *a, **kw):
            raise AppleTvError("pyatv 未安装")

        def apps(self):
            raise AppleTvError("pyatv 未安装")

        def launch_app(self, bundle):
            raise AppleTvError("pyatv 未安装")

        def artwork(self):
            raise AppleTvError("pyatv 未安装")

        def keyboard_focus(self):
            return "Unknown"
