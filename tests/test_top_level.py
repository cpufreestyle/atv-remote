#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app.js 顶层整文件可执行性回归（严格模式中断类缺陷的兜底）。

动机：2026-10-18 小米 K90 真机「无法点到 Apple TV」。根因不是页签逻辑，
是 static/app.js:2116 VOLSET_MAX_FALLBACK = 15 漏了 const——文件第 2 行就是
use strict 指令，顶层裸赋值抛 ReferenceError，整个脚本当场中断，3304 行的
页签 click 绑定及其后所有初始化从未执行（原生滚动不走 JS，所以页面照样能
滚，把所有点击都吞掉）。该行由 dd3b99e（10-02）引入，存活 16 天无人发现：
现有 harness 全部按 atv-xxx:begin/end 标记抽段执行，整文件顶层路径没有
任何行为证据；node --check 只查语法不执行；SW 陈旧缓存又持续发放旧版
app.js 掩盖症状，直到缓存失效才在真机上现形。

验三件事（标准库 + node harness，离线可跑）：
  1) 行为：vm 沙箱整文件执行 app.js 顶层不抛异常，且两个 .devtab 都注册了
     click 监听（tests/top_level_harness.js 经 subprocess 驱动）——注入裸
     赋值 bug 时本测试必须变红（已手工验证：报错与真机 console 一致）；
  2) 前提：app.js 第 2 行保持 use strict 指令——删了它裸赋值就静默通过，
     冒烟测试随之失效，所以这条本身也要钉住；
  3) 形态：源码里不存在顶层裸赋值（行首标识符直接 = 且无声明关键字），
     编译期就挡住下一个 VOLSET_MAX_FALLBACK。
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "static" / "app.js"
HARNESS = Path(__file__).resolve().parent / "top_level_harness.js"


class TopLevelHarnessTest(unittest.TestCase):
    """整文件顶层执行 + 页签 click 绑定的真行为证据。"""

    def test_harness_cases_pass(self):
        r = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALL_TOP_LEVEL_CASES_PASSED", r.stdout)


class TopLevelStructureTest(unittest.TestCase):
    """严格模式前提与顶层裸赋值的静态守护。"""

    @classmethod
    def setUpClass(cls):
        cls.lines = APP.read_text(encoding="utf-8").splitlines()

    def test_strict_mode_kept(self):
        """第 2 行是指令，冒烟测试才有效（裸赋值才会炸）。"""
        head = [l.strip() for l in self.lines[:5]]
        self.assertIn('"use strict";', head)

    def test_no_bare_top_level_assignment(self):
        """行首标识符直接 = 且无 const/let/var 的一律挡住（VOLSET 复发即红）。"""
        bad = []
        for i, line in enumerate(self.lines, 1):
            s = line.strip()
            if not s or s.startswith(("//", "/*", "*")):
                continue
            if re.match(r"^[A-Za-z_$][w$]*s*=(?!=)", s) and \
               not re.match(r"^(const|let|var)", s):
                bad.append("%d: %s" % (i, s[:60]))
        self.assertEqual(bad, [], "顶层裸赋值: " + "; ".join(bad))


if __name__ == "__main__":
    unittest.main()
