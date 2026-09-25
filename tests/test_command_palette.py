"""命令面板（第十六轮）静态资产回归：结构 / ARIA 契约 / 选择器引用一致性 / 安全禁令。

前端功能没有 HTTP 行为可测，但三类回归必须挡住：
1. 面板结构与 ARIA 契约被后续改动破坏（combobox/listbox 是键盘可达性的地基）；
2. app.js 里 $("#id") 引用了不存在的 id——新 ID 打错字时功能静默失效
   （历史上 static/static/ 那种「安静事故」同款风险），宏表单动态字段走白名单；
3. AGENTS.md 禁令回潮：设备名 / IP 用 innerHTML 渲染，新组件里写死颜色。

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


class PaletteMarkupTest(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_dialog_contract(self):
        for frag in ('id="cmdpal"', 'class="modal hidden pal"',
                     'role="dialog"', 'aria-modal="true"', 'aria-label="命令面板"'):
            self.assertIn(frag, self.html)

    def test_combobox_listbox_contract(self):
        for frag in ('id="palInput"', 'role="combobox"', 'aria-expanded="true"',
                     'aria-controls="palList"', 'aria-autocomplete="list"', "data-autofocus",
                     'id="palList"', 'role="listbox"', 'id="palTip"', 'id="palBtn"'):
            self.assertIn(frag, self.html)

    def test_trigger_button_discovers_hotkey(self):
        m = re.search(r'<button id="palBtn"[^>]*>', self.html)
        self.assertTrue(m)
        self.assertIn("aria-label", m.group(0))
        self.assertIn("Ctrl", m.group(0))   # 快捷键写进 tooltip 才可发现


class PaletteScriptTest(unittest.TestCase):
    def setUp(self):
        self.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_syntax(self):
        r = subprocess.run(["node", "--check", str(STATIC / "app.js")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_wiring(self):
        for frag in ("const PAL_KEY", "palScore(", "palCommands(", "palRender(",
                     "palSelect(", "palMove(", "palExec(", "palOpen()", "palClose()"):
            self.assertIn(frag, self.js)
        self.assertIn('e.key !== "k"', self.js)          # 全局热键只认 k
        self.assertIn("aria-activedescendant", self.js)  # 读屏跟随选中项

    def test_palette_rows_render_text_only(self):
        # AGENTS.md：设备名 / IP 来自局域网广播、可伪造，渲染必须 textContent
        seg = self.js[self.js.index("function palRender"):]
        seg = seg[:seg.index("function palSelect")]
        self.assertNotIn("innerHTML", seg)

    def test_no_hardcoded_color_in_palette_section(self):
        css = (STATIC / "style.css").read_text(encoding="utf-8")
        m = re.search(r"/\* -+ 命令面板.*?(?=\n/\* -+|\n\.hidden)", css, re.S)
        self.assertIsNotNone(m, "style.css 缺少命令面板样式段")
        self.assertNotRegex(m.group(0), r"#[0-9a-fA-F]{3,8}\b")


class SelectorDriftTest(unittest.TestCase):
    def test_literal_selector_ids_exist(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        ids = set(re.findall(r'id="([^"]+)"', html))
        refs = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js))
        missing = sorted(refs - ids - DYNAMIC_IDS)
        self.assertEqual(missing, [], "app.js 引用了不存在的 id：" + ", ".join(missing))
