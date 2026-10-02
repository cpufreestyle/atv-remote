#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音量精确设置（按格）——第三十八轮回归。

动机：音量链路此前是「按键 + 只读 OSD」。OSD 只能看，想从 3 格调到 12 格得连按
9 次，还容易按过头；老 ROM 连级数都读不到，用户根本不知道现在几格。

学习源：androidtv 0.0.75（MIT，Copyright (c) 2020 Jeff Irion，commit
343b74ea7bb3d159f8a715190b4b9d8c00c2c0fd）—— Home Assistant 遥控 Android TV 的协议库。
三条口径（详见 docs/opensource-references.md 第 13 节）：
  1) constants.py:145 / :148 两条 set 命令；
  2) basetv_async.py:830 的 int(min(max(round(x), 0.0), max_volume))：先 round 后夹；
  3) basetv_async.py:825-828：max 拿不到再读一次、仍拿不到就返回 None（放弃）。

这里只验可离线复现的部分，不起真 adb、不碰局域网：
  1) 规则段（volset:begin/end）能搬进 node，行为口径与服务端逐值对齐；
  2) 服务端 clamp / 两条命令的有序降级 / 读通道全废时安静报错；
  3) DOM 胶水在段外，HTML/CSS 接线齐全；
  4) 文档里的学习源出处可 grep（引错行比不引更糟）。

跑法：python3 -m unittest tests.test_volume_set -v
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import server

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "volume_slider_harness.js"
DOCS = ROOT / "docs" / "opensource-references.md"

BEGIN = "/* ===== volset:begin"
END = "/* ===== volset:end"
MIN_CASES = 10

# 文档里必须能 grep 到的出处（写错行号等于没写）
SOURCES = [
    "constants.py:145",
    "constants.py:148",
    "basetv_async.py:830",
    "set_volume_level",
    "Copyright (c) 2020 Jeff Irion",
    "343b74ea7bb3d159f8a715190b4b9d8c00c2c0fd",
    "media volume --show --stream 3 --set",
    "cmd media_session volume --show --stream 3 --set",
]

GLUE = ["volSetPaint", "volSetSync", "volSetCommit", "volSetRenderPresets", "volSetRetarget"]


def _strip(text):
    return re.sub(r"/\*[\s\S]*?\*/", " ", re.sub(r"//[^\n]*", " ", text))


class SegmentTest(unittest.TestCase):
    """规则段必须是可搬进 node 的纯函数。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_markers_unique(self):
        self.assertEqual(self.js.count(BEGIN), 1, "begin 必须唯一")
        self.assertEqual(self.js.count(END), 1, "end 必须唯一")
        self.assertLess(self.js.index(BEGIN), self.js.index(END))

    def test_pure_segment_has_no_global_state(self):
        seg = _strip(self.js[self.js.index(BEGIN):self.js.index(END)])
        for banned in ["document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$(", "api("]:
            self.assertNotIn(banned, seg, "纯函数段里出现 " + banned)

    def test_helpers_live_inside_segment(self):
        seg = self.js[self.js.index(BEGIN):self.js.index(END)]
        for fn in ["volSetClamp", "volSetPct", "volSetPresets", "volSetReconcile",
                   "volSetUsable", "VOLSET_MAX_FALLBACK"]:
            self.assertIn(fn, seg, fn + " 必须定义在纯函数段内")

    def test_glue_lives_outside_segment(self):
        for fn in GLUE:
            self.assertGreater(self.js.index("function " + fn), self.js.index(END),
                               fn + " 摸 DOM，必须放在段外")

    def test_harness_green(self):
        if shutil.which("node") is None:
            self.skipTest("没有 node")
        r = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALL_VOLUME_CASES_PASSED", r.stdout)
        total = json.loads(r.stdout.strip().splitlines()[-2])["total"]
        self.assertGreaterEqual(total, MIN_CASES)


class JsPythonAlignmentTest(unittest.TestCase):
    """前端夹取口径与服务端逐值对齐（夹出 16 / -1 电视侧直接报错）。"""

    def test_fallback_constants_match(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        m = re.search(r"VOLSET_MAX_FALLBACK = (\d+);", js)
        self.assertIsNotNone(m, "前端兜底格数必须显式写成常量")
        self.assertEqual(int(m.group(1)), server.VOLUME_MAX_FALLBACK)

    def test_two_set_commands_match(self):
        # 前端注释里抄的两条命令必须和服务端 volume_set_cmd 生成的一致
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        for cmd in server.volume_set_cmd(8):
            self.assertIn(cmd.split(" --show")[0], js, cmd)

    def test_python_half_up_not_bankers(self):
        # Python3 内建 round 是银行家舍入（8.5→8），JS Math.round 是 8.5→9；
        # 服务端必须显式半点向上，否则「滑条停在 9、电视设成 8」
        self.assertEqual(round(8.5), 8, "本用例的前提：内建 round 确实是银行家舍入")
        self.assertEqual(server.clamp_volume_level(8.5, 15)[0], 9)
        self.assertEqual(server.clamp_volume_level(7.5, 15)[0], 8)


class ClampTest(unittest.TestCase):
    def test_round_then_clamp(self):
        self.assertEqual(server.clamp_volume_level(7.6, 15), (8, 15))
        self.assertEqual(server.clamp_volume_level(0.4, 15), (0, 15))

    def test_out_of_range(self):
        self.assertEqual(server.clamp_volume_level(99, 15), (15, 15))
        self.assertEqual(server.clamp_volume_level(-4, 15), (0, 15))
        self.assertEqual(server.clamp_volume_level(99, 25), (25, 25))

    def test_untrusted_max_falls_back(self):
        for bad in (None, 0, -1, "", "abc"):
            self.assertEqual(server.clamp_volume_level(5, bad), (5, server.VOLUME_MAX_FALLBACK),
                             "max=%r 必须退到兜底值" % (bad,))

    def test_untrusted_level_is_zero_not_exception(self):
        for bad in (None, "abc", "", float("nan")):
            self.assertEqual(server.clamp_volume_level(bad, 15)[0], 0, "level=%r" % (bad,))


class FakeSetAdb:
    """按命令文本回包的 adb 替身。set_ok: both / first_only / second_only / none。

    first_only  = 只有第一条（media volume）成，第二条挂；
    second_only = 只有第二条（cmd media_session volume）成，第一条挂——
                  老 ROM 不认 media volume 的真实降级路径。"""

    def __init__(self, media=None, audio=None, set_ok="both"):
        self.calls, self.sets = [], []
        self._media, self._audio, self._set_ok = media, audio, set_ok

    def shell(self, serial, cmd, timeout=5):
        self.calls.append(cmd)
        if "--set" in cmd:
            self.sets.append(cmd)
            if self._set_ok == "none":
                raise server.AdbError("media: not found")
            if self._set_ok == "first_only" and "cmd media_session" in cmd:
                raise server.AdbError("cmd: not found")
            if self._set_ok == "second_only" and "media volume" in cmd:
                raise server.AdbError("media volume: not found")
            return "Volume set to 8\n"
        if "media volume" in cmd:
            if self._media is None:
                raise server.AdbError("/system/bin/sh: media: not found")
            return self._media
        if self._audio is None:
            raise server.AdbError("device offline")
        return self._audio


MV = "volume is 7 in range [0..15]\n"
DUMP25 = """
  - STREAM_MUSIC:
      Max: 25
      Current: 12, Latest: 12
"""


class VolumeSetTest(unittest.TestCase):
    def setUp(self):
        self.saved = server.adb
        self.saved_cache = dict(server._volume_cache)
        server._volume_cache.update(ts=0.0, serial=None, val=None)

    def tearDown(self):
        server.adb = self.saved
        server._volume_cache.update(**self.saved_cache)

    def test_happy_path_and_cache_invalidated(self):
        server.adb = FakeSetAdb(media=MV)
        r = server.volume_set("1.2.3.4:5555", 8)
        self.assertTrue(r["ok"])
        self.assertEqual(r["level"], 8)
        self.assertEqual(r["max"], 15)
        self.assertEqual(server.adb.sets,
                         ["media volume --show --stream 3 --set 8"])
        # 音量是被我们亲手改掉的真状态：不清缓存下一次读会返回旧格数
        self.assertEqual(server._volume_cache["ts"], 0.0)

    def test_second_command_fallback(self):
        # 旧 ROM 不认 media volume，Android 11+ 的 cmd media_session volume 兜底
        server.adb = FakeSetAdb(media=MV, set_ok="second_only")
        r = server.volume_set("1.2.3.4:5555", 8)
        self.assertEqual(r["cmd"], "cmd media_session volume --show --stream 3 --set 8")
        self.assertEqual(len(server.adb.sets), 2)

    def test_both_commands_fail_raises(self):
        server.adb = FakeSetAdb(media=MV, set_ok="none")
        with self.assertRaises(server.AdbError):
            server.volume_set("1.2.3.4:5555", 8)
        self.assertEqual(len(server.adb.sets), 2, "两条都得试过才放弃")

    def test_read_channel_dead_never_sends(self):
        # 读通道全废（两个查询命令都不认）：报错让前端退回音量键，不猜格数
        server.adb = FakeSetAdb()
        with self.assertRaises(server.AdbError):
            server.volume_set("1.2.3.4:5555", 8)
        self.assertEqual(server.adb.sets, [], "读不到 max 时不许发 set 命令")

    def test_clamps_before_sending(self):
        for asked, sent in ((99, 15), (-4, 0), (7.6, 8)):
            server._volume_cache.update(ts=0.0, serial=None, val=None)
            server.adb = FakeSetAdb(media=MV)
            r = server.volume_set("1.2.3.4:5555", asked)
            self.assertEqual(r["level"], sent, "asked=%r" % (asked,))
            self.assertEqual(server.adb.sets,
                             ["media volume --show --stream 3 --set %d" % sent])

    def test_dumpsys_fallback_max_respected(self):
        # media 通道没有、dumpsys 报 25 格：99 该夹成 25，不是兜底的 15
        server.adb = FakeSetAdb(media=None, audio=DUMP25)
        r = server.volume_set("1.2.3.4:5555", 99)
        self.assertEqual((r["level"], r["max"]), (25, 25))
        self.assertEqual(server.adb.sets, ["media volume --show --stream 3 --set 25"])


class HandleVolumeSetTest(unittest.TestCase):
    def setUp(self):
        self.saved_state = server.state
        self.saved_adb = server.adb
        self.saved_cache = dict(server._volume_cache)
        server.state = {"recent_android": [], "appletvs": [], "current": None}
        server._volume_cache.update(ts=0.0, serial=None, val=None)

    def tearDown(self):
        server.state = self.saved_state
        server.adb = self.saved_adb
        server._volume_cache.update(**self.saved_cache)

    def test_missing_level_field(self):
        with self.assertRaises(server.AdbError):
            server.handle_volume_set({})

    def test_not_android(self):
        server.state["current"] = {"type": "appletv", "id": "aa:bb"}
        with self.assertRaises(server.AdbError):
            server.handle_volume_set({"level": 8})

    def test_no_current(self):
        with self.assertRaises(server.AdbError):
            server.handle_volume_set({"level": 8})

    def test_android_connected(self):
        server.state["current"] = {"type": "android", "target": "1.2.3.4:5555"}
        server.adb = FakeSetAdb(media=MV)
        r = server.handle_volume_set({"level": 8})
        self.assertEqual(r["level"], 8)
        self.assertEqual(server.adb.sets, ["media volume --show --stream 3 --set 8"])

    def test_route_registered(self):
        # POST 走 do_POST 的 ROUTES 分发（鉴权/body/异常映射已统一，此处不重复判权限）
        self.assertIn("/api/volume", server.ROUTES)
        self.assertIs(server.ROUTES["/api/volume"], server.handle_volume_set)


class WiringTest(unittest.TestCase):
    """HTML / CSS / 胶水接线：缺一样滑条就是个死控件。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_html_has_all_nodes(self):
        for attr in ['id="volSetRow"', 'id="volMuteBtn"', 'id="volSlider"',
                     'id="volSliderFill"', 'id="volSetNum"', 'id="volPresets"',
                     'id="volSetHint"']:
            self.assertIn(attr, self.html, attr)
        self.assertIn('type="range"', self.html)
        self.assertIn('min="0"', self.html)
        self.assertIn('step="1"', self.html)

    def test_slider_inside_tools_card(self):
        card = self.html[self.html.index('id="toolsCard"'):]
        card = card[:card.index('</section>')]
        self.assertIn('id="volSlider"', card, "滑条得在工具卡片里，别漂到别的卡片")

    def test_css_has_slider_rules(self):
        for sel in [".vrow", ".vsliderwrap", ".vslider", ".vsliderfill", ".vpresets"]:
            self.assertIn(sel + " ", self.css, sel)
        self.assertIn(".vpresets .btn.on", self.css)
        self.assertIn("appearance: none", self.css, "原生 range 不吃系统配色的部分要自己画")

    def test_dragging_guard_wired(self):
        # 拖动过程每帧一次 adb 会把输入锁堵死：input 只画本地，change 才提交
        block = self.js[self.js.index("const slider = document.querySelector(\"#volSlider\")")::]
        block = block[:block.index("\n}\n\n/* Apple TV 控件 */")]
        self.assertIn('addEventListener("input"', block)
        self.assertIn('addEventListener("change"', block)
        self.assertIn("VolSet.dragging = true;", block)
        self.assertIn('addEventListener("click"', block, "静音按钮要有 click")

    def test_sync_hooked_into_status_polling(self):
        seg = self.js[self.js.index("volRetarget((s.cur_type ||"):]
        seg = seg[:seg.index("\n    } catch (e) {")]
        self.assertIn("volSetRetarget();", seg)
        self.assertIn('if (status.curType === "android" && status.connected) volSetSync();', seg)

    def test_drag_commit_hits_post_route(self):
        # volSetCommit 的实际写法：const r = await api("/api/volume", { level: c[0] });
        self.assertIn('await api("/api/volume", { level: c[0] });', self.js)
        # 回读通道（GET）：volSetSync 里的 await api("/api/volume");
        self.assertIn('await api("/api/volume");', self.js)

    def test_no_innerhtml_for_device_data(self):
        # 预设按钮的文字是数字/档位，仍走 textContent：这条规矩不因内容简单而放松
        self.assertIn("b.textContent = n === 0 ?", self.js)


class DocsProvenanceTest(unittest.TestCase):
    """文档结论必须能 grep 到出处。"""

    @classmethod
    def setUpClass(cls):
        if not DOCS.exists():
            raise unittest.SkipTest("docs/opensource-references.md 不存在")
        cls.doc = DOCS.read_text(encoding="utf-8")

    def test_thirty_eighth_section_exists(self):
        self.assertIn("第三十八轮补记", self.doc)

    def test_sources_are_citable(self):
        for s in SOURCES:
            self.assertIn(s, self.doc, "文档缺少出处 " + s)

    def test_three_rules_documented(self):
        for s in ["先 round 后夹", "有序降级", "退回按键"]:
            self.assertIn(s, self.doc, "三条口径之一没写进文档：" + s)


if __name__ == "__main__":
    unittest.main()
