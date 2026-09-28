#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手机安装引导的地址来源回归。

动机：安装命令 / 二维码 / 令牌提示原先用 location.origin 拼地址。在 Mac 上打开页面时
origin 是 127.0.0.1，复制给手机的就是回环地址，一定连不上（用户实际遇到了
ERR_ADDRESS_UNREACHABLE）。二维码下加明文地址也一样要先有正确的局域网 IP。

验三件事（标准库 + node harness，离线可跑）：
  1) 命令、二维码、明文「本机地址」、令牌提示一律用服务端 /api/setup 给的局域网 IP；
  2) 二维码仍指向同一条命令，接入二维码仍带令牌，明文地址后能跟 ?token=；
  3) /api/setup 失败时退回 location.origin，不渲染空地址。
"""

import subprocess
import unittest
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "install_harness.js"
BEGIN = "/* ---------------- 手机安装引导 ---------------- */"
END = "/* ---------------- 启动 ---------------- */"


class InstallBaseTest(unittest.TestCase):
    """安装引导段：局域网 IP 而不是 location.origin。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_harness_cases(self):
        proc = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("install harness: all passed", proc.stdout)
        self.assertGreaterEqual(proc.stdout.count("ok   "), 9, "用例数不应悄悄变少")

    def test_no_origin_in_install_section(self):
        s = self.js.find(BEGIN)
        e = self.js.find(END, s)
        self.assertTrue(0 <= s < e, "安装段标记缺失或顺序错误")
        seg = self.js[s:e]
        # location.origin 只允许作为 api() 取不到时的兜底初值
        code = re.sub(r"/\*[\s\S]*?\*/", " ", seg)
        code = re.sub(r"//[^\n]*", " ", code)
        self.assertEqual(code.count("location.origin"), 1,
                         "安装段只应有一处 location.origin（兜底初值）")
        self.assertIn("installBase = location.origin", code)
        self.assertNotIn("curl -sL ${location.origin}", seg,
                         "命令不能再直接用 location.origin 拼")

    def test_hint_markup(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="lanUrl"', html, "二维码下要有明文地址兜底")
        self.assertIn('id="lanHint"', html)
        # 明文地址用 textContent 渲染，不能 innerHTML（IP 来自局域网广播）
        self.assertIn("lanUrl.textContent", self.js)
        self.assertNotIn("lanUrl.innerHTML", self.js)


if __name__ == "__main__":
    unittest.main()
