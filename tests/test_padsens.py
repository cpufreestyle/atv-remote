#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""触摸板灵敏度自适应（第二十四轮）回归。

动机：各遥控 App 的指针 / 滚动增益、游戏灵敏度滑条——「同一个手势，不同人想要的
速率不同」是输入设备的共性。本轮的解法不是新增控件，而是给既有触摸板的两种滚动
（双指手势 seek、单指长按连发）加一个可调增益，步长 / 间隔按增益缩放。

这里只验五件事，全部离线可跑（标准库 + 读仓库文件 + 跑 node harness）：
  1) 规则段（padsens:begin/end）可搬进 node：钳位 / 步长缩放 / 间隔缩放 / 读数
     有真行为证据（tests/padsens_harness.js）；
  2) 基础常量与触摸板手势段的 GESTURE_STEP / PAD_HOLD_RATE 对齐——两处各写一份
     数字迟早漂移，用正则解析后比对，而不是复制注释里的说明；
  3) 持久化只落 localStorage（atv.padSens），不进 state.json（敏感文件）；
  4) 读数渲染用 textContent——虽然是本地数字，但整条链路保持无 innerHTML；
  5) 样式段只用语义令牌。
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "padsens_harness.js"

PADSENS_BEGIN = "/* ===== padsens:begin"
PADSENS_END = "/* ===== padsens:end"
CSS_MARK = "触摸板灵敏度"
PADSENS_KEY = "atv.padSens"


def _const(js, name):
    # 常量可能是同行多声明（如 const A = 1, B = 2, C = 3;），只取该名字的值
    m = re.search(r"\b%s = ([^,;]+)" % re.escape(name), js)
    if not m:
        return None
    return m.group(1).strip()


def _strip_comments(text):
    return re.sub(r"/\*[\s\S]*?\*/", " ", re.sub(r"//[^\n]*", " ", text))


class PureSegmentTest(unittest.TestCase):
    """规则段：符号齐全、可搬进 node、没碰 DOM / 网络 / 敏感文件。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.server = (ROOT / "server.py").read_text(encoding="utf-8")

    def seg(self):
        return self.js[self.js.index(PADSENS_BEGIN):self.js.index(PADSENS_END)]

    def test_segment_has_every_rule(self):
        seg = self.seg()
        for frag in ("const PADSENS_KEY = ",
                     "const PADSENS_MIN = 0.5, PADSENS_MAX = 2, PADSENS_DEFAULT = 1;",
                     "const PADSENS_BASE_STEP = ", "const PADSENS_BASE_RATE = ",
                     "function padSensClamp(", "function padStepFor(",
                     "function padHoldRateFor(", "function padSensLabel("):
            self.assertIn(frag, seg)

    def test_segment_touches_no_dom_or_network(self):
        code = _strip_comments(self.seg())
        for banned in ("document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("):
            self.assertNotIn(banned, code)

    def test_persistence_is_localstorage_only_not_state_json(self):
        # state.json 带配对凭据、会进 bundle.tgz：灵敏度是客户端偏好，不进敏感文件
        self.assertIn("localStorage.getItem(PADSENS_KEY)", self.js)
        self.assertIn("localStorage.setItem(PADSENS_KEY", self.js)
        self.assertNotIn(PADSENS_KEY, self.server)


class WiringTest(unittest.TestCase):
    """接线：滑条 / 读数 / 重置挂点、基础常量对齐、手势与连发改用增益。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_slider_markup_exists(self):
        for frag in ('id="padSens"', 'id="padSensVal"', 'id="padSensReset"'):
            self.assertIn(frag, self.html)
        m = re.search(r'<input type="range" id="padSens"[^>]*>', self.html)
        self.assertIsNotNone(m, "滑条 input 缺失")
        for attr in ('min="0.5"', 'max="2"', 'step="0.05"', 'value="1"'):
            self.assertIn(attr, m.group(0))

    def test_storage_key_is_defined_once(self):
        # 收藏夹那轮栽过：KEY 只在 getItem/setItem 里出现、从没定义，
        # try 吞掉 ReferenceError，页面照常但不落盘。定义恰好一次才拦得住。
        self.assertEqual(self.js.count('const PADSENS_KEY = "atv.padSens"'), 1)

    def test_base_step_matches_gesture_step(self):
        # 两处常量各写一份数字迟早漂移：直接解析真实定义再比对
        self.assertEqual(_const(self.js, "PADSENS_BASE_STEP"), _const(self.js, "GESTURE_STEP"))
        self.assertEqual(_const(self.js, "GESTURE_STEP"), "26")

    def test_base_rate_matches_hold_rate(self):
        hold = _const(self.js, "PAD_HOLD_RATE")
        self.assertIsNotNone(hold)
        self.assertEqual(_const(self.js, "PADSENS_BASE_RATE"), hold)
        self.assertEqual(_const(self.js, "PADSENS_BASE_RATE"), "120")

    def test_two_finger_gesture_uses_scaled_step(self):
        self.assertIn("const sp = padStepFor(padGain);", self.js)
        self.assertIn("while (Math.abs(gesture.ry) >= sp) {", self.js)
        self.assertIn("while (Math.abs(gesture.rx) >= sp) {", self.js)
        # 旧写法整条换掉：pointermove 里不得再直接拿 GESTURE_STEP 当阈值
        i = self.js.index("const sp = padStepFor(padGain);")
        self.assertNotIn(">= GESTURE_STEP", self.js[i:i + 1200])

    def test_hold_repeat_uses_scaled_rate(self):
        self.assertIn("setInterval(padHoldTick, padHoldRateFor(padGain))", self.js)
        self.assertNotIn("setInterval(padHoldTick, PAD_HOLD_RATE)", self.js)

    def test_reading_is_text_content_not_inner_html(self):
        i = self.js.index("function padSensApply() {")
        body = self.js[i:self.js.index(chr(10) + "function ", i + 10)]
        body = _strip_comments(body)   # 行尾注释里的说明文字不算渲染方式
        self.assertIn('$("#padSensVal")', body)
        self.assertIn("textContent = padSensLabel(padGain)", body)
        self.assertNotIn("innerHTML", body)

    def test_boot_applies_and_binds(self):
        # 滑条要有初值（localStorage 恢复）+ 事件监听，缺一个就是「看着能用其实是死的」
        self.assertIn("padSensApply();" + chr(10) + "padSensBind();", self.js)

    def test_reset_falls_back_to_default(self):
        i = self.js.index("function padSensBind() {")
        body = self.js[i:self.js.index(chr(10) + "function ", i + 10)]
        self.assertIn('$("#padSensReset")', body)
        self.assertIn("padGain = PADSENS_DEFAULT;", body)


class HarnessParityTest(unittest.TestCase):
    """harness 抽的就是 static/app.js 里那一段：符号必须同源，且真跑全绿。"""

    @classmethod
    def setUpClass(cls):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.seg = js[js.index(PADSENS_BEGIN):js.index(PADSENS_END)]
        cls.harness = HARNESS.read_text(encoding="utf-8")

    def test_harness_references_the_same_symbols(self):
        for sym in ("PADSENS_MIN", "PADSENS_MAX", "PADSENS_DEFAULT", "PADSENS_STEP_MIN",
                    "PADSENS_STEP_MAX", "PADSENS_RATE_MIN", "PADSENS_RATE_MAX",
                    "padSensClamp", "padStepFor", "padHoldRateFor", "padSensLabel",
                    "ALL_PADSENS_CASES_PASSED"):
            self.assertIn(sym, self.harness)

    def test_harness_runs_green(self):
        if shutil.which("node") is None:
            self.skipTest("没有 node，跳过 harness")
        p = subprocess.run(["node", str(HARNESS)], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ALL_PADSENS_CASES_PASSED", p.stdout)


class StyleTest(unittest.TestCase):
    """样式：语义色、一行排布、读数不抖字宽。"""

    @classmethod
    def setUpClass(cls):
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def seg(self):
        i = self.css.index(CSS_MARK)
        j = self.css.find("/* ----------", i + 1)
        return self.css[i:] if j < 0 else self.css[i:j]

    def test_section_exists_and_uses_only_semantic_tokens(self):
        seg = self.seg()
        self.assertNotIn("rgb(", seg)
        self.assertNotIn("hsl(", seg)
        self.assertFalse(re.findall(r"#[0-9a-fA-F]{3,8}(?:[^0-9a-zA-Z]|$)", seg))

    def test_layout_is_one_row(self):
        seg = self.seg()
        for frag in (".padsens {", ".padsens label {", '.padsens input[type="range"] {',
                     ".padsens .padsensval {", ".padsens .btn.tiny {"):
            self.assertIn(frag, seg)


if __name__ == "__main__":
    unittest.main()
