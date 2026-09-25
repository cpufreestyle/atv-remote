"""按键自定义（第十八轮）静态资产回归：结构 / ARIA 契约 / 零回归 wiring / 安全约定。

与命令面板、截屏工作台同款思路——前端功能没有 HTTP 行为可测，但四类回归必须挡住：
1. 改键弹窗结构与 ARIA 契约被后续改动破坏（role=list / aria-modal 是读屏与键盘
   可达性的地基）；
2. 零回归：未改绑的键必须仍走 sendKey(code)（历史行为）。kmSend 只是分发外壳，
   谁把它「顺手简化」成直接调用绑定逻辑，全量按键行为就静默变了；
3. AGENTS.md 禁令回潮：动作名来自应用目录 / 宏名（用户可编辑），渲染必须用
   textContent；新样式段不得写死 hex；自定义内容只进 localStorage，不碰 state.json；
4. $("#id") 引用漂移：app.js 引用了 index.html 里不存在的 id（static/static/
   那种「安静事故」同款风险）。

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


class KeymapMarkupTest(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_dialog_contract(self):
        for frag in ('id="keymapModal"', 'class="modal hidden"', 'role="dialog"',
                     'aria-modal="true"', 'aria-label="按键自定义"',
                     'class="mbox kmbox"', 'id="kmKeyLabel"', 'id="kmCur"',
                     'id="kmEmpty"', 'class="kmempty hidden"',
                     'id="kmList"', 'role="list"', 'aria-label="可绑定动作"'):
            self.assertIn(frag, self.html)

    def test_foot_buttons_and_autofocus(self):
        for frag in ('<button id="kmUnbind" class="btn tiny"',
                     '<button id="kmResetAll" class="btn tiny danger"',
                     '<button id="kmClose" class="btn" data-autofocus>完成</button>'):
            self.assertIn(frag, self.html)

    def test_hint_bar_and_quick_reset(self):
        # 可发现性：按键卡片里必须告诉用户「长按可改绑」，否则功能没人知道
        for frag in ('class="kmhintbar"', 'id="kmHintBar"', 'id="kmResetAllBtn"',
                     "长按任意键可改绑"):
            self.assertIn(frag, self.html)


class KeymapScriptTest(unittest.TestCase):
    def setUp(self):
        self.js = (STATIC / "app.js").read_text(encoding="utf-8")
        # 段尾取下一节唯一锚点（「屏幕常亮」在文件里只出现一次）
        self.seg = self.js[self.js.index("/* ---------------- 按键自定义"):
                           self.js.index("/* ---------------- 屏幕常亮（Wake Lock）")]

    def test_syntax(self):
        r = subprocess.run(["node", "--check", str(STATIC / "app.js")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_wiring(self):
        for frag in ('const KM_LS = "atv.keymap_v1"', "const KM_EDIT_MS = 550",
                     "loadKeymap()", "saveKeymap()", "function kmFind(",
                     "function kmSend(", "function kmHoldable(", "function kmActions(",
                     "function kmRenderList(", "function kmOpen(", "function kmBind(",
                     "function kmResetAll(", "function kmRefreshMarks(",
                     'if (action === "none")', 'action.startsWith("key:")',
                     'action.startsWith("app:")', 'action.startsWith("macro:")',
                     'setAttribute("aria-current", "true")',
                     "localStorage.setItem(KM_LS", 'closeModal("#keymapModal")'):
            self.assertIn(frag, self.seg)

    def test_unmapped_key_falls_back_to_sendkey(self):
        # 零回归：没改绑的键必须仍走 sendKey(code)，kmSend 只是个分发外壳
        m = re.search(r"async function kmSend\(code\) \{.*?\n\}", self.seg, re.S)
        self.assertIsNotNone(m, "kmSend 缺失或签名被改")
        self.assertIn("if (!action) return sendKey(code);", m.group(0))

    def test_find_guards_empty_action(self):
        # 回归：没改绑的键 kmMap[key] 是 undefined，kmFind 必须先返回 null。
        # 上一版直接 action.startsWith(...)：刷新标记 / 长按弹窗一进就 TypeError，
        # 表现是「长按没反应」。
        m = re.search(r"function kmFind\(action\) \{(.*?)\n\}", self.seg, re.S)
        self.assertIsNotNone(m, "kmFind 缺失或签名被改")
        self.assertIn("if (!action) return null;", m.group(0))

    def test_none_action_runs_noop(self):
        # 「设为无操作」：按下什么都不做，但不能连发（kmHoldable 里 action==="none" 拦掉）
        body = self.seg[self.seg.index('if (action === "none")'):]
        body = body[:body.index("\n")]
        self.assertIn("run: () => {}", body)

    def test_list_renders_text_only(self):
        # 动作名来自应用目录 / 宏名（用户可编辑）：全程 DOM API，禁 innerHTML
        seg = self.seg[self.seg.index("function kmRenderList"):]
        seg = seg[:seg.index("function kmOpen(")]
        self.assertNotIn("innerHTML", seg)
        self.assertIn("document.createElement", seg)
        self.assertIn("textContent", seg)

    def test_marks_use_class_toggle_and_title(self):
        seg = self.seg[self.seg.index("function kmRefreshMarks"):]
        self.assertIn('classList.toggle("remapped"', seg)
        self.assertIn('btn.title = "按下："', seg)
        self.assertIn('removeAttribute("title")', seg)

    def test_events_wired(self):
        for frag in ('$("#kmClose").addEventListener',
                     '$("#keymapModal").addEventListener',
                     '$("#kmList")', 'closest("[data-a]")',
                     '$("#kmUnbind").addEventListener',
                     '$("#kmResetAll").addEventListener',
                     '$("#kmResetAllBtn").addEventListener'):
            self.assertIn(frag, self.seg)

    def test_custom_data_stays_out_of_state_json(self):
        # AGENTS.md：state.json 敏感（含配对凭据），自定义改键只进 localStorage
        # 注释里提 state.json 是说明缘由，剥掉注释只看代码
        code = re.sub(r"/\*.*?\*/", "", self.seg, flags=re.S)
        code = re.sub(r"//[^\n]*", "", code)
        self.assertNotIn("state.json", code)
        self.assertNotIn("saveState", self.seg)
        self.assertIn("localStorage.setItem(KM_LS", code)


class KeyButtonRouteTest(unittest.TestCase):
    """遥控键重接：pointerdown / click(detail===0) / 连发 tick 必须全走 kmSend。"""

    def setUp(self):
        self.js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.seg = self.js[self.js.index('$$("[data-key]")'):
                           self.js.index('$("#settingsBtn").addEventListener')]

    def test_all_send_paths_route_through_kmsend(self):
        self.assertEqual(self.seg.count("kmSend(code)"), 3)  # tick / pointerdown / click

    def test_longpress_opens_editor(self):
        # 长按 550ms 进改键弹窗；触发时先停连发，别让用户白白多发几个键
        self.assertIn("stop(); kmOpen(code, btn)", self.seg)
        self.assertIn("KM_EDIT_MS", self.seg)

    def test_hold_follows_bound_action(self):
        # 连发按绑定目标推导：启动器 / 宏不连发（连发启动器 = 连环重启 App）
        self.assertIn("kmHoldable(bound())", self.seg)
        seg = self.js[self.js.index("function kmHoldable"):
                      self.js.index("function kmActions(")]
        self.assertIn("HOLD_KEYS.has(+action.slice(4))", seg)

    def test_keyboard_click_guard(self):
        # 指针点击已在 pointerdown 发过：click 只服务键盘（detail===0），防双重发送
        self.assertIn("if (e.detail !== 0) return;", self.seg)
        self.assertIn("clearTimeout(edit)", self.seg)


class KeymapStyleTest(unittest.TestCase):
    def setUp(self):
        self.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def test_no_hardcoded_color_in_keymap_section(self):
        m = re.search(r"/\* -+ 按键自定义.*?(?=\n/\* -+|\n\.hidden)", self.css, re.S)
        self.assertIsNotNone(m, "style.css 缺少按键自定义样式段")
        seg = m.group(0)
        self.assertNotRegex(seg, r"#[0-9a-fA-F]{3,8}\b")
        for frag in (".kmhintbar", ".kmbox", ".kmbar", ".kmlist", ".kmgroup",
                     ".kmrow", ".kmlabel", ".kmfoot", ".kmempty", ".remapped"):
            self.assertIn(frag, seg)

    def test_row_touch_target_and_current_state(self):
        # ≥36px 触控目标（同命令面板 palrow 口径）；当前绑定用 accent 边 + aria-current
        self.assertIn("min-height: 36px", self.css)
        self.assertIn('.kmrow[aria-current="true"]', self.css)


class SelectorDriftTest(unittest.TestCase):
    def test_literal_selector_ids_exist(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        ids = set(re.findall(r'id="([^"]+)"', html))
        refs = set(re.findall(r'\$\$\("#([A-Za-z0-9_-]+)"\)', js))
        missing = sorted(refs - ids - DYNAMIC_IDS)
        self.assertEqual(missing, [], "app.js 引用了不存在的 id：" + ", ".join(missing))


if __name__ == "__main__":
    unittest.main()
