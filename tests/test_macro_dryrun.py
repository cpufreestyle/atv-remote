"""宏预演 dry-run（第十九轮）回归：markup 契约 / 与服务端规则同源 / 零副作用 / harness 真行为。

本轮的立论是「计划与执行同源」（学 Terraform plan/apply、Ansible --check）：预演放行的
宏，服务端必须照样放行；预演拦下的问题，服务端必须照样拒绝。所以这里最关键的一条测试是
常量比对——app.js 里的 FE 常量与 server.py 逐个相等，改一边忘另一边测试就红。

真行为由 node harness（tests/macro_dryrun_harness.js）从 app.js 抽出纯函数段原样执行给出，
本文件的 HarnessBehaviorTest 负责驱动它并断言全绿。

只读 static/ 与 tests/ 文件 + 跑 node，不起服务、不碰网络。
"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "macro_dryrun_harness.js"

def dry_seg(js):
    """抽出 macro-dry-run 纯函数段（marker 之间），与 harness 同一套边界。"""
    s = js.index("/* ===== macro-dry-run:begin")
    e = js.index("/* ===== macro-dry-run:end")
    return js[s:e]


class MarkupTest(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_dry_button_and_panel(self):
        for frag in ('<button id="macroDryBtn" class="btn"',
                     'title="\u672c\u5730\u9759\u6001\u68c0\u67e5\uff0c\u4e0d\u53d1\u9001\u3001\u4e0d\u6267\u884c\uff08\u2318/Ctrl+Enter\uff09"',
                     '\U0001f50d \u9884\u6f14</button>',
                     '<div id="macroDry" class="mdry hidden" aria-live="polite"></div>',
                     '\u9884\u6f14\u4e0d\u6267\u884c\u4efb\u4f55\u64cd\u4f5c'):
            self.assertIn(frag, self.html)

    def test_button_sits_with_run_and_save(self):
        # 三个按钮同一行：运行 / 保存 / 预演，预演是运行前的第三步，不是藏在折叠里
        row = self.html[self.html.index('id="macroRunCustomBtn"'):self.html.index('id="macroDry"')]
        self.assertIn('id="macroSaveCustomBtn"', row)
        self.assertIn('id="macroDryBtn"', row)


class WiringAndPurityTest(unittest.TestCase):
    def setUp(self):
        self.js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.seg = dry_seg(self.js)

    def test_syntax(self):
        r = subprocess.run(["node", "--check", str(STATIC / "app.js")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_pure_segment_has_no_side_effects(self):
        # 纯函数段：不碰 DOM、不碰存储、不发请求——否则 harness 抽不出来、预演也不再是零副作用
        for bad in ("document.", "localStorage", "fetch(", "$(#", "api(", "toast(", "buzz("):
            self.assertNotIn(bad, self.seg, bad)

    def test_dom_glue_wired(self):
        for frag in ('function macroShowDryRun() {',
                     '$("#macroDryBtn")?.addEventListener("click", macroShowDryRun);',
                     'imeCurrent: imeState.current,',
                     'curType: status.curType,',
                     'knownPkgs: KNOWN_PKGS_FOR_DRY(),',
                     '$("#macroText").addEventListener("keydown"',
                     'if (e.key === "Enter" && (e.ctrlKey || e.metaKey))'):
            self.assertIn(frag, self.js)

    def test_rows_rendered_text_only(self):
        # 行内容全部来自用户 JSON（宏名/包名/文本），一律 textContent，禁 innerHTML
        seg = self.js[self.js.index("function macroShowDryRun"):]
        seg = seg[:seg.index("\n}")]
        self.assertNotIn("innerHTML", seg)
        self.assertIn("textContent", seg)

    def test_panel_unhidden_on_both_paths(self):
        # CDP 抓到的真 bug：成功路径忘了 box.classList.remove("hidden")
        # 内容全渲染好了、面板还带着 hidden，用户点「预演」什么都没发生
        seg = self.js[self.js.index("function macroShowDryRun"):]
        seg = seg[:seg.index("\n}")]
        self.assertEqual(seg.count("classList.remove(\"hidden\")"), 2,
                         "解析失败与成功渲染两条路径都必须解除 hidden")



class ServerParityTest(unittest.TestCase):
    """预演规则必须逐条镜像服务端 validate_macro_steps——这是本轮的立论。"""

    def setUp(self):
        self.server = (ROOT / "server.py").read_text(encoding="utf-8")
        self.js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.seg = dry_seg(self.js)

    def _server_const(self, name):
        m = re.search(r"^%s = (.+)$" % re.escape(name), self.server, re.M)
        self.assertIsNotNone(m, "server.py 缺少常量 " + name)
        return m.group(1).split("#")[0].strip()  # 服务端常量常带行尾注释，剥掉再比

    def _fe_const(self, name):
        m = re.search(r"^const %s = (.+?);" % re.escape(name), self.seg, re.M)
        self.assertIsNotNone(m, "app.js dry 段缺少常量 " + name)
        return m.group(1).split("//")[0].strip()

    def test_step_and_delay_limits(self):
        self.assertEqual(self._server_const("MACRO_MAX_STEPS"), self._fe_const("MACRO_MAX_STEPS_FE"))
        self.assertEqual(self._server_const("MACRO_MAX_DELAY"), self._fe_const("MACRO_MAX_DELAY_FE"))
        self.assertEqual(self._server_const("MAX_KEYCODES"), self._fe_const("MAX_KEYCODES_FE"))
        self.assertEqual(self._server_const("MAX_TEXT_LEN"), self._fe_const("MAX_TEXT_LEN_FE"))

    def test_step_types(self):
        srv = re.search(r"MACRO_STEP_TYPES = \((.*?)\)", self.server, re.S).group(1)
        srv_types = set(re.findall(r'"([a-z]+)"', srv))
        fe = re.search(r"MACRO_STEP_TYPES_FE = \[(.*?)\]", self.seg, re.S).group(1)
        fe_types = set(re.findall(r'"([a-z]+)"', fe))
        self.assertEqual(srv_types, fe_types)

    def test_app_id_regex(self):
        srv = re.search(r'APP_ID_RE = re\.compile\(r"(.*?)"\)', self.server).group(1)
        fe = re.search(r"MACRO_APP_ID_RE_FE = /(.*?)/;", self.seg).group(1)
        self.assertEqual(srv, fe, "包名正则两边不一致，预演会放过服务端拒绝的包名")

    def test_keycode_digit_check_mirrors_server(self):
        # 服务端 str(c).lstrip("-").isdigit() 的 JS 等价（-25 服务端放行、设备会失败）
        self.assertIn("/^[0-9]+$/.test(String(c).replace(/^-+/, \"\"))", self.seg)


class HarnessBehaviorTest(unittest.TestCase):
    """驱动 node harness：纯函数段的真行为证据。"""

    def test_harness_all_cases_pass(self):
        r = subprocess.run(["node", str(HARNESS)], cwd=str(ROOT),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALL_DRYRUN_CASES_PASSED", r.stdout)
        m = re.search(r"total[^0-9]*([0-9]+)[^0-9]*failed[^0-9]*([0-9]+)", r.stdout)
        self.assertIsNotNone(m, r.stdout)
        self.assertEqual(int(m.group(2)), 0)
        self.assertGreaterEqual(int(m.group(1)), 15)


class StyleTest(unittest.TestCase):
    def setUp(self):
        self.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def test_dry_section_exists_and_uses_tokens(self):
        m = re.search(r"/\* -+ \u5b8f\u9884\u6f14\uff08dry-run\uff09 -+ \*/.*$", self.css, re.S)
        self.assertIsNotNone(m, "style.css 缺少宏预演样式段")
        seg = m.group(0)
        self.assertNotRegex(seg, r"#[0-9a-fA-F]{3,8}\b")
        for frag in (".mdry", ".mdrysum", ".mdrysum.warn", ".mdrysum.err",
                     ".mdry .drow", ".mdry .drow.ok", ".mdry .drow.warn", ".mdry .drow.err",
                     ".mdry .drow .dnote"):
            self.assertIn(frag, seg)
        self.assertIn("var(--ok-text)", seg)
        self.assertIn("var(--warn-text)", seg)
        self.assertIn("var(--danger-text)", seg)


class SelectorDriftTest(unittest.TestCase):
    def test_literal_selector_ids_exist(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        ids = set(re.findall(r'id="([^"]+)"', html))
        refs = set(re.findall(r'\$\$\("#([A-Za-z0-9_-]+)"\)', js))
        missing = sorted(refs - ids)
        self.assertEqual(missing, [], "app.js 引用了不存在的 id")


if __name__ == "__main__":
    unittest.main()
