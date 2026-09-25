"""通知队列 / 通知中心（第二十一轮）回归。

立论来自一个具体的痛处：旧版 toast 是单例，下一条顶掉上一条。报错滚过去就再也找不回，
用户只能靠记忆复述「刚才那条说啥」。学 macOS 通知中心 / VS Code 通知：浮层同屏最多 3 条、
停留时长随级别拉长、全部入账，🔔 上挂未读数，面板里可整段重看。

所以这里最关键的几条：
  1) 级别判定矩阵——40+ 个调用点零改动，级别全靠从文案推，判错就全盘皆错；
  2) 同屏上限与 hidden 计数——被挤出屏幕的没消失，只是进了历史；
  3) 历史合并与截断——1.2s 内同消息合并（别刷成复读机），上限 50 条（别无限涨）；
  4) 坏 localStorage 数据不能把整个通知中心打挂；
  5) 浮层 pointer-events:none——旧单条提示条实测吞掉过应用按钮点击；
  6) 通知文本一律 textContent——设备名 / IP 来自可伪造的局域网广播；
  7) 通知只落 localStorage，不进 state.json（敏感文件不放可编辑内容）；
  8) notifInit() 必须挂在启动序列里，而不是塞在某个按钮的回调中。

只用标准库 + 读仓库文件 + 跑 node harness：不起端口、不联网、不碰 adb/pyatv。
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "notif_queue_harness.js"

NOTIF_BEGIN = "/* ===== notif-queue:begin"
NOTIF_END = "/* ===== notif-queue:end"
CSS_MARK = "通知队列 + 通知中心"


def strip_js_comments(s):
    """剥掉块注释 / 行注释后的「真代码」——禁用词检查不能把注释里的说明文字当违规。"""
    return re.sub(r"/\*[\s\S]*?\*/", " ", re.sub(r"//[^\n]*", " ", s))


class NotifMarkupTest(unittest.TestCase):
    """markup：容器 / 徽标 / 弹窗契约齐备，旧 #toast 单例已退役。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def test_old_singleton_toast_is_retired(self):
        self.assertNotIn('id="toast"', self.html)
        # 不能用裸 "toast-in"——"ntoast-in" 是它的子串，会误报，故取精确串
        for frag in ("toastTimer", "@keyframes toast-in", "#toast {"):
            self.assertNotIn(frag, self.js)
            self.assertNotIn(frag, self.css)
        # 入口仍在，只是换了实现——40+ 个调用点零改动
        self.assertIn("function toast(msg, isInfo", self.js)

    def test_stack_is_a_polite_live_region(self):
        self.assertIn('<div id="notifStack" class="nstack" role="status" aria-live="polite"',
                      self.html)
        self.assertIn('aria-label="通知"', self.html)

    def test_overflow_button_hidden_until_it_has_something_to_say(self):
        self.assertIn('<button id="notifMore" class="nmore hidden" type="button">', self.html)

    def test_panel_is_a_real_dialog(self):
        self.assertIn('<div id="notifPanel" class="modal hidden npanel" role="dialog"',
                      self.html)
        self.assertIn("aria-modal=\"true\"", self.html)

    def test_panel_has_head_list_and_foot(self):
        for frag in ('<span id="notifCount" class="npanelcount"></span>',
                     '<button id="notifClearBtn" class="btn tiny" type="button">清空</button>',
                     '<ul id="notifList" class="npanellist" role="list"></ul>'):
            self.assertIn(frag, self.html)

    def test_bell_button_carries_hidden_badge(self):
        for frag in ('id="notifBtn" class="btn tiny gear notifbtn"',
                     'title="通知中心（Ctrl / ⌘ + N）"',
                     '<span id="notifBadge" class="notifbadge hidden" aria-hidden="true"></span>'):
            self.assertIn(frag, self.html)


class WiringAndPurityTest(unittest.TestCase):
    """接线与纯度：入口已挂对位置、旧调用点零改动、纯函数段可搬进 node、渲染不走 innerHTML。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def seg(self):
        s = self.js.index(NOTIF_BEGIN)
        e = self.js.index(NOTIF_END)
        return self.js[s:e]

    def test_init_runs_in_bootstrap_not_in_a_button_callback(self):
        # 曾经误插进 #privacyBtn 的 click 回调：不点隐私按钮就永不初始化，
        # 每点一次还重复挂一遍监听。启动序列里只许出现一次。
        self.assertEqual(self.js.count("notifInit();"), 1, "notifInit 只应在启动序列调用一次")
        boot_at = self.js.index("applyPrivacy();\nrenderPhrases();\nrenderHist();\n")
        call_at = self.js.index("notifInit();")
        self.assertLess(boot_at, call_at, "notifInit 应排在 renderHist 之后")
        self.assertLess(call_at, self.js.index("refreshStatus();", boot_at),
                        "notifInit 应排在 refreshStatus 之前")
        pi = self.js.index('$("#privacyBtn").addEventListener')
        privacy_body = self.js[pi:self.js.index("});", pi)]
        self.assertNotIn("notifInit", privacy_body)

    def test_toast_entry_point_is_back_compatible(self):
        self.assertIn("function toast(msg, isInfo = false) { notifPush(msg, isInfo); }", self.js)
        self.assertIn("function notifPush(msg, isInfo) {", self.js)
        self.assertIn("notifShow(item.msg, level);", self.js)

    def test_pure_segment_has_all_rules(self):
        seg = self.seg()
        for frag in ("function notifNormLevel(", "function notifLevel(", "function notifDur(",
                     "function notifPlan(", "function notifCoalesce(", "function notifPushHistory(",
                     "function notifUnread(", "function notifFmtTime(",
                     "function notifSanitizeHistory(",
                     "const NOTIF_MAX_VISIBLE", "const NOTIF_HISTORY_MAX",
                     "const NOTIF_COALESCE_MS"):
            self.assertIn(frag, seg)

    def test_pure_segment_touches_no_dom_or_network(self):
        code = strip_js_comments(self.seg())
        for banned in ("document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("):
            self.assertNotIn(banned, code)

    def test_push_history_uses_coalesce_and_cap(self):
        seg = self.seg()
        self.assertIn("if (arr.length && notifCoalesce(arr[0], item))", seg)
        self.assertIn("arr.unshift(item);", seg)      # 最新在前，不是 push 后 reverse
        self.assertIn("return arr.slice(0, cap);", seg)

    def test_notifications_render_text_only(self):
        # 设备名 / IP 来自可伪造的局域网广播，必须 textContent
        self.assertIn("txt.textContent = n.msg;", self.js)
        self.assertIn("msg.textContent = it.msg;", self.js)
        for start in ("function notifMount(n) {", "function notifRenderPanel() {"):
            i = self.js.index(start)
            body = self.js[i:self.js.index("\nfunction ", i + len(start))]
            self.assertNotIn("innerHTML", body)
            self.assertNotIn("insertAdjacentHTML", body)

    def test_panel_reuses_shared_modal_behaviour(self):
        # Esc 关窗 / 焦点归还 / Tab 循环都由 openModal 统一负责
        self.assertIn('openModal("#notifPanel", btn || $("#notifBtn"));', self.js)
        self.assertIn('closeModal("#notifPanel")', self.js)

    def test_hotkey_and_palette_entry_exist(self):
        self.assertIn('e.key !== "n" && e.key !== "N"', self.js)
        # hotkey / palette share one switch helper, no duplicated toggle logic
        self.assertIn("function notifToggle(btn) {", self.js)
        self.assertIn("  notifToggle();", self.js)
        self.assertIn('() => notifToggle($("#notifBtn")));', self.js)
        self.assertIn('push("notif", "', self.js)

    def test_bell_button_exposes_its_expanded_state(self):
        self.assertIn("function notifSyncBtn() {", self.js)
        self.assertIn('btn.setAttribute("aria-pressed", on ? "true" : "false");', self.js)
        self.assertIn('btn.setAttribute("aria-expanded", on ? "true" : "false");', self.js)
        self.assertIn('panel.addEventListener("modalclosed", notifSyncBtn);', self.js)
        self.assertIn("  notifSyncBtn();", self.js)

    def test_panel_open_marks_new_notifications_read(self):
        self.assertIn("if (notifPanelOpen()) {", self.js)
        self.assertIn("notifLastSeen = item.ts;", self.js)


class HarnessParityTest(unittest.TestCase):
    """harness 抽的就是 app.js 里那一段：常量与 helper 必须同源，漂移即失败。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.seg = cls.js[cls.js.index(NOTIF_BEGIN):cls.js.index(NOTIF_END)]
        cls.harness = HARNESS.read_text(encoding="utf-8")

    def test_constants_match_source_literals(self):
        lit = dict(re.findall(r"^const (NOTIF_[A-Z_]+) = (.+);$", self.seg, re.M))
        self.assertTrue(lit)
        self.assertEqual(lit["NOTIF_MAX_VISIBLE"], "3")
        self.assertEqual(lit["NOTIF_HISTORY_MAX"], "50")
        self.assertEqual(lit["NOTIF_COALESCE_MS"], "1200")
        self.assertEqual(lit["NOTIF_MSG_MAX"], "200")
        self.assertEqual(lit["NOTIF_HISTORY_KEY"], '"atv.notif.history"')
        self.assertEqual(lit["NOTIF_SEEN_KEY"], '"atv.notif.seen"')
        self.assertIn("{ ok: 2600, info: 3400, err: 7000 }", lit["NOTIF_DUR"])
        self.assertEqual(lit["NOTIF_LEVELS"], '["ok", "info", "err"]')

    def test_harness_references_the_same_symbols(self):
        for sym in ("NOTIF_MAX_VISIBLE", "NOTIF_HISTORY_MAX", "NOTIF_COALESCE_MS",
                    "notifLevel", "notifPlan", "notifPushHistory", "notifUnread",
                    "notifFmtTime", "notifSanitizeHistory", "ALL_NOTIF_CASES_PASSED"):
            self.assertIn(sym, self.harness)

    def test_harness_uses_same_segment_markers(self):
        self.assertIn(NOTIF_BEGIN, self.harness)
        self.assertIn(NOTIF_END, self.harness)


class HarnessBehaviorTest(unittest.TestCase):
    """真行为：node harness 从 app.js 抽出纯函数段原样执行，必须全绿。"""

    def test_harness_passes(self):
        if shutil.which("node") is None:
            self.skipTest("没有 node，跳过 harness")
        p = subprocess.run(["node", str(HARNESS)], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ALL_NOTIF_CASES_PASSED", p.stdout)
        failed = re.findall('"failed"[^0-9]*([0-9]+)', p.stdout)
        total = re.findall('"total"[^0-9]*([0-9]+)', p.stdout)
        self.assertTrue(failed and failed[0] == "0", p.stdout)
        self.assertTrue(total and int(total[0]) >= 12, p.stdout)


class StyleTest(unittest.TestCase):
    """浮层样式：语义色、可点区域克制、列表面板有结构与滚动。"""

    @classmethod
    def setUpClass(cls):
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def seg(self):
        i = self.css.index(CSS_MARK)
        j = self.css.index("/* ----------", i + 1)   # 下一段样式起点
        return self.css[i:j]

    def test_section_exists_and_uses_only_semantic_tokens(self):
        seg = self.seg()
        self.assertNotIn("rgb(", seg)
        self.assertNotIn("hsl(", seg)
        self.assertFalse(re.findall(r"#[0-9a-fA-F]{3,8}([^0-9a-zA-Z]|$)", seg))

    def test_toast_levels_and_animations(self):
        seg = self.seg()
        for frag in (".ntoast {", ".ntoast.ok {", ".ntoast.info {", ".ntoast.out {",
                     "animation: ntoast-in", "@keyframes ntoast-in", "@keyframes ntoast-out",
                     ".ntoast .ntext {", ".ntoast .ntof {"):
            self.assertIn(frag, seg)

    def test_overlay_does_not_eat_clicks(self):
        # 旧单条提示条实测吞掉过应用按钮点击：浮层 none，只有 × 与「还有 N 条」放行
        seg = self.seg()
        self.assertIn("#notifStack {", seg)
        self.assertIn("pointer-events: none", seg)
        self.assertIn("pointer-events: auto", seg)
        self.assertIn(".nmore {", seg)

    def test_panel_rows_have_dot_time_and_message(self):
        seg = self.seg()
        for frag in (".npanel .npanelbox {", ".npanelhead {", ".npanellist {", ".nrow {",
                     ".nrow .ndot {", ".nrow .ntime {", ".nrow .nmsg {", ".nempty {"):
            self.assertIn(frag, seg)

    def test_list_scrolls_instead_of_growing(self):
        seg = self.seg()
        self.assertIn("max-height:", seg)
        self.assertIn("overflow-y: auto", seg)

    def test_badge_uses_semantic_colors(self):
        seg = self.seg()
        self.assertIn(".notifbtn { position: relative; }", seg)
        self.assertIn(".notifbadge {", seg)
        self.assertIn("var(--danger-text)", seg)
        self.assertIn("var(--danger)", seg)

    def test_message_column_can_shrink(self):
        # 窄屏下长消息要保住 60px 时间列、消息列断行，而不是把网格撑破
        seg = self.seg()
        self.assertIn("grid-template-columns: 8px 60px minmax(0, 1fr)", seg)
        self.assertIn("min-width: 0", seg)


class StorageContractTest(unittest.TestCase):
    """通知是本机缓存：走 localStorage；state.json 不该知道通知的存在。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.server = (ROOT / "server.py").read_text(encoding="utf-8")

    def test_history_and_seen_keys_go_to_local_storage(self):
        for frag in ("function notifStore(key, val) {",
                     "function notifLoad(key) {",
                     "function notifLoadHistory() {",
                     "function notifSaveHistory() { notifStore(NOTIF_HISTORY_KEY, JSON.stringify(notifHistory)); }",
                     "function notifSaveSeen() { notifStore(NOTIF_SEEN_KEY, String(notifLastSeen)); }"):
            self.assertIn(frag, self.js)

    def test_storage_failure_degrades_quietly(self):
        # 隐私模式 / 配额满不能让通知把主功能拖死
        self.assertIn("catch (e) { /* 隐私模式 / 配额满", self.js)
        self.assertIn("try { return notifSanitizeHistory(", self.js)

    def test_server_side_has_no_notification_state(self):
        for name in ("server.py", "atv_backend.py"):
            src = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("notif", src.lower(),
                             name + " 不该知道通知的存在（纯前端能力）")

    def test_state_json_key_set_is_frozen(self):
        # 与「可编辑内容只进 localStorage」的约定对齐：state.json 的键集合固定，
        # 新增前端可编辑内容不得往里塞（它会带上配对凭据被打包分发）。
        self.assertIn('state = {"recent_android": [], "appletvs": [], "current": None, "info": {},',
                      self.server)
        for key in ("notif", "privacy"):
            self.assertNotIn(key, self.server.lower())


if __name__ == "__main__":
    unittest.main()
