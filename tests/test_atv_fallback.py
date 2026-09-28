#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pyatv 缺失时的降级体验回归。

动机：用户装了手机端后遥控 Apple TV 报「pyatv 未安装」，但那条提示只给了 Mac 的
`.venv/bin/pip install pyatv`——Termux 上根本没有 .venv，照着跑只会再失败一次。
另外安装脚本里 `pip install pyatv || echo 失败` 会把失败原因咽掉，用户只看到一句
「失败」而不知道补什么。

验四件事（标准库 + 子进程假 import，离线可跑）：
  1) pyatv 导入失败时所有占位方法的报错都带一条能直接粘贴的命令；
  2) 命令按平台分流：Termux 给 pip 一行，Mac 给 .venv 一行；
  3) 安装脚本把 pyatv 的失败原因打出来，并附可反复执行的补救命令；
  4) 页面上有 pyatv 补救命令的复制入口。
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server.py"
BACKEND = ROOT / "atv_backend.py"
STATIC = ROOT / "static"

PROBE = """
import sys
sys.path.insert(0, {blockdir!r})
sys.path.insert(0, {root!r})
import atv_backend as b
print("AVAILABLE", b.PYATV_AVAILABLE)
print("MAC_HINT", b._pyatv_hint())
b._IS_TERMUX = True
print("TERMUX_HINT", b._pyatv_hint())
mgr = b.AppleTvManager()
for name in ("scan", "pair_begin", "pair_finish", "connect", "send_keys",
            "send_text", "tap", "swipe", "apps", "launch_app", "artwork"):
    fn = getattr(mgr, name)
    try:
        try:
            fn()
        except TypeError:
            fn("probe")
    except Exception as exc:
        print("ERR", name, exc)
"""


class AtvFallbackTest(unittest.TestCase):
    """降级占位：报错要能指导人把 pyatv 装上。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        blocker = Path(cls.tmp.name) / "pyatv.py"
        blocker.write_text('raise ImportError("blocked for test")\n',
                           encoding="utf-8")
        code = PROBE.format(blockdir=cls.tmp.name, root=str(ROOT))
        cls.proc = subprocess.run([sys.executable, "-c", code],
                                  capture_output=True, text=True, timeout=60)
        cls.out = cls.proc.stdout

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_probe_ran(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertIn("AVAILABLE False", self.out,
                      "假 pyatv 必须让导入失败，否则这个用例什么都没测")

    def test_every_placeholder_error_carries_command(self):
        errs = [ln for ln in self.out.splitlines() if ln.startswith("ERR ")]
        self.assertGreaterEqual(len(errs), 11, "占位方法数量不该悄悄变少")
        for line in errs:
            self.assertIn("pyatv", line)
            self.assertRegex(line, r"(pip install|\.venv/bin/pip install)",
                             "报错里要有一条能直接粘贴的安装命令：" + line)

    def test_hint_is_platform_aware(self):
        mac = next(ln for ln in self.out.splitlines()
                   if ln.startswith("MAC_HINT "))
        termux = next(ln for ln in self.out.splitlines()
                      if ln.startswith("TERMUX_HINT "))
        self.assertIn(".venv/bin/pip install pyatv", mac)
        self.assertNotIn(".venv", termux)
        self.assertIn("pip install pyatv==0.18.0", termux)

    def test_install_script_surfaces_pyatv_failure(self):
        src = SERVER.read_text(encoding="utf-8")
        self.assertIn("PYATV_LOG", src, "pip 失败要落日志，不能只 echo 一句")
        self.assertIn("pyatv-install.log", src)
        self.assertIn("补救（可反复跑）：pkg install rust clang libffi openssl "
                      "python-cryptography && pip install pyatv==0.18.0", src)
        self.assertIn('python -c "import pyatv"', src,
                      "装完要自查 pyatv 是否真的能 import")
        # pydantic-core 在 Android 上没有预编译 wheel，必须先装 rust，否则 pip 只会
        # 报「Failed to build pydantic-core」而看不出缺工具链
        self.assertIn("pkg install -y rust python-cryptography ", src)
        self.assertIn("pydantic-core", src, "要说清为什么要 rust")
        # 仍然要钉住版本：测试 test_install_rejects_unsanitized_host 也断言它
        self.assertIn("pyatv==0.18.0", src)

    def test_page_has_pyatv_fix_entry(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="pyatvFixCmd"', html)
        self.assertIn("pkg install rust clang libffi openssl python-cryptography "
                      "&& pip install pyatv==0.18.0", html,
                      "页面上的补救命令要自带编译工具链，单独跑也能成")
        self.assertIn("pip install pyatv==0.18.0", html)
        self.assertIn("Failed to build pydantic-core", html,
                      "要把用户实际看到的报错写在提示里")
        self.assertIn("copyPyatvFixBtn", js)


if __name__ == "__main__":
    unittest.main()
