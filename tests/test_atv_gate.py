#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apple TV 页签能力门禁（I24）回归。

动机：native APK 走 -PnoAppletv 降级构建（chaquo 仓库的 cryptography 最高 42.0.8，
pyatv 0.18.0 的 >=44.0.1 无 Android wheel 可装，见 harness.md 4.12 / D3），
/api/status 的 appletv.available 恒为 false。🍎 页签过去照常渲染「扫描」按钮，
用户点了只拿到空列表或 pyatv 报错——小米 K90 真机上的表现就是「无法点到 Apple TV」。
门禁（static/app.js 的 atv-gate 纯函数段 + applyAtvGate DOM 胶水）把死路变成一句人话。

验四件事（标准库 + 读仓库文件 + node harness，离线可跑）：
  1) 纯函数段可搬进 node：atvGateState 对 available 三态（true/false/缺失）的翻转
     有真行为证据（tests/atv_gate_harness.js 经 subprocess 驱动）；
  2) 判据单一：只认 status.appletv.available === true，truthy 值、设备列表非空、
     status 整个缺失都按不可用处理，且不抛异常；
  3) DOM 胶水在段外：applyAtvGate 存在、显隐只走 .hidden、三要素
     （#atvScanBrow / #atvUnsupported / #atvList）一个不漏；
  4) 调用点齐：renderStatus 与 🍎 页签点击分支都调 applyAtvGate——
     漏任何一处，用户切页签时看到的还是旧显隐。
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
APP = STATIC / "app.js"
INDEX = STATIC / "index.html"
HARNESS = Path(__file__).resolve().parent / "atv_gate_harness.js"
SEG_BEGIN = "/* ===== atv-gate:begin"
SEG_END = "/* ===== atv-gate:end"


class AtvGateHarnessTest(unittest.TestCase):
    """纯函数段经 node harness 跑真行为。"""

    def test_harness_cases_pass(self):
        r = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALL_ATV_GATE_CASES_PASSED", r.stdout)


class AtvGateStructureTest(unittest.TestCase):
    """段标记、DOM 胶水、调用点、HTML 要素的结构守护。"""

    @classmethod
    def setUpClass(cls):
        cls.js = APP.read_text(encoding="utf-8")
        cls.html = INDEX.read_text(encoding="utf-8")

    def test_segment_markers_exist(self):
        self.assertIn(SEG_BEGIN, self.js)
        self.assertIn(SEG_END, self.js)
        self.assertLess(self.js.index(SEG_BEGIN), self.js.index(SEG_END),
                        "段标记顺序颠倒")

    def test_glue_outside_segment(self):
        seg = self.js[self.js.index(SEG_BEGIN):self.js.index(SEG_END)]
        self.assertNotIn("applyAtvGate", seg, "DOM 胶水不许混进纯函数段")
        self.assertIn("function applyAtvGate(", self.js, "段外要有 DOM 胶水")

    def test_glue_toggles_only_hidden(self):
        m = re.search(r"function applyAtvGate\(s\) \{[\s\S]*?\n\}", self.js)
        self.assertIsNotNone(m, "applyAtvGate 没找到")
        body = m.group(0)
        for elid in ("atvScanBrow", "atvUnsupported", "atvList"):
            self.assertIn(elid, body, "门禁漏了要素 " + elid)
        self.assertIn('classList.toggle("hidden"', body, "显隐只走 .hidden")
        self.assertIn("atvGateState(s)", body, "判据必须来自纯函数段")

    def test_call_sites_cover_render_and_tab_click(self):
        # 定义 1 处 + renderStatus 1 处 + 🍎 页签点击分支 1 处
        self.assertGreaterEqual(self.js.count("applyAtvGate("), 3,
                                "renderStatus 与页签点击分支都要调 applyAtvGate")
        render = self.js[self.js.index("function renderStatus(s)"):
                         self.js.index("function renderStatus(s)") + 400]
        self.assertIn("applyAtvGate(s)", render, "renderStatus 里要调")

    def test_html_has_gate_elements(self):
        pane = self.html[self.html.index('id="pane-appletv"'):]
        pane = pane[:pane.index("</header>")]
        for elid in ("atvScanBrow", "atvUnsupported", "atvList"):
            self.assertIn('id="%s"' % elid, pane, "pane-appletv 里缺 " + elid)
        self.assertIn('id="atvUnsupported" class="atvnote hidden"', self.html,
                      "提示默认隐藏，available=true 时不出现")
