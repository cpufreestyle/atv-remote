#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""撤销 Snackbar（第二十七轮）回归。

动机：设置弹窗的「重置常用短语 / 清空自定义宏」、按键映射的「恢复默认按键」、
以及单个自定义宏的删除按钮，都是点一下就地生效、没有任何反悔机会的破坏性操作。
遥控器是拿在手里看电视时用的，误触一下要把自己攒的短语 / 宏 / 改键全部重建，
代价远大于其他误触。本轮学 Gmail「已删除·撤销」/ Material Snackbar action /
Radix Toast action 的行为契约：破坏性操作照旧立即执行，但给 6s 反悔窗口。

验七件事（标准库 + 读仓库文件 + node harness，离线可跑）：
  1) undo 纯函数段可搬进 node：过期判定 / 同 id 去重 / 二次撤销有真行为证据；
  2) DOM 胶水在段外、且落在 tabs 绑定之前（undoStack / undoSeq 是 let，绑定时必须已初始化）；
  3) 四个破坏性入口全部改走 undoable()，且快照抓在 undoable() 之前；
  4) undoable / undoRun / undoToast 式的计数只有一处定义，撤销回调包在 try 里；
  5) 通知层扩展是向后的：notifShow 收第三个 act 参数，act 缺省时行为与旧版一致；
  6) .nact 触控目标 36px、颜色走 --accent-text，且渲染走 createElement + textContent；
  7) 版本号单一来源（VERSION）随轮推进，别处没有硬编码。
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "undo_harness.js"
SEG_BEGIN = "/* ===== undo:begin"
SEG_END = "/* ===== undo:end"

BANNED = ("document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$(")

# 四个破坏性入口 → (入口锚点, 结束锚点, 人类可读名)
ENTRIES = [
    ('$("#clearPhrasesBtn").addEventListener', '$("#clearMacrosBtn").addEventListener', "重置常用短语"),
    ('$("#clearMacrosBtn").addEventListener', '$("#coachBtn").addEventListener', "清空自定义宏"),
    ('del.addEventListener("click"', 'const wrap = document.createElement("span")', "删除单个自定义宏"),
    ("function kmResetAll() {", "// 改过的键带", "恢复默认按键"),
]


def _glue(js):
    s = js.find(SEG_END)
    if s < 0:
        return ""
    e = js.find("/* tabs */", s)
    # 胶水段到 tabs 绑定为止，别把后面 tabs 的 classList 圈进断言
    return js[s:e] if e >= 0 else js[s:s + 3000]


def _block(js, start, end):
    s = js.find(start)
    if s < 0:
        return ""
    e = js.find(end, s)
    return js[s:e] if e >= 0 else js[s:]


def _arrow_count(text):
    return len(re.findall(r"\(\)\s*=>", text))


class PureSegmentTest(unittest.TestCase):
    """规则段：标记齐全、可搬进 node、没碰 DOM / localStorage / setTimeout。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_segment_markers_and_purity(self):
        s = self.js.find(SEG_BEGIN)
        e = self.js.find(SEG_END)
        self.assertGreaterEqual(s, 0, "undo:begin 缺失")
        self.assertGreater(e, s, "undo:end 缺失或顺序错误")
        body = re.sub(r"/\*[\s\S]*?\*/", " ", re.sub(r"//[^\n]*", " ", self.js[s:e]))
        for banned in BANNED:
            self.assertNotIn(banned, body, "纯函数段出现禁用引用 " + banned)

    def test_segment_takes_now_as_argument(self):
        seg = self.js[self.js.find(SEG_BEGIN):self.js.find(SEG_END)]
        for fn in ("undoLive", "undoPush", "undoTake"):
            m = re.search(r"function %s\(([^)]*)\)" % fn, seg)
            self.assertIsNotNone(m, fn + " 没定义")
            self.assertIn("now", m.group(1), fn + " 必须把时钟当参数收进来，否则搬不进 node")

    def test_harness_cases_pass(self):
        proc = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ALL_UNDO_CASES_PASSED", proc.stdout)
        self.assertGreaterEqual(proc.stdout.count("ok   - "), 8, "用例数不应悄悄变少")


class GlueTest(unittest.TestCase):
    """DOM 胶水在段外：只管执行 / 计时 / 回调，规则不重复实现。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.glue = _glue(cls.js)

    def test_glue_sits_between_segment_and_tabs(self):
        self.assertLess(self.js.find(SEG_END), self.js.find("let undoStack"),
                        "胶水必须跟在 undo 段之后")
        self.assertLess(self.js.find("let undoStack"), self.js.find("/* tabs */"),
                        "胶水必须在 tabs 绑定之前：let 声明的 undoStack 不能被更早的同步代码读到 TDZ")

    def test_undoable_runs_then_pushes_then_notifies(self):
        order = [self.glue.find("doFn();"),
                 self.glue.find("undoStack = undoPush("),
                 self.glue.find("notifShow(entry.label")]
        self.assertTrue(all(i >= 0 for i in order), "doFn -> push -> 通知 三步都得在")
        self.assertEqual(order, sorted(order), "必须「先执行、再压栈、最后弹通知」")

    def test_notify_level_comes_from_label(self):
        self.assertIn("notifShow(entry.label, notifLevel(entry.label)", self.glue,
                      "通知级别该由 label 推，别让调用点逐个传色")

    def test_undo_run_takes_then_swaps_stack(self):
        take = self.glue.find("undoTake(undoStack, id, Date.now())")
        swap = self.glue.find("undoStack = hit.rest;")
        self.assertGreaterEqual(take, 0, "undoRun 必须先 take")
        self.assertGreater(swap, take, "命中后才允许替换栈，否则过期条目会把栈清空")
        self.assertIn("toast(\"反悔时间已过", self.glue, "未命中要有明确反馈")

    def test_undo_callback_is_guarded(self):
        run = self.glue[self.glue.find("function undoRun("):]
        self.assertIn("try {", run, "撤销回调必须包 try")
        self.assertIn("catch (e)", run, "回调抛异常不能变成未捕获错误")
        self.assertIn('toast("撤销失败', run, "失败要有反馈")

    def test_single_definition(self):
        for name in ("function undoable(", "function undoRun(", "let undoStack = [];"):
            self.assertEqual(self.js.count(name), 1, name + " 只能定义一次")


class EntryPointsTest(unittest.TestCase):
    """四个破坏性入口都改走 undoable()，且快照抓在调用之前。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_all_four_entries_use_undoable(self):
        for start, end, name in ENTRIES:
            blk = _block(self.js, start, end)
            self.assertTrue(blk, "找不到入口 " + name)
            self.assertEqual(blk.count("undoable("), 1, name + " 必须恰好调一次 undoable")
            self.assertGreaterEqual(_arrow_count(blk), 2,
                                    name + " 要同时给「执行」和「恢复」两个回调")

    def test_snapshot_captured_before_undoable(self):
        for start, end, name in ENTRIES:
            blk = _block(self.js, start, end)
            call = blk.find("undoable(")
            self.assertGreaterEqual(call, 0, name + " 没调 undoable")
            snapshot = max(blk.find("prevPhrases"), blk.find("prevRaw"), blk.find("const prev ="))
            self.assertGreater(snapshot, -1, name + " 必须先抓快照")
            self.assertLess(snapshot, call,
                            name + " 的快照必须在 undoable() 之前抓，否则恢复的是破坏后的状态")

    def test_km_reset_keeps_log_and_close_modal(self):
        blk = _block(self.js, "function kmResetAll() {", "// 改过的键带")
        self.assertIn("closeModal(", blk, "恢复默认按键后照旧关弹窗")
        self.assertIn("log(", blk, "执行日志照旧写")
        self.assertIn('toast("没有改过的键")', blk, "空映射时不该弹撤销按钮")

    def test_km_reset_restores_whole_map(self):
        blk = _block(self.js, "function kmResetAll() {", "// 改过的键带")
        self.assertIn("kmMap = prev;", blk, "撤销要把整份按键表换回去，不是逐键删")


class NotifGlueTest(unittest.TestCase):
    """通知层的 act 扩展是向后的：不给 act 时行为与旧版完全一致。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.show = _block(cls.js, "// 只上屏、不入账", "function notifDismiss(")
        cls.mount = _block(cls.js, "function notifMount(n) {", "function notifUnmount(")

    def test_notif_show_takes_act_third_param(self):
        self.assertIn("function notifShow(msg, level, act)", self.show,
                      "act 必须是第三个可选参数")
        self.assertIn("act: act && act.text && typeof act.onAction", self.show,
                      "act 要校验过才收，坏对象不能进队列")

    def test_dwell_time_covers_undo_window(self):
        self.assertIn("n.act ? Math.max(notifDur(lv), UNDO_MS) : notifDur(lv)", self.show,
                      "带 act 的通知必须至少停留 UNDO_MS，否则按钮比反悔窗口先消失")
        self.assertIn("function notifDur(", self.js, "旧时长函数不能被绕过")

    def test_action_button_uses_dom_api(self):
        self.assertIn('a.className = "nact"', self.mount, "动作按钮要有自己的类")
        self.assertIn("a.textContent = n.act.text", self.mount,
                      "文案走 textContent，禁 innerHTML")
        self.assertNotIn("innerHTML", self.mount, "notifMount 不许出现 innerHTML")
        self.assertIn("e.stopPropagation()", self.mount, "点动作别穿透到 toast 本体")
        self.assertIn("notifDismiss(n, true)", self.mount, "点完先收通知再跑回调")

    def test_action_button_appended_between_text_and_close(self):
        self.assertIn("const kids = [txt];", self.mount)
        self.assertIn("kids.push(a);", self.mount)
        self.assertIn("kids.push(x);", self.mount)
        self.assertLess(self.mount.find("kids.push(a);"), self.mount.find("kids.push(x);"),
                        "动作按钮要排在关闭键之前")


class CssTest(unittest.TestCase):
    """触控目标与配色都走设计令牌，不许写死数值。"""

    @classmethod
    def setUpClass(cls):
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")
        cls.rule = _block(cls.css, ".ntoast .nact {", ".ntoast .nact:hover")

    def test_rule_exists(self):
        self.assertTrue(self.rule.strip(), ".ntoast .nact 规则缺失")

    def test_touch_target_and_color_tokens(self):
        self.assertIn("min-height: var(--touch-min)", self.rule, "触控下限要用令牌")
        self.assertIn("color: var(--accent-text)", self.rule, "强调色要用令牌")
        self.assertIn("pointer-events: auto", self.rule,
                      ".ntoast 是 pointer-events:none，按钮必须自己打开")
        self.assertNotIn("innerHTML", self.rule)


class VersionTest(unittest.TestCase):
    """版本号只从根目录 VERSION 读。"""

    def test_version_bumped(self):
        text = (ROOT / "VERSION").read_text(encoding="utf-8")
        name = re.search(r"versionName=(\S+)", text).group(1)
        code = re.search(r"versionCode=(\d+)", text).group(1)
        self.assertEqual((name, code), ("1.34.0", "35"), "版本随轮推进，勿停在上一轮")


if __name__ == "__main__":
    unittest.main(verbosity=2)
