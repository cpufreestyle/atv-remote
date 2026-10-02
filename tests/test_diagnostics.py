"""一键体检报告（第三十四轮）回归：脱敏 / 白名单形状 / 路由契约 / 前端契约 / harness。

立论是「报障时不该有来回追问」：用户说「连不上」，一份报告就该把版本、平台、adb 在不在、
设备在线状态、中文输入法三态讲清楚。而报告是会被原样贴进群和 issue 的，所以真正的红线
不在功能，而在**不外泄**——Apple TV 配对凭据和局域网令牌的值一个都不能进去。

实现学 Home Assistant 的 util/redact.py：把敏感键名显式列成集合，递归命中就整值替换
（常量沿用它的 REDACTED 原值）。但我们比 HA 再退一步：字段本来就是白名单现场拼的，
递归脱敏只是纵深防御，防的是将来谁手滑加了个名叫 token 的键。

只用标准库 + 读仓库文件 + 跑 node harness：不起端口、不碰真 adb、不联网。
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import server

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "diagnostics_harness.js"

TOP_KEYS = {"version", "embedded", "platform", "adb", "appletv", "current", "ime",
            "auth", "mdns"}
ADB_KEYS = {"found", "path", "version", "shell_alive", "devices_cache_ttl", "devices"}
FAKE_TOKEN = "SENTINEL-TOKEN-VALUE-7f3a"
FAKE_CREDS = "MRP_CREDENTIALS_BLOB_a91c"


class FakeAdb:
    """假 adb：只回答 make_diagnostics() 会问的那几个问题，绝不 fork 子进程。"""

    path = "/tmp/fakebin/adb"

    def __init__(self, devices=None):
        self._devices = [dict(d) for d in (devices if devices is not None
                                           else [{"serial": "192.168.1.5:5555",
                                                  "state": "device"}])]
        self._shell = None          # 常驻 shell 未起：poll() 无从谈起

    def exists(self):
        return True

    def version(self):
        return "Android Debug Bridge version 1.0.41"

    def devices(self):
        return [dict(d) for d in self._devices]


class MissingAdb(FakeAdb):
    """PATH 里没有 adb：临床上最常见的「连不上」原因之一。"""

    path = "/nonexistent/adb"

    def exists(self):
        return False

    def version(self):
        return ""

    def devices(self):
        raise server.AdbError("no adb")


class RedactTest(unittest.TestCase):
    """递归脱敏：对齐 HA 的口径，命中键整值替换、None/空串放行、不 mutate 输入。"""

    def test_top_level_hit_is_replaced(self):
        out = server.redact_diagnostics({"token": FAKE_TOKEN, "keep": "me"})
        self.assertEqual(out["token"], server.DIAG_REDACTED)
        self.assertEqual(out["keep"], "me")

    def test_nested_dict_and_list_are_recursed(self):
        data = {"a": {"password": "hunter2", "ok": 1},
                "b": [{"credentials": FAKE_CREDS}, {"fine": True}]}
        out = server.redact_diagnostics(data)
        self.assertEqual(out["a"]["password"], server.DIAG_REDACTED)
        self.assertEqual(out["a"]["ok"], 1)
        self.assertEqual(out["b"][0]["credentials"], server.DIAG_REDACTED)
        self.assertTrue(out["b"][1]["fine"])

    def test_none_and_empty_string_are_kept(self):
        # HA 的取舍：None 与空串不是值，替换了反而破坏形状（前端靠键是否存在分支）
        out = server.redact_diagnostics({"token": None, "secret": "", "password": ""})
        self.assertIsNone(out["token"])
        self.assertEqual(out["secret"], "")

    def test_non_string_sensitive_value_is_replaced(self):
        # token 类的键哪怕混进数字 / 子结构也不该出去
        out = server.redact_diagnostics({"token": 12345, "credentials": {"a": "b"},
                                         "private_key": ["k"]})
        for key in ("token", "credentials", "private_key"):
            self.assertEqual(out[key], server.DIAG_REDACTED)

    def test_input_is_not_mutated(self):
        data = {"token": FAKE_TOKEN, "n": {"password": "x"}}
        snapshot = json.dumps(data, sort_keys=True)
        server.redact_diagnostics(data)
        self.assertEqual(json.dumps(data, sort_keys=True), snapshot)

    def test_default_key_set_covers_known_leak_names(self):
        for key in ("token", "credentials", "pairing", "password", "secret",
                    "private_key", "authorization"):
            self.assertIn(key, server.DIAG_SENSITIVE_KEYS)
            self.assertEqual(server.redact_diagnostics({key: "v"})[key],
                             server.DIAG_REDACTED)

    def test_scalar_passthrough(self):
        # 递归的叶子：字符串 / 数字 / 布尔 / None 原样返回，别包一层
        for v in ("s", 1, 1.5, True, None):
            self.assertIs(server.redact_diagnostics(v), v)


class MakeDiagnosticsTest(unittest.TestCase):
    """make_diagnostics()：白名单字段现场拼，绝不整体序列化 state。"""

    def setUp(self):
        self._state = {
            "current": {"type": "android", "target": "192.168.1.5:5555"},
            "recent_android": [{"serial": "192.168.1.5:5555"}, {"serial": "a"},
                               {"serial": "b"}],
            "appletvs": [{"id": "4F5E:ATV", "name": "living room",
                          "credentials": FAKE_CREDS}],
            "info": {},
        }
        patchers = [
            mock.patch.object(server, "state", self._state),
            mock.patch.object(server, "adb", FakeAdb()),
            mock.patch.object(server, "ATV_AVAILABLE", True),
            mock.patch.object(server, "atv_mgr", mock.Mock(connected=False)),
            mock.patch.object(server, "AUTH_TOKEN", FAKE_TOKEN),
            mock.patch.object(server, "ime_status",
                              lambda serial: {"ime": "com.android.adbkeyboard/.AdbIME",
                                              "installed": True, "enabled": True,
                                              "current": True,
                                              "default_ime":
                                                  "com.android.adbkeyboard/.AdbIME",
                                              "apk": "https://example.com/a.apk",
                                              "apk_a16": "https://example.com/a16.apk"}),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def report(self):
        return server.make_diagnostics()

    def test_top_level_key_whitelist(self):
        self.assertEqual(set(self.report()), TOP_KEYS)

    def test_adb_subkey_whitelist(self):
        self.assertEqual(set(self.report()["adb"]), ADB_KEYS)

    def test_devices_only_expose_serial_and_state(self):
        devs = self.report()["adb"]["devices"]
        self.assertEqual(devs, [{"serial": "192.168.1.5:5555", "state": "device"}])
        for d in devs:
            self.assertEqual(set(d), {"serial", "state"})

    def test_paired_count_is_counted_not_serialized(self):
        # 已配对台数要报，但条目本身（含 credentials）不能进报告
        atv = self.report()["appletv"]
        self.assertEqual(atv["paired_count"], 1)
        self.assertTrue(atv["pyatv_available"])
        self.assertFalse(atv["connected"])

    def test_current_uses_target_for_android(self):
        cur = self.report()["current"]
        self.assertEqual(cur["type"], "android")
        self.assertEqual(cur["target"], "192.168.1.5:5555")
        self.assertEqual(cur["state"], "device")
        self.assertEqual(cur["recent_android_count"], 3)

    def test_current_uses_id_for_appletv(self):
        with mock.patch.object(server, "state",
                               {"current": {"type": "appletv", "id": "4F5E:ATV"},
                                "recent_android": [], "appletvs": []}):
            cur = self.report()["current"]
        self.assertEqual(cur["target"], "4F5E:ATV")
        self.assertIsNone(cur["state"])          # Apple TV 不在 adb devices 里

    def test_missing_adb_reports_not_found(self):
        with mock.patch.object(server, "adb", MissingAdb()):
            adb = self.report()["adb"]
        self.assertFalse(adb["found"])
        self.assertEqual(adb["version"], "")
        self.assertEqual(adb["devices"], [])
        self.assertFalse(adb["shell_alive"])

    def test_ime_only_queried_for_android(self):
        # Apple TV 时不该去打 ime_status（adb shell 很贵，且 ADBKeyboard 只属于 Android）
        with mock.patch.object(server, "state",
                               {"current": {"type": "appletv", "id": "X"},
                                "recent_android": [], "appletvs": []}):
            self.assertIsNone(self.report()["ime"])

    def test_credentials_and_token_never_appear(self):
        blob = json.dumps(self.report(), ensure_ascii=False)
        for secret in (FAKE_TOKEN, FAKE_CREDS, "living room"):
            self.assertNotIn(secret, blob)
        # 反证：哨兵确实在输入里，上面的断言不是 vacuously passed
        self.assertIn(FAKE_CREDS,
                      json.dumps(self._state["appletvs"], ensure_ascii=False))

    def test_auth_reports_switch_not_value(self):
        auth = self.report()["auth"]
        self.assertTrue(auth["token_enabled"])
        self.assertEqual(auth["mode"], "lan")
        self.assertEqual(set(auth), {"token_enabled", "mode", "loopback_hosts"})

    def test_no_token_means_mode_off(self):
        with mock.patch.object(server, "AUTH_TOKEN", ""):
            auth = self.report()["auth"]
        self.assertFalse(auth["token_enabled"])
        self.assertEqual(auth["mode"], "off")

    def test_report_is_json_serializable(self):
        # 前端 api() 走 JSON：报告里不能混进 set / Path 之类
        json.dumps(self.report(), ensure_ascii=False)


class RouteTest(unittest.TestCase):
    """/api/diagnostics：走统一 _send，不自己判权限、不裸写 socket。"""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "server.py").read_text(encoding="utf-8")

    def test_route_uses_shared_send(self):
        i = self.src.index('if path == "/api/diagnostics":')
        seg = self.src[i:self.src.index("if path == ", i + 10)]
        self.assertIn("make_diagnostics()", seg)
        self.assertIn("return self._send(200,", seg)
        self.assertNotIn("self.wfile.write", seg)
        # 精确到调用形式：注释里提一句「鉴权在上游统一处理」是允许的（那正是约定本身）
        self.assertNotIn("self._check_auth(", seg)

    def test_route_sits_next_to_perf(self):
        self.assertLess(self.src.index('if path == "/api/perf":'),
                        self.src.index('if path == "/api/diagnostics":'))

    def test_helpers_defined_above_the_route(self):
        self.assertLess(self.src.index("def redact_diagnostics("),
                        self.src.index("def make_diagnostics("))
        self.assertLess(self.src.index("def make_diagnostics("),
                        self.src.index('if path == "/api/diagnostics":'))


class FrontendContractTest(unittest.TestCase):
    """前端契约：白名单 JSON 变成人能读的纯函数、按钮在、选择器不漂移。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def seg(self):
        s = self.js.index("/* ===== diagnostics:begin")
        e = self.js.index("/* ===== diagnostics:end")
        return self.js[s:e]

    def test_pure_segment_touches_no_dom_or_network(self):
        seg = self.seg()
        # 与 node harness 同一套禁用词，外加本仓库自己的 api() 封装与 XHR；
        # 不含 "/api/" —— 那段注释里本来就要点名接口名，禁它只会逼人把注释写含糊
        for banned in ("document.", "localStorage", "fetch(", "XMLHttpRequest",
                       "setInterval", "innerHTML", "$(", "navigator.", "api("):
            self.assertNotIn(banned, seg)

    def test_pure_segment_has_all_helpers(self):
        seg = self.seg()
        for frag in ("function diagYes(v)", "function diagImeLine(ime)",
                     "function diagReportText(d)", "const devs = adb.devices || [];"):
            self.assertIn(frag, seg)

    def test_report_is_self_contained_lines(self):
        # join 自己拼行，不引用外部 NL 之类，抽段执行才成立
        self.assertIn('lines.join("\\n")', self.seg())

    def test_ime_line_names_the_real_culprit(self):
        # 中文打不进去的第一嫌疑是「没启用」，三态必须逐一报出来
        for frag in ("已装 ", "已启用 ", "当前 "):
            self.assertIn(frag, self.seg())

    def test_button_markup(self):
        self.assertIn('<div class="setrow"><span>一键体检</span>', self.html)
        self.assertIn('<button id="diagCopyBtn" class="btn tiny">复制诊断信息</button>',
                      self.html)

    def test_click_handler_wires_render_and_copy(self):
        i = self.js.index('$("#diagCopyBtn").addEventListener("click"')
        seg = self.js[i:self.js.index('$("#hapticBtn")', i)]
        self.assertIn('await api("/api/diagnostics")', seg)
        self.assertIn("sheetCopy(diagReportText(d))", seg)
        self.assertIn("toast(", seg)

    def test_selector_ids_do_not_drift(self):
        self.assertIn('id="diagCopyBtn"', self.html)
        self.assertIn('$("#diagCopyBtn")', self.js)


class HarnessBehaviorTest(unittest.TestCase):
    """真行为：node harness 从 app.js 抽出纯函数段原样执行，必须全绿。"""

    def test_harness_passes(self):
        if shutil.which("node") is None:
            self.skipTest("没有 node，跳过 harness")
        p = subprocess.run(["node", str(HARNESS)], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ALL_DIAG_CASES_PASSED", p.stdout)
        failed = re.findall('"failed"[^0-9]*([0-9]+)', p.stdout)
        total = re.findall('"total"[^0-9]*([0-9]+)', p.stdout)
        self.assertTrue(failed and failed[0] == "0", p.stdout)
        self.assertTrue(total and int(total[0]) >= 10, p.stdout)


if __name__ == "__main__":
    unittest.main()

