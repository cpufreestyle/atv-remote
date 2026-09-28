#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""空状态（第二十八轮）回归。

动机：Android 连接面板以前是一片静默——没连过设备时只剩一个 IP 输入框和扫描按钮，
新用户不知道该点什么；连过的设备全掉线时又看不出「电视是不是睡了」。本轮学
Shopify Polaris Empty State / Material empty state 的行为契约：列表空着不是无话可说，
而要给「一句解释 + 一个下一步动作」，且 empty 状态自己会说话（离线要说几台）。

验七件事（标准库 + 读仓库文件 + node harness，离线可跑）：
  1) empty 纯函数段可搬进 node：判空优先级 / 文案查表 / 动作键有真行为证据；
  2) 胶水快照只认 Android 视角（cur_type === "android"），Apple TV 的 current 不算有设备；
  3) 空态渲染挂在 renderStatus 上、chipSig 带 adb_found，否则判空输入变化不触发刷新；
  4) 节流签名 + 隐藏路径：签名没变不重建，隐藏时清空内容，按钮文案一律 textContent；
  5) 容器排在 deviceChips 之后、默认 hidden、aria-live，可被读屏播报；
  6) 空态样式走设计令牌（虚线圈 + --card2 + --touch-min），无 hex 硬编码；
  7) 版本号单一来源（VERSION）随轮推进为 1.31.0，别处没有硬编码。
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "empty_harness.js"
SEG_BEGIN = "/* ===== empty:begin"
SEG_END = "/* ===== empty:end"

BANNED = ("document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$(",
          "Date.now", "new Date")

def _block(js, start, end):
    s = js.find(start)
    if s < 0:
        return ""
    e = js.find(end, s)
    return js[s:e] if e >= 0 else js[s:]

def _strip_comments(seg):
    return re.sub(r"/\*[\s\S]*?\*/", " ", re.sub(r"//[^\n]*", " ", seg))

class PureSegmentTest(unittest.TestCase):
    """规则段：标记齐全、可搬进 node、没碰 DOM / localStorage / setTimeout。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_segment_markers_and_purity(self):
        s = self.js.find(SEG_BEGIN)
        e = self.js.find(SEG_END)
        self.assertGreaterEqual(s, 0, "empty:begin 缺失")
        self.assertGreater(e, s, "empty:end 缺失或顺序错误")
        body = _strip_comments(self.js[s:e])
        for banned in BANNED:
            self.assertNotIn(banned, body, "纯函数段出现禁用引用 " + banned)

    def test_copy_has_no_markup(self):
        seg = self.js[self.js.find(SEG_BEGIN):self.js.find(SEG_END)]
        body = _strip_comments(seg)
        self.assertNotIn("<", body, "文案最终走 textContent，源头不许埋 HTML")

    def test_copy_table_contract(self):
        seg = self.js[self.js.find(SEG_BEGIN):self.js.find(SEG_END)]
        self.assertIn('cta: "🔍 扫描局域网", act: "scan"', seg, "intro 主动作是扫描")
        self.assertIn('cta: "重连最近一台", act: "reconnect"', seg, "offline 主动作是重连")
        self.assertIn("cta: null, act: null", seg, "noadb 没有可执行的下一步")
        self.assertIn('cta2: "手输 IP", act2: "focus"', seg, "intro 次动作是聚焦输入框")

    def test_harness_cases_pass(self):
        proc = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ALL_EMPTY_CASES_PASSED", proc.stdout)
        self.assertGreaterEqual(proc.stdout.count("ok   - "), 13, "用例数不应悄悄变少")

class GlueTest(unittest.TestCase):
    """DOM 胶水在段外：只建 DOM / 映射动作，规则不重复实现。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.glue = _block(cls.js, "function estateSnap(s) {", "/* ---------------- ADBKeyboard")

    def test_layout_is_hoisting_safe(self):
        # 布局事实：renderStatus 调用点与 DOM 胶水都在规则段之前，靠函数声明提升生效；
        # 规则段里的 const EMPTY_NONE 只能被函数体引用，首个渲染必须晚于顶层执行
        self.assertLess(self.js.find("renderEstate(s);"), self.js.find(SEG_BEGIN),
                        "调用点可以站在段前：emptyKind / emptyCopy 是函数声明，会提升")
        self.assertLess(self.js.find("function renderEstate("),
                        self.js.find("/* ---------------- ADBKeyboard"),
                        "空状态胶水要收在 ADBKeyboard 段之前")
        self.assertGreater(self.js.find("\nrefreshStatus();"),
                           self.js.find(SEG_END),
                           "顶层首次 refreshStatus 必须落在规则段之后，否则会撞 EMPTY_NONE 的 TDZ")

    def test_snap_only_android_perspective(self):
        self.assertIn('current: s.cur_type === "android" ? s.current : ""', self.glue,
                      "Apple TV 页签的 current 不能算 Android 视角的有设备")

    def test_single_definition(self):
        for name in ("function estateSnap(", "function estateAct(",
                     "function renderEstate(", "let estateSig ="):
            self.assertEqual(self.js.count(name), 1, name + " 只能定义一次")

    def test_throttle_and_hide_path(self):
        self.assertIn("if (sig === estateSig) return;", self.glue,
                      "签名没变就别重建：8s 轮询会反复进来")
        self.assertIn('box.classList.add("hidden");', self.glue, "隐藏路径要加回 hidden")
        self.assertIn('box.textContent = "";', self.glue, "隐藏必须清空内容")

    def test_build_with_dom_api(self):
        self.assertIn("createElementNS", self.glue, "图标手搓 SVG，不引图标库")
        self.assertIn('ic.setAttribute("class", "eicon");', self.glue,
                      "SVG 的 className 是只读的 SVGAnimatedString，赋值会整段抛异常")
        code = _strip_comments(self.glue)
        self.assertNotIn("innerHTML", code, "glue 代码里不许出现 innerHTML（注释提到不算）")
        self.assertIn("title.textContent = copy.title;", self.glue)
        self.assertIn("desc.textContent = copy.desc;", self.glue)

    def test_action_mapping(self):
        for line in ('if (act === "scan") return adbScan;',
                     'if (act === "focus")',
                     'if (act === "reconnect")'):
            self.assertIn(line, self.glue, "动作键必须映射到真行为：" + line)
        self.assertIn('$("#targetInput").value = ip;', self.glue, "reconnect 要填输入框")
        self.assertIn("connect(ip);", self.glue, "reconnect 要真去连")

    def test_render_called_from_status_loop(self):
        self.assertIn("renderEstate(s);", self.js, "renderStatus 每次刷新都要 revisit 空态")
        self.assertIn("s.adb_found,", self.js, "chipSig 要带 adb_found，否则判空输入变化不刷新")

class HtmlTest(unittest.TestCase):
    """容器排在 chips 之后、默认隐藏、可播报。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_estate_container_after_chips(self):
        chips = self.html.find('id="deviceChips"')
        box = self.html.find('id="estateAndroid"')
        self.assertGreaterEqual(chips, 0, "deviceChips 缺失")
        self.assertGreater(box, chips, "空状态容器要排在 chips 后面")
        self.assertEqual(self.html.count('id="estateAndroid"'), 1, "只能有一个空态容器")
        self.assertIn('class="estate hidden"', self.html, "默认隐藏，有设备时不占地")
        self.assertIn("aria-live=\"polite\"", self.html, "内容变化要能被读屏播报")

class CssTest(unittest.TestCase):
    """空态卡片走设计令牌，颜色不写死。"""

    @classmethod
    def setUpClass(cls):
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")
        cls.rule = _block(cls.css, ".estate {", "/* ---------- 音量 OSD")

    def test_rule_exists(self):
        self.assertTrue(self.rule.strip(), ".estate 规则缺失")

    def test_dashed_card_uses_tokens(self):
        self.assertIn("border: 1px dashed var(--line)", self.rule, "空卡片用虚线圈出")
        self.assertIn("background: var(--card2)", self.rule)
        self.assertIn("text-align: center", self.rule)
        self.assertNotIn("#", self.rule, "颜色不许写死 hex")
        self.assertNotIn("innerHTML", self.rule)

    def test_cta_touch_target(self):
        self.assertIn("min-height: var(--touch-min)", self.rule,
                      "CTA 触控下限与 panemotion 段同一个令牌")

class VersionTest(unittest.TestCase):
    """版本号只从根目录 VERSION 读。"""

    def test_version_bumped(self):
        text = (ROOT / "VERSION").read_text(encoding="utf-8")
        name = re.search(r"versionName=(\S+)", text).group(1)
        code = int(re.search(r"versionCode=(\d+)", text).group(1))
        # 1.31.0 是空状态落地时的基线，不是「永远等于它」：后续轮次只许往上走，
        # 否则每出一轮都要回来改这条断言（滚动的那份在 test_undo.py）
        self.assertGreaterEqual(code, 32, "VERSION 不许往回退")
        self.assertTrue(name.startswith("1."), "版本号仍从单一来源读")

if __name__ == "__main__":
    unittest.main(verbosity=2)
