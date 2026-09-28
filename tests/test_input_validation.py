#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""畸形 body 的输入校验回归：这些用例以前都回 500「服务器内部错误」。

对应 2026-09-25 Lumi 客户端交接的 bug2（connect 必须带真实设备 id）以及服务端自查：
缺字段 / 坏值必须在 handler 里挡成 400 + 中文提示，而不是让 KeyError / ValueError
一路逃到 do_POST 的兜底分支。同一类里还包括：appletv 键码不过滤、坐标裸取、
手势时长 int() 强转、整个 body 不是 JSON 对象。

跑法：python3 -m unittest discover -s tests   （或 ./check.sh）
不依赖 adb / pyatv / 局域网：adb 与 atv_mgr 都换假件，服务是真 ThreadingHTTPServer，
起在随机端口上；假件的强转口径（send_keys 走 int()、swipe 走 float()）与真实包装一致，
所以漏校验照样会炸，不会因为换桩而把 bug 藏起来。
"""

import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server


class _FakeAdb:
    """只实现设备状态查询与 run_shell 用到的方法，并记下发过的命令"""

    def __init__(self):
        self.cmds = []

    def devices(self, fresh=False):
        return [{"serial": "fake-tv", "state": "device"}]

    def reset_shell(self):
        pass

    def invalidate_devices(self):
        pass

    def shell(self, serial, cmd, timeout=6):
        self.cmds.append(cmd)
        if cmd == "dumpsys power":
            return "mWakefulness=Awake"
        return ""


class _FakeMgr:
    """强转口径与真实 pyatv 包装一致：send_keys 走 int()、swipe 走 float()"""

    connected = True

    def __init__(self):
        self.keys = []
        self.swipes = []

    def connect(self, dev):
        pass

    def current_device(self):
        return {"id": "fake"}

    def send_keys(self, codes):
        self.keys.append([int(c) for c in codes])

    def tap(self):
        self.keys.append("tap")

    def swipe(self, x1, y1, x2, y2, dur):
        self.swipes.append(tuple(float(v) for v in (x1, y1, x2, y2)) + (int(dur),))

    def launch_app(self, pkg):
        pass


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)


class InputValidationTest(unittest.TestCase):
    def setUp(self):
        self.saved = (server.adb, server.atv_mgr, server.state.get("current"))
        server.adb = _FakeAdb()
        server.atv_mgr = _FakeMgr()
        self.httpd = _Server(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        (server.adb, server.atv_mgr, server.state["current"]) = self.saved

    # ---------- 工具 ----------
    def url(self, path):
        return "http://127.0.0.1:{}{}".format(self.port, path)

    def set_current(self, kind):
        with server.state_lock:
            server.state["current"] = ({"type": "appletv", "id": "fake"} if kind == "appletv"
                                       else {"type": "android", "target": "fake-tv"})

    def post(self, path, payload):
        req = Request(self.url(path), data=json.dumps(payload).encode(),
                      method="POST", headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=10) as r:
                return r.getcode(), json.loads(r.read() or b"{}")
        except HTTPError as e:
            body = e.read()
            e.close()   # 不关掉会在基线输出里刷 ResourceWarning
            return e.code, json.loads(body or b"{}")

    def post_raw(self, path, raw):
        """绕过 json.dumps：直接送非对象 JSON（数组 / 标量）"""
        req = Request(self.url(path), data=raw, method="POST",
                      headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=10) as r:
                return r.getcode(), json.loads(r.read() or b"{}")
        except HTTPError as e:
            body = e.read()
            e.close()
            return e.code, json.loads(body or b"{}")

    def assert_bad(self, path, payload, needle):
        """畸形 body 的统一口径：400 + 中文提示，绝不是 500"""
        code, data = self.post(path, payload)
        self.assertEqual(code, 400, "{} {}".format(code, data))
        self.assertIn(needle, data.get("error", ""))
        return data

    # ---------- /api/atv/connect：设备入口必须带真实 id ----------
    def test_atv_connect_empty_body(self):
        self.assert_bad("/api/atv/connect", {}, "缺少设备信息（id/ip）")

    def test_atv_connect_ip_without_id(self):
        self.assert_bad("/api/atv/connect", {"ip": "10.0.0.9"}, "缺少设备信息（id/ip）")

    # ---------- Apple TV 按键 ----------
    def test_atv_key_nondigit_codes(self):
        self.set_current("appletv")
        self.assert_bad("/api/cmd", {"type": "key", "codes": ["abc"]}, "键码不合法")

    def test_atv_key_null_code(self):
        self.set_current("appletv")
        self.assert_bad("/api/cmd", {"type": "key", "code": None}, "键码不合法")

    def test_atv_tap_without_coords_is_ok(self):
        """基线别破：Apple TV 的 tap 本来就不带坐标，等价轻点"""
        self.set_current("appletv")
        code, _ = self.post("/api/cmd", {"type": "tap"})
        self.assertEqual(code, 200)

    # ---------- Apple TV 手势 ----------
    def test_atv_swipe_missing_coords(self):
        self.set_current("appletv")
        self.assert_bad("/api/cmd", {"type": "swipe"}, "缺少坐标参数 x1")

    def test_atv_swipe_garbage_coord(self):
        self.set_current("appletv")
        self.assert_bad("/api/cmd", {"type": "swipe", "x1": "a", "y1": 0.5,
                                    "x2": 0.1, "y2": 0.2}, "坐标不合法")

    def test_atv_swipe_garbage_duration(self):
        self.set_current("appletv")
        self.assert_bad("/api/cmd", {"type": "swipe", "x1": 0.1, "y1": 0.1,
                                    "x2": 0.2, "y2": 0.2, "duration": "abc"},
                        "手势时长不合法")

    def test_atv_swipe_normalized_ok(self):
        self.set_current("appletv")
        code, _ = self.post("/api/cmd", {"type": "swipe", "x1": 0.1, "y1": 0.2,
                                        "x2": 0.3, "y2": 0.4})
        self.assertEqual(code, 200)
        self.assertEqual(server.atv_mgr.swipes, [(0.1, 0.2, 0.3, 0.4, 300)])

    def test_atv_swipe_out_of_range_still_clamps(self):
        """越界归一化坐标照旧夹到边界，不新增拒绝路径（与 atv_backend 内部一致）"""
        self.set_current("appletv")
        code, _ = self.post("/api/cmd", {"type": "swipe", "x1": 1.5, "y1": -0.2,
                                        "x2": 0.3, "y2": 0.4, "duration": 99999})
        self.assertEqual(code, 200)
        self.assertEqual(server.atv_mgr.swipes, [(1.0, 0.0, 0.3, 0.4, 2000)])

    # ---------- Android TV 手势 ----------
    def test_android_tap_missing_coords(self):
        self.set_current("android")
        self.assert_bad("/api/cmd", {"type": "tap"}, "缺少坐标参数 x")

    def test_android_tap_garbage_coord(self):
        self.set_current("android")
        self.assert_bad("/api/cmd", {"type": "tap", "x": "zz", "y": 0}, "坐标不合法")

    def test_android_tap_ok(self):
        self.set_current("android")
        code, _ = self.post("/api/cmd", {"type": "tap", "x": 100, "y": 200})
        self.assertEqual(code, 200)
        self.assertIn("input tap 100 200", server.adb.cmds)

    def test_android_swipe_missing_coords(self):
        self.set_current("android")
        self.assert_bad("/api/cmd", {"type": "swipe"}, "缺少坐标参数 x1")

    def test_android_swipe_garbage_duration(self):
        self.set_current("android")
        self.assert_bad("/api/cmd", {"type": "swipe", "x1": 1, "y1": 2, "x2": 3,
                                    "y2": 4, "duration": "abc"}, "手势时长不合法")

    def test_android_swipe_ok_clamps_duration(self):
        self.set_current("android")
        code, _ = self.post("/api/cmd", {"type": "swipe", "x1": 1, "y1": 2,
                                        "x2": 3, "y2": 4, "duration": 99999})
        self.assertEqual(code, 200)
        self.assertIn("input swipe 1 2 3 4 2000", server.adb.cmds)

    # ---------- Android TV 按键（含本来就正确的基线） ----------
    def test_android_key_null_code(self):
        self.set_current("android")
        self.assert_bad("/api/cmd", {"type": "key", "code": None}, "键码不合法")

    def test_android_key_mixed_codes_filtered(self):
        self.set_current("android")
        code, data = self.post("/api/cmd", {"type": "key",
                                          "codes": [3, "4", None, "abc"]})
        self.assertEqual(code, 200, data)
        self.assertEqual(data.get("sent"), [3, 4])
        self.assertEqual(server.atv_mgr.keys, [])
        self.assertIn("input keyevent 3 4", server.adb.cmds)

    # ---------- 整个 body 不是 JSON 对象 ----------
    def test_non_dict_body_array(self):
        self.set_current("android")
        code, data = self.post_raw("/api/cmd", b"[]")
        self.assertEqual(code, 400, data)
        self.assertIn("JSON 对象", data.get("error", ""))

    def test_non_dict_body_scalar(self):
        self.set_current("android")
        code, data = self.post_raw("/api/cmd", b"5")
        self.assertEqual(code, 400, data)
        self.assertIn("JSON 对象", data.get("error", ""))


if __name__ == "__main__":
    unittest.main()
