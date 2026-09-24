#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Android 无线调试扫描 / 配对 / 掉线自动重连的逻辑回归（不起真 adb）。

覆盖：`adb mdns services` 解析（配对与连接端口并到同一台、非 adb-tls 服务忽略）、
配对参数校验、自动重连状态机（从未在线不碰 / 掉线重试 / 连失败 3 次停 /
手动连接成功重新武装）。handle_connect 用例会把 STATE_FILE 指到临时文件 ——
仓库里的 state.json 含 Apple TV 配对凭据，绝不能被测试改写。
"""

import tempfile
import unittest
from pathlib import Path

import server

MDNS_OUT = """List of discovered mdns services
adb-tls-pairing._tcp._local.\t192.168.1.50:37123
adb-tls-connect._tcp._local.\t192.168.1.50:5555
adb-tls-connect._tcp._local.\t192.168.1.60:5555
adb-tls-connect._tcp._local.\t192.168.1.70:5555
someother._tcp._local.\t192.168.1.99:1234
"""


class _MdnsFakeAdb:
    """只认 mdns services / pair 两条命令"""

    def __init__(self, out=MDNS_OUT, pair_result="Successfully paired to 192.168.1.50:37123"):
        self.out = out
        self.pair_result = pair_result
        self.run_calls = []
        self.invalidated = False

    def run(self, *args, timeout=10, binary=False):
        self.run_calls.append(args)
        if args[0] == "mdns":
            return self.out
        if args[0] == "pair":
            return self.pair_result
        raise AssertionError("unexpected adb run: " + " ".join(args))

    def invalidate_devices(self):
        self.invalidated = True


class AndroidScanTest(unittest.TestCase):
    def setUp(self):
        self.saved = (server.adb, dict(server._auto_reconn))
        server.adb = _MdnsFakeAdb()

    def tearDown(self):
        server.adb, ar = self.saved
        server._auto_reconn.clear()
        server._auto_reconn.update(ar)

    def test_mdns_parsing_merges_pairing_and_connect(self):
        hosts = server._mdns_hosts()
        self.assertEqual(hosts, [
            {"host": "192.168.1.50", "pairing": 37123, "connect": 5555},
            {"host": "192.168.1.60", "pairing": None, "connect": 5555},
            {"host": "192.168.1.70", "pairing": None, "connect": 5555},
        ])

    def test_mdns_empty_when_nothing_broadcasts(self):
        server.adb.out = "List of discovered mdns services\n"
        self.assertEqual(server._mdns_hosts(), [])

    def test_pair_ok_and_validates(self):
        r = server.handle_android_pair({"host": "192.168.1.50", "port": 37123, "code": "123456"})
        self.assertTrue(r["ok"])
        self.assertTrue(server.adb.invalidated)
        for bad in ({"host": "192.168.1.50", "port": 0, "code": "123456"},
                    {"host": "192.168.1.50", "port": 37123, "code": "12345"},
                    {"host": "192.168.1.50", "port": 37123, "code": "abcdef"},
                    {"host": "bad host;x", "port": 37123, "code": "123456"},
                    {"host": "192.168.1.50", "port": "37123", "code": "123456"},
                    {"host": "192.168.1.50", "port": True, "code": "123456"}):
            with self.assertRaises(server.AdbError, msg=str(bad)):
                server.handle_android_pair(bad)

    def test_pair_failure_surfaces(self):
        server.adb.pair_result = "error: failed to pair"
        with self.assertRaises(server.AdbError):
            server.handle_android_pair({"host": "1.2.3.4", "port": 37123, "code": "123456"})


class _ReconnFakeAdb:
    """按脚本回放 devices() 状态，记录 connect 尝试"""

    def __init__(self, states, connect_exc=None):
        self.states = list(states)
        self.connect_exc = connect_exc
        self.connect_calls = 0

    def devices(self, fresh=False):
        return [{"serial": "tv", "state": self.states.pop(0)}]

    def reset_shell(self):
        pass

    def invalidate_devices(self):
        pass

    def connect(self, target):
        self.connect_calls += 1
        if self.connect_exc:
            raise self.connect_exc


class AutoReconnectTest(unittest.TestCase):
    def setUp(self):
        self.saved = (server.adb, dict(server._auto_reconn), dict(server.state))
        server._auto_reconn.update(was_online=False, fails=0, next_retry=0.0,
                                   active=False, stopped=False)
        with server.state_lock:
            server.state["current"] = {"type": "android", "target": "tv"}

    def tearDown(self):
        server.adb, ar, st = self.saved
        server._auto_reconn.clear()
        server._auto_reconn.update(ar)
        with server.state_lock:
            server.state.clear()
            server.state.update(st)

    def test_never_online_is_left_alone(self):
        """从未连上过的是用户还没连，后台不能上去就 adb connect"""
        fake = _ReconnFakeAdb(["offline"])
        server.adb = fake
        server._auto_reconnect_tick()
        self.assertEqual(fake.connect_calls, 0)

    def test_drop_then_success_rearms(self):
        fake = _ReconnFakeAdb(["device", "offline", "device"])
        server.adb = fake
        server._auto_reconn["was_online"] = True
        server._auto_reconnect_tick()
        server._auto_reconnect_tick()          # 掉了 → 自动重连一次
        self.assertEqual(fake.connect_calls, 1)
        self.assertFalse(server._auto_reconn["stopped"])
        server._auto_reconnect_tick()          # 又在线 → 计数清零
        self.assertEqual(server._auto_reconn["fails"], 0)

    def test_three_failures_then_stop(self):
        fake = _ReconnFakeAdb(["offline"] * 8, connect_exc=server.AdbError("boom"))
        server.adb = fake
        server._auto_reconn["was_online"] = True
        for _ in range(3):
            server._auto_reconn["next_retry"] = 0.0   # 绕过退避，专注次数上限
            server._auto_reconnect_tick()
        self.assertEqual(fake.connect_calls, 3)
        self.assertTrue(server._auto_reconn["stopped"])
        self.assertFalse(server._auto_reconn["active"])
        server._auto_reconnect_tick()
        self.assertEqual(fake.connect_calls, 3)       # 停了就不再试

    def test_non_android_current_is_ignored(self):
        fake = _ReconnFakeAdb(["offline"] * 4)
        server.adb = fake
        server._auto_reconn["was_online"] = True
        with server.state_lock:
            server.state["current"] = {"type": "appletv", "id": "x"}
        server._auto_reconnect_tick()
        self.assertEqual(fake.connect_calls, 0)


class _ConnectFakeAdb:
    def __init__(self):
        self.connect_calls = 0

    def connect(self, target):
        self.connect_calls += 1

    def invalidate_devices(self):
        pass

    def devices(self, fresh=False):
        return [{"serial": "192.168.1.50:5555", "state": "device"}]

    def shell(self, serial, cmd, timeout=6):
        return "brand\nmodel\n14\nPhysical size: 1920x1080"


class HandleConnectResetTest(unittest.TestCase):
    def setUp(self):
        self.saved = (server.adb, server.STATE_FILE, dict(server._auto_reconn),
                      dict(server.state))
        server.adb = _ConnectFakeAdb()
        self.tmp = tempfile.TemporaryDirectory()
        server.STATE_FILE = Path(self.tmp.name) / "state.json"
        server._auto_reconn.update(was_online=False, fails=2, stopped=True)
        server.state["recent_android"] = []

    def tearDown(self):
        server.adb, server.STATE_FILE, ar, st = self.saved
        self.tmp.cleanup()
        server._auto_reconn.clear()
        server._auto_reconn.update(ar)
        server.state.clear()
        server.state.update(st)

    def test_connect_resets_autoreconnect(self):
        r = server.handle_connect({"target": "192.168.1.50"})
        self.assertTrue(r["ok"])
        self.assertTrue(server._auto_reconn["was_online"])
        self.assertEqual(server._auto_reconn["fails"], 0)
        self.assertFalse(server._auto_reconn["stopped"])
        self.assertEqual(server.adb.connect_calls, 1)
        # 状态写到了临时文件，不是仓库里那份带配对凭据的 state.json
        self.assertTrue(server.STATE_FILE.exists())


if __name__ == "__main__":
    unittest.main()
