#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命中可见：命令面板高亮命中片段（第三十二轮）回归。

动机：加了 tier 5 容错之后，「打开 YouTube」能匹配查询「yutube」，但用户满眼看
去找不到 yutube 在哪——不知道这一行为什么冒出来。模糊匹配必须同时回答
「命中的是哪一段」，否则容错越强越难用。

学习源与实现口径见 static/app.js palmark 段首注释（rapidfuzz 3.14.3 的
rapidfuzz.distance.ScoreAlignment 返回 src/dest 起止区间，而不只给分数）。
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "palmark_harness.js"

PAL_BEGIN = "/* ===== palmark:begin"
PAL_END = "/* ===== palmark:end"
INT_BEGIN = "/* ===== intent:begin"
INT_END = "/* ===== intent:end"
MIN_CASES = 7


def _strip(text):
    return re.sub(r"/\*[\s\S]*?\*/", " ", re.sub(r"//[^\n]*", " ", text))


class SegmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_markers_unique(self):
        self.assertEqual(self.js.count(PAL_BEGIN), 1, "begin 必须唯一")
        self.assertEqual(self.js.count(PAL_END), 1, "end 必须唯一")
        self.assertLess(self.js.index(PAL_BEGIN), self.js.index(PAL_END))

    def test_pure_segment_has_no_global_state(self):
        seg = _strip(self.js[self.js.index(PAL_BEGIN):self.js.index(PAL_END)] +
                     self.js[self.js.index(INT_BEGIN):self.js.index(INT_END)])
        for banned in ["document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("]:
            self.assertNotIn(banned, seg, "禁用引用 " + banned)

    def test_harness_green_and_not_shorter(self):
        if shutil.which("node") is None:
            self.skipTest("没有 node")
        r = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALL_PALMARK_CASES_PASSED", r.stdout)
        total = json.loads(r.stdout.strip().splitlines()[-2])["total"]
        self.assertGreaterEqual(total, MIN_CASES)


class RenderTest(unittest.TestCase):
    """渲染契约：无 innerHTML、语义 mark、查询驱动、保守兜底。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_span_glue_uses_textcontent_only(self):
        seg = self.js[self.js.index("function palMarkSpan"):]
        seg = seg[:seg.index("\n}")]
        self.assertNotIn("innerHTML", seg, "AGENTS.md 禁令")
        self.assertIn("document.createElement(\"mark\")", seg)
        self.assertIn("mark.textContent =", seg)

    def test_no_ranges_falls_back_to_plain_row(self):
        seg = self.js[self.js.index("function palMarkSpan"):]
        seg = seg[:seg.index("\n}")]
        self.assertIn("if (!ranges.length) { span.textContent = text; return span; }", seg)

    def test_palette_row_uses_highlight(self):
        self.assertIn("li.append(ic, palMarkSpan(c.label, palQuery));", self.js)
        self.assertNotIn("lb.textContent = c.label", self.js)

    def test_query_captured_per_render(self):
        seg = self.js[self.js.index("function palRender"):]
        seg = seg[:seg.index("\n  if (!rows.some")]
        self.assertIn("palQuery = query;", seg)

    def test_conservative_rule_asserted_in_harness(self):
        src = HARNESS.read_text(encoding="utf-8")
        self.assertIn("保守原则：对不上就空数组，绝不乱高亮", src)


class StyleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def test_mark_style_tokens_only(self):
        m = re.search(r"\.palmark \{[^}]*\}", self.css)
        self.assertIsNotNone(m)
        self.assertNotRegex(m.group(0), r"#[0-9a-fA-F]{3,8}\b")
        self.assertIn("var(--accent-soft)", m.group(0))

    def test_both_themes_covered(self):
        self.assertGreaterEqual(self.css.count(".palmark"), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
