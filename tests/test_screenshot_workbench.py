"""截屏工作台（第十七轮）静态资产回归：结构 / ARIA 契约 / 手势 wiring / 选择器引用一致性。

与命令面板同款思路——前端功能没有 HTTP 行为可测，但四类回归必须挡住：
1. 弹窗结构与 ARIA 契约被后续改动破坏（role=list / aria-live 是读屏与键盘可达性的地基）；
2. app.js 里 $("#id") 引用了不存在的 id——新 ID 打错字时功能静默失效
   （static/static/ 那种「安静事故」同款风险），宏表单动态字段走白名单；
3. blob URL 泄漏：归档每截一张都在常驻内存，挤出的那张必须 revoke；
4. AGENTS.md 禁令回潮：设备名 / IP 用 innerHTML 渲染；新组件样式写死颜色。

只读 static/ 文件 + node --check，不起服务、不碰网络。
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

# 宏可视化表单的字段由 JS 动态创建（macroAddStep），不属于「引用漂移」
DYNAMIC_IDS = {"mfDelay", "mfKey", "mfOk", "mfPkg", "mfText"}


class ShotMarkupTest(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_dialog_contract(self):
        for frag in ('id="shotModal"', 'class="modal hidden"', 'role="dialog"',
                     'aria-modal="true"', 'aria-label="电视画面"', 'id="shotCap"',
                     'id="shotStage"', 'id="shotStrip"', 'id="shotHint"',
                     'id="shotSave"', 'download="tv.png"', 'class="mbox shotbox"',
                     'draggable="false"', "data-autofocus"):
            self.assertIn(frag, self.html)

    def test_strip_and_zoom_aria(self):
        for frag in ('role="list"', 'aria-label="最近截屏"', 'role="group"',
                     'aria-label="画面缩放"', 'aria-live="polite"', 'id="shotZoomLabel"',
                     'id="shotZoomIn"', 'id="shotZoomOut"', 'id="shotZoomFit"'):
            self.assertIn(frag, self.html)

    def test_capture_button_exists(self):
        # 截屏是免费奶牛：手机端不一定有遥控器实体键，入口必须在工具栏里
        self.assertIn('id="shotBtn"', self.html)


class ShotScriptTest(unittest.TestCase):
    def setUp(self):
        self.js = (STATIC / "app.js").read_text(encoding="utf-8")
        # 段尾取下一节唯一锚点：loadMacros() 在文件里被调用多次，不能当边界
        self.seg = self.js[self.js.index("const SHOT_MAX"):
                           self.js.index("/* ---------------- 屏幕常亮（Wake Lock）")]

    def test_syntax(self):
        r = subprocess.run(["node", "--check", str(STATIC / "app.js")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_wiring(self):
        for frag in ("const SHOT_MAX", "const shotShots", "shotFmt(", "shotClock(",
                     "shotApply(", "shotClamp(", "shotFit(", "shotZoomAt(",
                     "shotRenderStrip(", "shotCapText(", "shotShow(", "shotPush(",
                     "shotPtrEnd(", "setPointerCapture", "aria-current", "passive: false",
                     "translate(${", "scale(${", "URL.createObjectURL",
                     "`tv-${shotFmt(shot.ts)}.png`"):
            self.assertIn(frag, self.seg)

    def test_eviction_revokes_exactly_once(self):
        # 归档上限内每张都可能被再次点开：只 revoke 被挤出的那一张，且只能有一次
        m = re.search(
            r"while \(shotShots\.length > SHOT_MAX\)\s*URL\.revokeObjectURL\(shotShots\.pop\(\)\.url\);",
            self.seg)
        self.assertIsNotNone(m, "归档驱逐逻辑缺失或写法被改")
        self.assertEqual(self.seg.count("URL.revokeObjectURL"), 1)

    def test_strip_renders_text_only(self):
        # AGENTS.md：设备名来自局域网广播、可伪造，渲染必须 textContent
        seg = self.seg[self.seg.index("function shotRenderStrip"):]
        seg = seg[:seg.index("function shotCapText")]
        self.assertNotIn("innerHTML", seg)
        self.assertIn("textContent", seg)

    def test_hotkey_requires_topmost_modal(self):
        # 快捷键只在截屏弹窗是顶层弹窗时生效（命令面板开着时 +-0 属于输入框）
        hotkey = self.seg[self.seg.index('document.addEventListener("keydown"'):]
        self.assertIn('openModals[openModals.length - 1] !== $("#shotModal")', hotkey)
        self.assertIn("}, true);", hotkey)

    def test_no_hardcoded_color_in_shot_section(self):
        css = (STATIC / "style.css").read_text(encoding="utf-8")
        m = re.search(r"/\* -+ 截屏工作台.*?(?=\n/\* -+|\n\.hidden)", css, re.S)
        self.assertIsNotNone(m, "style.css 缺少截屏工作台样式段")
        seg = m.group(0)
        self.assertNotRegex(seg, r"#[0-9a-fA-F]{3,8}\b")
        self.assertIn(".shotstage", seg)
        self.assertIn("touch-action: none", seg)


class SelectorDriftTest(unittest.TestCase):
    def test_literal_selector_ids_exist(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        ids = set(re.findall(r'id="([^"]+)"', html))
        refs = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js))
        missing = sorted(refs - ids - DYNAMIC_IDS)
        self.assertEqual(missing, [], "app.js 引用了不存在的 id：" + ", ".join(missing))
