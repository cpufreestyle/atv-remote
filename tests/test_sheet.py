#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设备 chip 长按 → 底部快捷菜单（第二十九轮）回归。

动机：最近连接 chip 原先只有「点连接 / 右键移除」——触屏没有右键，iOS Safari 的
contextmenu 根本不触发，手机上当前无法移除最近设备；在线设备 chip 也只剩「点一下切换」。
本轮学 Material 3 Bottom Sheet / iOS Context Menu / Home Assistant more-info 的行为契约：
「属于这个对象的所有动作，收进一个单指可达的弹层」，长按 450ms 起手、破坏性动作排尾。

验六件事（标准库 + 读仓库文件 + node harness，离线可跑）：
  1) sheet 纯函数段可搬进 node：快照解析 / 动作表矩阵 / danger 排尾有真行为证据；
  2) 三条菜单硬规则：复制恒在、forget 恒最后且 danger、唤醒只在当前离线且有 MAC；
  3) 胶水只映射动作到既有入口，长按手势契约（450ms / 10px / buzz / 吞 click）齐全；
  4) 旧右键移除已并入菜单（oncontextmenu 归零），遮蔽点击可关（触屏没有 Esc）；
  5) 容器 / 样式走设计令牌：底端锚定、动作行 >= 36px、无 hex 硬编码；
  6) 版本号单一来源（VERSION）只往前推进（不低于本轮 versionCode 33），别处没有硬编码。
"""

import re
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "sheet_harness.js"
SEG_BEGIN = "/* ===== sheet:begin"
SEG_END = "/* ===== sheet:end"
GLUE_BEGIN = "/* ---- 底部快捷菜单 DOM 胶水"
GLUE_END = "/* ---------------- ADBKeyboard"

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
        cls.seg = cls.js[cls.js.find(SEG_BEGIN):cls.js.find(SEG_END)]

    def test_segment_markers_and_purity(self):
        s = self.js.find(SEG_BEGIN)
        e = self.js.find(SEG_END)
        self.assertGreaterEqual(s, 0, "sheet:begin 缺失")
        self.assertGreater(e, s, "sheet:end 缺失或顺序错误")
        body = _strip_comments(self.seg)
        for banned in BANNED:
            self.assertNotIn(banned, body, "纯函数段出现禁用引用 " + banned)

    def test_labels_have_no_markup(self):
        body = _strip_comments(self.seg)
        self.assertNotIn("<", body, "文案最终走 textContent，源头不许埋 HTML")

    def test_snapshot_contract(self):
        for line in ('const SHEET_NONE = null;',
                     'function sheetStateText(dev) {',
                     'function sheetDevOfRecent(s, ip, wakes) {',
                     'function sheetDevOfDevice(s, dev) {',
                     'function sheetDevOfAtv(s, dev) {',
                     'function sheetItems(dev) {'):
            self.assertIn(line, self.seg, "快照 / 动作表函数缺失：" + line)
        self.assertIn('wolIpOf(d.serial) === target', self.seg,
                      "online 要按 IP 归一再比（adb serial 带端口）")
        self.assertIn('removable: true, canWake: !online && !current && wake', self.seg,
                      "唤醒只在当前离线且非当前设备")

    def test_harness_cases_pass(self):
        proc = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ALL_SHEET_CASES_PASSED", proc.stdout)
        self.assertGreaterEqual(proc.stdout.count("ok   - "), 15, "用例数不应悄悄变少")

class GlueTest(unittest.TestCase):
    """DOM 胶水在段外：只建 DOM / 映射动作，规则不重复实现。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.glue = _block(cls.js, GLUE_BEGIN, GLUE_END)

    def test_layout_is_hoisting_safe(self):
        self.assertLess(self.js.find("sheetBindChip(c, sheetDevOfRecent"), self.js.find(SEG_BEGIN),
                        "调用点可以站在段前：sheet* 是函数声明，会提升")
        self.assertLess(self.js.find(GLUE_BEGIN), self.js.find(GLUE_END),
                        "胶水段落标记顺序")
        self.assertLess(self.js.find("let wolTargets = [];"), self.js.find(GLUE_END),
                        "wolTargets 必须先声明：胶水在顶层选中 #sheet 之外只引用它")

    def test_single_definition(self):
        for name in ("function sheetBindChip(", "function sheetOpen(", "function sheetAct(",
                     "function sheetCancelHold(", "function sheetCopy(", "const SHEET_HOLD_MS ="):
            self.assertEqual(self.js.count(name), 1, name + " 只能定义一次")

    def test_longpress_contract(self):
        self.assertIn("const SHEET_HOLD_MS = 450;", self.glue, "长按判定 450ms")
        self.assertIn("const SHEET_MOVE_PX = 10;", self.glue, "位移超过 10px 取消长按")
        self.assertIn("buzz(15);", self.glue, "长按命中有触觉确认")
        self.assertIn("sheetHoldConsumed = true;", self.glue, "长按后要吞掉补发的 click")
        self.assertIn("if (sheetHoldConsumed) { sheetHoldConsumed = false;", self.glue,
                      "吞 click 的那一拍")
        self.assertIn("Math.hypot(e.clientX - sheetHoldCtx.x0, e.clientY - sheetHoldCtx.y0) > SHEET_MOVE_PX",
                      self.glue, "滚动意图要能取消长按")

    def test_controls_keep_their_own_gestures(self):
        self.assertIn('t.closest("button, a, input, select, textarea")', self.glue,
                      "Apple TV 行里的按钮不能被长按/右键劫走")

    def test_chip_bindings(self):
        self.assertIn('c.title = "点击连接 · 长按/右键打开菜单";', self.js)
        self.assertIn('sheetBindChip(c, sheetDevOfRecent(s, t, wolTargets));', self.js,
                      "最近连接 chip 要接菜单")
        self.assertIn('c.title = "点击切换 · 长按/右键打开菜单";', self.js)
        self.assertIn('sheetBindChip(c, sheetDevOfDevice(s, d));', self.js,
                      "在线设备 chip 要接菜单")
        self.assertIn('function atvRow(dev, s) {', self.js, "行渲染要收状态参数")
        self.assertIn('sheetBindChip(row, sheetDevOfAtv(s || window.__atvLastStatus || {}, dev));', self.js,
                      "Apple TV 行要接菜单，且状态显式传入")
        self.assertEqual(self.js.count('renderAtvFound(s.appletv.devices.map((d) => ({ ...d, paired: true, stored: true })), s);'), 2,
                         "renderAtvKnown 与页签扫描两条渲染路径都要把状态传给行")
        self.assertEqual(self.js.count("oncontextmenu"), 0,
                         "旧右键移除应已并入菜单，oncontextmenu 归零")
        self.assertIn("window.__atvLastStatus = s;", self.js,
                      "renderStatus 要存快照，Apple TV 行的 connected 判断要用")

    def test_wol_cache(self):
        self.assertIn("let wolTargets = [];", self.js, "WOL 发现结果要缓存")
        self.assertIn("wolTargets = r.targets || [];", self.js, "wolDiscover 要写缓存")

    def test_render_and_close_paths(self):
        self.assertIn('closeModal("#sheet"); sheetAct(it.id, dev);', self.glue,
                      "先关菜单再执行动作")
        self.assertIn('b.textContent = it.label;', self.glue, "动作文案走 textContent")
        self.assertIn('$("#sheet").addEventListener("click", (e) => { if (e.target === $("#sheet")) closeModal("#sheet"); });',
                      self.glue, "触屏没有 Esc：点遮罩要能关")

    def test_copy_has_fallback(self):
        self.assertIn("navigator.clipboard", self.glue, "优先走 clipboard API")
        self.assertIn("document.execCommand(\"copy\")", self.glue,
                      "明文 http 非安全上下文里 navigator.clipboard 不存在，必须留 execCommand 兜底")

    def test_action_mapping(self):
        for line in ('if (id === "copy") {',
                     'if (id === "connect") { connect(t); return; }',
                     'if (id === "switch") { await api("/api/switch", { target: t }); refreshStatus(); return; }',
                     'if (id === "wake") {',
                     'if (id === "forget") {',
                     'await api("/api/forget", { target: t });',
                     'await api("/api/atv/forget", { id: t });'):
            self.assertIn(line, self.glue, "动作 id 必须映射到既有入口：" + line)
        code = _strip_comments(self.glue)
        self.assertNotIn("innerHTML", code, "glue 代码里不许出现 innerHTML（注释提到不算）")

class HtmlTest(unittest.TestCase):
    """容器排在 keymapModal 之后、默认隐藏、可被读屏识别。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_container(self):
        km = self.html.find('id="keymapModal"')
        box = self.html.find('id="sheet"')
        self.assertGreaterEqual(km, 0, "keymapModal 缺失")
        self.assertGreater(box, km, "菜单容器要排在 keymapModal 之后")
        self.assertEqual(self.html.count('id="sheet"'), 1, "只能有一个菜单容器")
        self.assertIn('class="modal hidden sheet"', self.html, "默认隐藏，有设备时不占地")
        self.assertIn('role="dialog"', self.html, "弹窗语义")
        self.assertIn('aria-modal="true"', self.html)

    def test_inner_ids_unique(self):
        for i in ("sheetTitle", "sheetSub", "sheetActs"):
            self.assertEqual(self.html.count('id="%s"' % i), 1, i + " 只能有一个")
        self.assertIn('role="menu"', self.html, "动作列表是 menu")

class DomNestingTest(unittest.TestCase):
    """DOM 嵌套回归：#sheet 必须是 body 直接子元素。

    第一版把 #sheet 插在了 keymapModal 的闭合 </div> 之前，弹层于是成了 keymapModal
    的子节点——父级 display:none，菜单「开了」却一个字都不显示，静态检查全部看不出来。
    用 HTMLParser 数 div 嵌套，任何把 #sheet / #volOsd 埋进别的容器里的改动都会挂。"""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr"}

    @classmethod
    def setUpClass(cls):
        cls.parents = {}

        class Nesting(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.stack = []

            def handle_starttag(self, tag, attrs):
                if tag in void:
                    return
                d = dict(attrs)
                if d.get("id") in ("sheet", "volOsd"):
                    cls.parents[d["id"]] = [t for t, _ in self.stack]
                self.stack.append((tag, d))

            def handle_endtag(self, tag):
                for i in range(len(self.stack) - 1, -1, -1):
                    if self.stack[i][0] == tag:
                        del self.stack[i:]
                        break

        void = cls.VOID
        parser = Nesting()
        parser.feed((STATIC / "index.html").read_text(encoding="utf-8"))
        parser.close()

    def test_sheet_is_body_child(self):
        self.assertIn("sheet", self.parents, "#sheet 没被解析到")
        self.assertEqual(self.parents["sheet"], ["html", "body"],
                         "#sheet 必须是 body 直接子元素，嵌进别的容器会被父级 display:none 吞掉")

    def test_no_stray_unclosed_div(self):
        # keymapModal 必须自己在 #sheet 之前闭合：闭合标签被「借走」会把后面所有节点
        # 都变成它的子节点（音量 OSD 就是这么消失的）
        self.assertEqual(self.parents.get("volOsd"), ["html", "body"],
                         "#volOsd 也被埋进了别的容器：前面有 div 没闭合")

class CssTest(unittest.TestCase):
    """底部菜单走设计令牌，颜色不写死。"""

    @classmethod
    def setUpClass(cls):
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")
        cls.rule = _block(cls.css, ".modal.sheet {", "/* ---------- 截屏工作台")

    def test_rule_exists(self):
        self.assertTrue(self.rule.strip(), ".modal.sheet 规则缺失")

    def test_bottom_anchored(self):
        self.assertIn("align-items: flex-end", self.rule, "底端锚定：拇指热区在屏幕下半")
        self.assertIn("padding: 0", self.rule)
        self.assertIn("border-radius: var(--r-lg) var(--r-lg) 0 0", self.rule)
        self.assertIn("env(safe-area-inset-bottom)", self.rule, "要避开 iPhone 底部横条")

    def test_touch_target_and_tokens(self):
        self.assertIn("min-height: var(--touch-min)", self.rule,
                      "动作行触控下限与空状态 CTA 同一个令牌")
        self.assertIn("animation: sheet-in .2s var(--ease)", self.rule)
        self.assertNotIn("#", self.rule, "颜色不许写死 hex")
        self.assertIn(".sheetact.danger { color: var(--danger-text); }", self.rule,
                      "破坏性动作走语义色")

class VersionTest(unittest.TestCase):
    """版本号只从根目录 VERSION 读。"""

    def test_version_bumped(self):
        text = (ROOT / "VERSION").read_text(encoding="utf-8")
        name = re.search(r"versionName=(\S+)", text).group(1)
        code = re.search(r"versionCode=(\d+)", text).group(1)
        # 只做下限守卫：把某一轮的版本号钉死，之后每轮都要回来改这条断言
        # （滚动的那份在 test_undo.py）。
        self.assertGreaterEqual(int(code), 33, "VERSION 不许往回退")
        self.assertTrue(name.startswith("1."), "版本号仍从单一来源读")

if __name__ == "__main__":
    unittest.main(verbosity=2)
