#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端自动重连倒计时的行为回归。

跑 tests/reconnect_ui_harness.js（node，把 static/app.js 里的 renderAutoReconn / arPaint
抽出来配假 DOM + 假 setInterval 执行），锁这几件事：

- 排队中把「还要等几秒」显示出来，并且本地按秒走（不靠 8s 轮询刷新）；
- 倒数归零后清掉计时器，回到不带秒数的「自动重连中」；
- 停机只提示手动，不留计时器；
- 空闲 / Apple TV 时后缀为空，且 arBase 会复位（不残留上一台 Android 的后缀）。

为什么值得一个真 harness：这三段逻辑全在闭包状态 + 定时器里，光 import / node --check
查不出来，源码字符串断言也挡不住「渲染到了不存在的元素上」这类回归
（本轮就真的先写错过一次选择器，是 tests/test_command_palette.py 的 SelectorDriftTest 抓住的）。
"""

import re
import subprocess
import unittest
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "reconnect_ui_harness.js"
STATIC = Path(__file__).resolve().parent.parent / "static"
MIN_HARNESS_CASES = 12


class ReconnectUiHarnessTest(unittest.TestCase):
    def setUp(self):
        self.r = subprocess.run(["node", str(HARNESS)],
                                capture_output=True, text=True)

    def test_harness_is_green(self):
        self.assertEqual(self.r.returncode, 0,
                         self.r.stdout + self.r.stderr)

    def test_harness_case_count_never_shrinks(self):
        m = re.search(r"OK (\d+) cases", self.r.stdout)
        self.assertIsNotNone(m, "harness 没打印用例数：" + self.r.stdout)
        self.assertGreaterEqual(int(m.group(1)), MIN_HARNESS_CASES,
                                "reconnect_ui harness 用例数减少了")

    def test_harness_actually_extracted_the_real_functions(self):
        """harness 靠函数名切片：改名 / 被删时要在这里炸，而不是安静地少测一块"""
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        for fn in ("function arPaint(", "function renderAutoReconn("):
            self.assertIn(fn, js, "app.js 里没有 " + fn)
        # 渲染目标必须由调用方传进来：段内再 $() 找一次容易选错元素
        seg = js[js.index("function arPaint("):]
        seg = seg[:seg.index("function fmtLeft(")]
        self.assertNotIn("$(", seg, "arPaint/arPaint 段内不要再做选择器查询")


if __name__ == "__main__":
    unittest.main()
