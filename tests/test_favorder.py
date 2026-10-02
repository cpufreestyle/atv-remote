#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收藏夹可调序（第三十二轮）回归。

动机：收藏夹的 UI 一直写着「顺序即优先级」，但顺序只能由固定时间决定
（新固定的排最前），用户没有任何办法调整——攒到五六个常用之后，
最常用的那个可能压在第四位。

学习源与实现口径见 static/app.js favorder 段首注释：SortableJS 1.15.6（MIT）
的拖拽结束事件携带 oldIndex / newIndex，消费者的责任是把数据重排得和 DOM 一致。
本项目取其契约，不取其纯拖拽交互（它没有键盘通道，而这个界面同时跑在
Mac App 上），改成一队显式的上移/下移按钮。

这里只验四件事，全部离线可跑：
  1) 规则段（favorder:begin/end）可搬进 node：移动/越界/可逆有真行为证据；
  2) 移动不改入参——调用方要拿旧值走 undoable 撤销；
  3) 边界按钮要置灰（首行不上移、末行不下移）且不消失；
  4) 撤销链路真的接上了（favOrderBtn 里调 undoable，而不是直接 favSave）。
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "favorder_harness.js"

FAV_BEGIN = "/* ===== favorder:begin"
FAV_END = "/* ===== favorder:end"
MIN_CASES = 8


def _strip(text):
    return re.sub(r"/\*[\s\S]*?\*/", " ", re.sub(r"//[^\n]*", " ", text))


class SegmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_markers_unique(self):
        self.assertEqual(self.js.count(FAV_BEGIN), 1, "begin 必须唯一")
        self.assertEqual(self.js.count(FAV_END), 1, "end 必须唯一")
        self.assertLess(self.js.index(FAV_BEGIN), self.js.index(FAV_END))

    def test_pure_segment_has_no_global_state(self):
        seg = _strip(self.js[self.js.index(FAV_BEGIN):self.js.index(FAV_END)])
        for banned in ["document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("]:
            self.assertNotIn(banned, seg, "禁用引用 " + banned)

    def test_harness_green_and_not_shorter(self):
        if shutil.which("node") is None:
            self.skipTest("没有 node")
        r = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALL_FAVORDER_CASES_PASSED", r.stdout)
        total = json.loads(r.stdout.strip().splitlines()[-2])["total"]
        self.assertGreaterEqual(total, MIN_CASES)


class GlueTest(unittest.TestCase):
    """DOM 胶水：边界置灰、撤销接上、偏好仍只进 localStorage。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_boundary_buttons_disabled_not_removed(self):
        seg = self.js[self.js.index("function favRender"):]
        seg = seg[:seg.index("function favStarBtn")]
        self.assertIn("up.disabled = !favCanMove(list, idx, \"up\");", seg)
        self.assertIn("dn.disabled = !favCanMove(list, idx, \"down\");", seg)
        self.assertIn("list.forEach((a, idx) => {", seg, "渲染必须带下标才能判边界")

    def test_undoable_chain_wired(self):
        seg = self.js[self.js.index("function favOrderBtn"):]
        seg = seg[:seg.index("\n}")]
        self.assertIn("undoable(", seg, "调序必须可撤销")
        self.assertIn("favMove(pins, from, from + (dir === \"up\" ? -1 : 1))", seg)
        self.assertIn("const prev = pins.slice();", seg, "撤销要拿到旧值")
        self.assertIn("favIndexOf(pins, a.pkg)", seg)

    def test_buttons_are_accessible(self):
        seg = self.js[self.js.index("function favOrderBtn"):]
        seg = seg[:seg.index("\n}")]
        self.assertIn("b.type = \"button\";", seg)
        self.assertIn("b.setAttribute(\"aria-label\", b.title);", seg)
        self.assertIn("b.textContent = dir === \"up\"", seg)

    def test_pref_stays_in_localstorage(self):
        glue = self.js[self.js.index("function favOrderBtn"):]
        glue = glue[:glue.index("\n}")]
        for banned in ["state.json", "save_state(", "ATV_STATE"]:
            self.assertNotIn(banned, glue, "收藏夹偏好不得进敏感文件")


class StyleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def test_favmove_rule_exists_and_uses_tokens(self):
        m = re.search(r"\.favrow \.favmove \{[^}]*\}", self.css)
        self.assertIsNotNone(m)
        self.assertNotRegex(m.group(0), r"#[0-9a-fA-F]{3,8}\b")


if __name__ == "__main__":
    unittest.main(verbosity=2)
