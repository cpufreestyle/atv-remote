#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡片折叠（第二十六轮）回归。

动机：Playwright 双视口审计显示移动端文档高 2489px（viewport 844px），8 张次级卡片
（键盘输入 / 快捷启动 / 应用 / 工具 / 远程开机 / 调用时间线 / 一键宏 / 睡眠定时）
全平铺，每滚一屏才够到下一组功能。本轮学 WAI-ARIA Disclosure 模式与 Radix
Collapsible / Accordion 的「状态即属性」方法论：data-state 是 CSS 与 JS 之间唯一的
合同，折叠态存浏览器 localStorage（键 atv.collapsed.v1，与自定义宏同规矩，不进
会被打进 bundle.tgz 的 state.json）。

验六件事（标准库 + 读仓库文件 + node harness，离线可跑）：
  1) 纯函数段（collapse:begin/end）可搬进 node：状态机 / 反序列化有真行为证据；
  2) JS 常量与 CSS 令牌数字一致（COLLAPSE_MS <-> --collapse-dur），且规则真的引用令牌；
  3) 折叠三件套：grid-template-rows 1fr->0fr、.cinner overflow hidden、visibility
     带等长延迟隐藏（折叠后不被 Tab 捞到），以及 36px 触控下限；
  4) 8 张卡片都带齐属性：data-collapsible / data-state / data-collapse-name /
     .ctog + aria-controls + cbody-cinner 双层包裹；主遥控卡不许被折叠；
  5) 设置弹窗有「全部展开 / 全部折叠」入口；
  6) DOM 胶水在段外：restore 先于 bind，持久化只走 COLLAPSE_KEY + serialize/parse。
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "collapse_harness.js"
SEG_BEGIN = "/* ===== collapse:begin"
SEG_END = "/* ===== collapse:end"

CARDS = [
    ("kbCard", "键盘输入"), ("favCard", "快捷启动"), ("appsCard", "应用"),
    ("toolsCard", "工具"), ("wolCard", "远程开机"), ("perfCard", "调用时间线"),
    ("macrosCard", "一键宏"), ("sleepCard", "睡眠定时"),
]


def _const(js, name):
    m = re.search(r"\b%s = ([^,;\n]+)" % re.escape(name), js)
    return m.group(1).strip() if m else None


def _css_var(css, name):
    m = re.search(r"--%s:\s*([^;]+);" % re.escape(name), css)
    return m.group(1).strip() if m else None


def _section_open(html, cid):
    for m in re.finditer(r"<section class=\"card\"[^>]*>", html):
        if 'id=\"%s\"' % cid in m.group(0):
            return m.group(0)
    return None


def _glue(js):
    s = js.find(SEG_END)
    if s < 0:
        return ""
    # 胶水段到 tabs 绑定为止，别把后面 tabs 的 classList 圈进断言
    e = js.find("/* tabs */", s)
    return js[s:e] if e >= 0 else js[s:s + 3000]


class PureSegmentTest(unittest.TestCase):
    """规则段：标记齐全、可搬进 node、没碰 DOM / localStorage / setTimeout。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_segment_markers_and_purity(self):
        s = self.js.find(SEG_BEGIN)
        e = self.js.find(SEG_END)
        self.assertGreaterEqual(s, 0, "collapse:begin 缺失")
        self.assertGreater(e, s, "collapse:end 缺失或顺序错误")
        body = re.sub(r"/\*[\s\S]*?\*/", " ", re.sub(r"//[^\n]*", " ", self.js[s:e]))
        for banned in ("document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("):
            self.assertNotIn(banned, body, "纯函数段出现禁用引用 " + banned)

    def test_harness_cases_pass(self):
        proc = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ALL_COLLAPSE_CASES_PASSED", proc.stdout)


class AlignmentTest(unittest.TestCase):
    """JS 常量与 CSS 令牌一一对应：单边改就会让这里红。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def test_constants_present(self):
        self.assertEqual(_const(self.js, "COLLAPSE_MS"), "180")
        self.assertEqual(_const(self.js, "COLLAPSE_KEY"), '"atv.collapsed.v1"')

    def test_tokens_present(self):
        self.assertEqual(_css_var(self.css, "collapse-dur"), "180ms")

    def test_js_css_numeric_alignment(self):
        self.assertEqual(
            int(_const(self.js, "COLLAPSE_MS")),
            int(_css_var(self.css, "collapse-dur")[:-2]),
        )

    def test_rules_reference_tokens(self):
        self.assertRegex(
            self.css, r"\.card > \.cbody \{[^}]*transition:[^}]*var\(--collapse-dur\)[^}]*\}"
        )
        self.assertRegex(self.css, r"grid-template-rows: 1fr")
        self.assertRegex(
            self.css, r"\.cbody > \.cinner \{[^}]*calc\(var\(--collapse-dur\) \* 0\.6\)"
        )
        self.assertRegex(self.css, r"\.card \.ctog \{[^}]*min-height: var\(--touch-min\)")
        self.assertRegex(self.css, r"\.card \.ctog \{[^}]*min-width: var\(--touch-min\)")


class CssBehaviorTest(unittest.TestCase):
    """折叠三件套 + 36px 触控 + chevron 两态旋转。"""

    @classmethod
    def setUpClass(cls):
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def test_zero_row_collapse(self):
        self.assertRegex(
            self.css,
            r'\.card\[data-state="closed"\] > \.cbody \{[^}]*grid-template-rows: 0fr',
        )

    def test_visibility_hidden_after_transition(self):
        # transition 简写里 visibility 段是空格分隔的，不是 visibility: 0s
        self.assertIn("visibility 0s linear var(--collapse-dur)", self.css)
        self.assertRegex(self.css, r"\.cbody > \.cinner \{[^}]*min-height: 0;\s*overflow: hidden")
        self.assertRegex(self.css, r'\.card\[data-state="closed"\] > \.cbody > \.cinner \{[^}]*opacity: 0')

    def test_chevron_two_states(self):
        self.assertRegex(self.css, r"\.card \.ctog \.chev \{[^}]*transform: rotate\(45deg\)")
        self.assertRegex(
            self.css, r'\.card\[data-state="closed"\] \.ctog \.chev \{[^}]*rotate\(-45deg\)'
        )

    def test_settings_seg_touch_target(self):
        self.assertRegex(self.css, r"#collapseSeg button \{[^}]*min-height: var\(--touch-min\)")


class HtmlCoverageTest(unittest.TestCase):
    """8 张次级卡片带齐属性；主遥控卡 / 按键卡不许被折叠。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_card_count(self):
        self.assertEqual(self.html.count("data-collapsible"), len(CARDS), "可折叠卡片数")
        self.assertEqual(self.html.count('class="ctog"'), len(CARDS), "折叠按钮数")

    def test_each_card_carries_state_attrs(self):
        for cid, name in CARDS:
            tag = _section_open(self.html, cid)
            self.assertIsNotNone(tag, cid + " section 缺失")
            self.assertIn("data-collapsible", tag, cid + " 缺 data-collapsible")
            self.assertIn('data-state="open"', tag, cid + " 缺 data-state")
            self.assertIn('data-collapse-name="' + name + '"', tag, cid + " 缺显示名")

    def test_each_card_has_button_and_body_wrapper(self):
        for cid, _ in CARDS:
            self.assertIn('aria-controls="cbody-' + cid + '"', self.html, cid + " 缺 aria-controls")
            self.assertIn('<div class="cbody" id="cbody-' + cid + '">', self.html, cid + " 缺 cbody")
            self.assertIn('<button class="ctog" type="button"', self.html, cid + " 缺 ctog")
            self.assertIn('<div class="cinner">', self.html, cid + " 缺 cinner 内层")
            self.assertIn('id="' + cid + '"', self.html, cid + " 缺卡片 id")

    def test_head_carries_chead_class(self):
        self.assertEqual(self.html.count('<h2 class="chead">'), len(CARDS), "chead 标题行数")

    def test_main_remote_card_not_collapsible(self):
        # 方向键 / 触摸板那张是主遥控卡：永远展开，不能被折叠
        i = self.html.find('id="pane-dpad"')
        self.assertGreater(i, 0, "pane-dpad 缺失")
        j = self.html.rfind("<section", 0, i)
        tag = self.html[j:self.html.index(">", j) + 1]
        self.assertNotIn("data-collapsible", tag, "主遥控卡不许折叠")

    def test_keypad_card_not_collapsible(self):
        # 物理按键卡 + 手机安装卡都没有 h2：无 id 的普通 card section 一律不许可折叠
        chunks = self.html.split('<section class="card">')[1:]
        self.assertGreaterEqual(len(chunks), 1, "至少应有一个无 id 的普通 card section")
        for blk in chunks:
            nxt = blk.find("<section")
            seg = blk if nxt < 0 else blk[:nxt]
            self.assertNotIn("data-collapsible", seg, "无 id 的普通卡片不许折叠")
        nxt = chunks[0].find("<section")
        seg = chunks[0] if nxt < 0 else chunks[0][:nxt]
        self.assertIn('data-key="4"', seg, "第一个无 id 卡片应是按键卡")


class SettingsEntryTest(unittest.TestCase):
    """设置弹窗里有「全部展开 / 全部折叠」入口。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_collapse_seg_in_settings(self):
        i = self.html.find('id="settingsModal"')
        j = self.html.find('id="coachMark"')
        self.assertGreater(i, 0)
        self.assertGreater(j, i)
        modal = self.html[i:j]
        self.assertIn('id="collapseSeg"', modal, "设置弹窗缺 collapseSeg")
        self.assertIn('data-collapse-all="open"', modal, "缺「全部展开」")
        self.assertIn('data-collapse-all="closed"', modal, "缺「全部折叠」")


class GlueTest(unittest.TestCase):
    """DOM 胶水在段外：restore 先于 bind，存储只在胶水里，样式只走属性。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.glue = _glue(cls.js)

    def test_glue_exists_after_segment(self):
        self.assertTrue(self.glue.strip(), "collapse:end 之后没有 DOM 胶水")

    def test_restore_before_bind(self):
        self.assertLess(self.glue.find("collapseRestore();"), self.glue.find("collapseBind();"))
        self.assertIn("collapseCards()", self.glue)

    def test_persistence_only_in_glue(self):
        self.assertIn("localStorage", self.glue, "存储必须在胶水里")
        self.assertIn("COLLAPSE_KEY", self.glue)
        self.assertIn("parseCollapsed(localStorage.getItem(COLLAPSE_KEY)", self.glue)
        self.assertIn("localStorage.setItem(COLLAPSE_KEY, serializeCollapsed(closed))", self.glue)

    def test_state_is_the_only_contract(self):
        # 胶水只翻属性，不碰样式 / classList / innerHTML
        self.assertIn("el.dataset.state = state;", self.glue)
        self.assertIn('setAttribute("aria-expanded"', self.glue)
        self.assertIn('setAttribute("aria-label"', self.glue)
        for banned in ("classList", "innerHTML", ".style"):
            self.assertNotIn(banned, self.glue, "胶水出现 " + banned)

    def test_no_double_bind(self):
        self.assertEqual(
            self.glue.count("collapseBind();"), 1, "collapseBind() 只能调用一次"
        )


if __name__ == "__main__":
    unittest.main()
