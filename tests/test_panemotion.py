#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""窗格转场与触控目标（第二十五轮）回归。

动机：Playwright 审计（双视口 390x844 / 1280x800）实测两项问题——tabpane / card
零 transition / animation，点「遥控 / 触摸板」整块瞬间硬切；#palBtn 33x32、
#appSettingsBtn 29x32、#notifBtn 37x34 三个图标按钮低于 36px 触控目标。本轮学
Material 3 motion 的短进场与 WCAG 2.2 目标尺寸，落地为 .tabpane.on 的 pane-in
转场 + .btn.tiny 36px / .btn.tiny.gear 36px 宽。

验五件事（标准库 + 读仓库文件 + node harness，离线可跑）：
  1) 规则段（panemotion:begin/end）可搬进 node：映射 / 减少动效时长有真行为证据；
  2) JS 常量与 CSS 令牌数字一致（--pane-dur <-> PANE_ANIM_MS、--touch-min <->
     TOUCH_MIN_PX 且 .btn.tiny / .btn.tiny.gear 真的引用令牌）——正则解析后比对，
     单边改就会红；
  3) 转场三件套齐全：@keyframes pane-in、.tabpane.on 引用它、全局
     prefers-reduced-motion「animation: none !important」兜底还在；
  4) 三个图标按钮（palBtn / notifBtn / appSettingsBtn）确实带 tiny gear 类，
     否则 min-width 规则落空；
  5) tabs 绑定用 paneIdFor()，不再手拼 "#pane-" 字符串。
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "panemotion_harness.js"

SEG_BEGIN = "/* ===== panemotion:begin"
SEG_END = "/* ===== panemotion:end"


def _const(js, name):
    m = re.search(r"\b%s = ([^,;\n]+)" % re.escape(name), js)
    return m.group(1).strip() if m else None


def _css_var(css, name):
    m = re.search(r"--%s:\s*([^;]+);" % re.escape(name), css)
    return m.group(1).strip() if m else None


class PureSegmentTest(unittest.TestCase):
    """规则段：标记齐全、可搬进 node、没碰 DOM / localStorage / setTimeout。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_segment_markers_and_purity(self):
        s = self.js.find(SEG_BEGIN)
        e = self.js.find(SEG_END)
        self.assertGreaterEqual(s, 0, "panemotion:begin 缺失")
        self.assertGreater(e, s, "panemotion:end 缺失或顺序错误")
        body = re.sub(r"/\*[\s\S]*?\*/", " ", re.sub(r"//[^\n]*", " ", self.js[s:e]))
        for banned in ("document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("):
            self.assertNotIn(banned, body, "纯函数段出现禁用引用 " + banned)

    def test_harness_cases_pass(self):
        proc = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ALL_PANEMOTION_CASES_PASSED", proc.stdout)


class JsCssAlignmentTest(unittest.TestCase):
    """JS 常量与 CSS 令牌一一对应：单边改就会让这里红。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def test_constants_present(self):
        self.assertEqual(_const(self.js, "PANE_ANIM_MS"), "240")
        self.assertEqual(_const(self.js, "TOUCH_MIN_PX"), "36")

    def test_tokens_present(self):
        self.assertEqual(_css_var(self.css, "pane-duration"), None)
        self.assertEqual(_css_var(self.css, "pane-dur"), "240ms")
        self.assertEqual(_css_var(self.css, "touch-min"), "36px")

    def test_js_css_numeric_alignment(self):
        self.assertEqual(
            int(_const(self.js, "PANE_ANIM_MS")),
            int(_css_var(self.css, "pane-dur")[:-2]),
        )
        self.assertEqual(
            int(_const(self.js, "TOUCH_MIN_PX")),
            int(_css_var(self.css, "touch-min")[:-2]),
        )

    def test_rules_reference_tokens(self):
        self.assertRegex(self.css, r"\.btn\.tiny \{[^}]*min-height: var\(--touch-min\)")
        self.assertRegex(self.css, r"\.btn\.tiny\.gear \{[^}]*min-width: var\(--touch-min\)")
        self.assertRegex(
            self.css, r"\.tabpane\.on \{[^}]*animation: pane-in var\(--pane-dur\)"
        )


class CssBehaviorTest(unittest.TestCase):
    """转场三件套 + 全局减少动效兜底。"""

    @classmethod
    def setUpClass(cls):
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def test_pane_in_keyframes(self):
        self.assertIn("@keyframes pane-in", self.css)
        self.assertRegex(self.css, r"@keyframes pane-in \{[^}]*opacity: 0")

    def test_reduced_motion_guard(self):
        self.assertIn("animation: none !important", self.css)
        self.assertIn("prefers-reduced-motion: reduce", self.css)


class GearButtonCoverageTest(unittest.TestCase):
    """三个图标按钮必须带 tiny gear，min-width 规则才落得到它们头上。"""

    def test_gear_buttons_carry_classes(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        for bid in ("palBtn", "notifBtn", "appSettingsBtn"):
            m = re.search(r'<button id="%s" class="([^"]+)"' % re.escape(bid), html)
            self.assertIsNotNone(m, bid + " 按钮缺失")
            classes = m.group(1).split()
            self.assertIn("tiny", classes, bid + " 缺 tiny")
            self.assertIn("gear", classes, bid + " 缺 gear")


class TabsBindingTest(unittest.TestCase):
    """tabs 绑定走 paneIdFor()，不再手拼 "#pane-" 字符串；减少动效在 UI 线程同步压时长。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_binding_uses_helper(self):
        self.assertIn('$("#" + paneIdFor(t.dataset.tab))', self.js)
        self.assertNotIn('$("#pane-" + t.dataset.tab)', self.js)
        self.assertNotIn("$(paneIdFor(t.dataset.tab))", self.js)   # 裸 id 直接喂 $ 会拿到 null

    def test_reduced_motion_runtime_gate(self):
        self.assertIn(
            'paneAnimMs(window.matchMedia("(prefers-reduced-motion: reduce)").matches)',
            self.js,
        )


if __name__ == "__main__":
    unittest.main()
