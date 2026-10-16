#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apple TV 三档能力的命令面板 / 语音入口回归（I23）。

动机：I18/I21/I22 给 Apple TV 加了「文本追加 / 整段替换」「下一个按键双击」「原生长按」
三项能力，页面上有按钮，但键盘优先的命令面板（Cmd/Ctrl+K）与语音意图层
（INTENT_ALIASES）完全没收录——熟手敲几个字母到不了这些新功能，功能等于只做了一半。

本轮把 8 条命令推进 palCommands()（门禁 status.curType === "appletv"，与页面上三个按钮
的 hidden 同一个条件）、15 条别名推进 INTENT_ALIASES。这里只验四件事，全部离线可跑：
  1) 真行为：palCommands() 在 node 沙箱里原样执行——门禁、幂等、单一状态源、label 反态
     （tests/atv_pal_harness.js，8 组用例 + 结构化 facts 供本文件复核）；
  2) 门禁条件与页面按钮一致：Android 用户面板一条 Apple TV 命令都不多；
  3) 执行体不另发一套请求，只复用页面按钮 / holdSend 这个单一状态源；
  4) 别名表里每个新短语都能对上真实 push 出来的 id（语音听懂个不存在的命令 = 点了没反应）。
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "atv_pal_harness.js"

ATV_IDS = ["atv:append:on", "atv:append:off", "atv:dbl",
  "atv:hold:23", "atv:hold:19", "atv:hold:20", "atv:hold:21", "atv:hold:22"]
MIN_CASES = 8


class PaletteActionHarnessTest(unittest.TestCase):
    """palCommands() 真跑：门禁 / 幂等 / 单一状态源 / label 反态。"""

    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("没有 node")
        cls.r = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True,
                               cwd=str(ROOT))
        cls.facts = json.loads(cls.r.stdout.strip().splitlines()[-2])["facts"]

    def test_harness_green_and_not_shorter(self):
        self.assertEqual(self.r.returncode, 0, self.r.stdout + self.r.stderr)
        self.assertIn("ALL_ATV_PALETTE_CASES_PASSED", self.r.stdout)
        total = json.loads(self.r.stdout.strip().splitlines()[-2])["total"]
        self.assertGreaterEqual(total, MIN_CASES)

    def test_gate_apple_tv_only(self):
        self.assertEqual(sorted(self.facts["atvIds"]), sorted(ATV_IDS),
                         "Apple TV 组必须正好是这 8 条")
        self.assertEqual(self.facts["androidAtvIds"], [],
                         "Android 用户面板里不该出现任何 atv: 命令")

    def test_append_is_idempotent_not_toggle(self):
        """说「替换」时若当前已是替换态，翻转会切回追加——与用户说的正好相反。"""
        self.assertEqual(self.facts["appendNoopClicks"], 0, "已是追加还点了切换按钮")
        self.assertEqual(self.facts["replaceNoopClicks"], 0, "已是替换还点了切换按钮")
        self.assertEqual(self.facts["appendSwitchClicks"], ["#atvAppendBtn"],
                         "需要切换时必须点页面那个按钮，而不是自己发请求")
        self.assertEqual(self.facts["replaceSwitchClicks"], ["#atvAppendBtn"])

    def test_double_tap_reuses_page_button(self):
        self.assertEqual(self.facts["dblClicks"], ["#atvDoubleBtn"],
                         "双击必须走页面按钮（单一状态源）")

    def test_hold_keys_map_to_hold_send(self):
        for cid in ATV_IDS:
            if not cid.startswith("atv:hold:"):
                continue
            code = int(cid.split(":")[-1])
            self.assertEqual(self.facts["holdCalls"][cid], [code],
                             cid + " 必须调 holdSend(" + str(code) + ")")

    def test_labels_reflect_current_state(self):
        labels = self.facts["labels"]
        self.assertIn("当前：整段替换", labels["appendOn"])
        self.assertIn("当前：整段替换", labels["appendOff"])
        self.assertNotIn("待命中", labels["dbl"], "非待命态不该标注待命中")

    def test_no_duplicate_command_ids(self):
        self.assertEqual(self.facts["dupes"], [], "面板里出现了重复 id")


class GateContractTest(unittest.TestCase):
    """静态：门禁条件、分组名、与页面按钮同一个判据。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.pal = cls.js[cls.js.index("function palCommands()"):]
        cls.pal = cls.pal[:cls.pal.index("\n}\n") + 2]
        cls.block = cls.pal[cls.pal.index('if (status.curType === "appletv")'):]
        cls.block = cls.block[:cls.block.index("\n  }\n")]

    def test_gate_uses_same_condition_as_page_buttons(self):
        """页面按钮用 classList.toggle("hidden", status.curType !== "appletv")，面板必须同判据。"""
        self.assertIn('if (status.curType === "appletv")', self.block)
        self.assertEqual(self.js.count('btn.classList.toggle("hidden", status.curType !== "appletv")'), 2,
                         msg="页面按钮的 hidden 判据被改掉了，面板门禁要跟着复核")

    def test_all_eight_ids_are_pushed(self):
        for cid in ATV_IDS:
            if cid.startswith("atv:hold:"):
                continue  # 动态 id（"atv:hold:" + code），由命名常量收割测试覆盖
            self.assertIn('"' + cid + '"', self.block, "palCommands 里没有 push " + cid)

    def test_group_name_is_apple_tv(self):
        self.assertEqual(self.block.count('"Apple TV"'), self.block.count("push("),
                         "门禁块内每条 push 都必须挂 Apple TV 组（关掉门禁时整组消失）")

    def test_hold_ids_come_from_named_constant(self):
        """动态 id 要能从命名常量收割，否则别名表一对不上就误判成幽灵命令。"""
        self.assertIn("const ATV_HOLD_KEYS = ", self.block)
        hold = self.block[self.block.index("const ATV_HOLD_KEYS = "):]
        hold = hold[:hold.index("];")]
        self.assertEqual(sorted(re.findall(r"\[(\d+),", hold)),
                         sorted([c.split(":")[-1] for c in ATV_IDS if c.startswith("atv:hold:")]))

    def test_no_innerhtml_in_apple_tv_block(self):
        self.assertNotIn("innerHTML", self.block, "AGENTS.md 禁令：禁 innerHTML 渲染")


class SingleSourceTest(unittest.TestCase):
    """执行体只复用页面出口，不另发一套请求；引用的 id 必须真的存在。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")
        pal = cls.js[cls.js.index("function palCommands()"):]
        cls.block = pal[pal.index('if (status.curType === "appletv")'):]
        cls.block = cls.block[:cls.block.index("\n  }\n")]

    def test_run_bodies_reuse_page_controls(self):
        self.assertIn('$("#atvAppendBtn").click()', self.block, "文本切换必须点页面按钮")
        self.assertIn('$("#atvDoubleBtn").click()', self.block, "双击必须点页面按钮")
        self.assertIn("holdSend(code)", self.block, "长按必须复用 holdSend")

    def test_run_bodies_do_not_talk_to_backend(self):
        """面板命令自己发请求就会和页面按钮各写一份状态，两边必然漂移。"""
        for banned in ("api(", "fetch(", "adb.", "atvBackend"):
            self.assertNotIn(banned, self.block, "Apple TV 面板命令不该直接碰 " + banned)

    def test_referenced_ids_exist_in_markup(self):
        ids = set(re.findall(r'id="([^"]+)"', self.html))
        for sel in re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', self.block):
            self.assertIn(sel, ids, "面板引用了 index.html 里不存在的 #" + sel)


class AliasReachabilityTest(unittest.TestCase):
    """语音层：每个新短语都要能落到真实存在的命令 id。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        seg = cls.js[cls.js.index("/* ===== intent:begin"):cls.js.index("/* ===== intent:end")]
        cls.pairs = re.findall(r'\["([^"]+)", "([^"]+)"\]', seg)
        cls.by_cmd = {}
        for phrase, cid in cls.pairs:
            cls.by_cmd.setdefault(cid, []).append(phrase)

    def test_every_new_command_has_aliases(self):
        for cid in ATV_IDS:
            self.assertIn(cid, self.by_cmd, "没有语音别名能到 " + cid)
            self.assertGreaterEqual(len(self.by_cmd[cid]), 2,
                                     cid + " 至少要两个说法，否则语音层形同虚设")

    def test_aliases_normalize_into_the_command(self):
        """intentNorm 会 lowercase + 去空白标点，「长按OK」必须能命中「长按ok」。"""
        norm = lambda s: re.sub(r"[\s\W_]+", "", s).lower()
        normed = {norm(p) for p, c in self.pairs}
        self.assertIn(norm("长按OK"), normed, "大写 OK 的说法没进表")

    def test_no_alias_phrase_collides_with_other_commands(self):
        """同一个说法指向两条命令 = 语音随机二选一，必须避免。"""
        seen = {}
        for phrase, cid in self.pairs:
            if phrase in seen and seen[phrase] != cid:
                self.fail("别名「" + phrase + "」同时指向 " + seen[phrase] + " 与 " + cid)
            seen[phrase] = cid


if __name__ == "__main__":
    unittest.main(verbosity=2)

