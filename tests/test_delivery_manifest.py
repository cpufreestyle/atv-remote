#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付清单一致性：server.py 的本地 import 闭包必须在三种交付形态里都齐全。

一份 server.py 有三个 runtime 落点（AGENTS.md「没有单一事实来源」那条风险）：
  1) 根目录直跑（Mac / 开发机）
  2) /bundle.tgz → Termux           —— 清单来自 server.bundle_paths()
  3) android-native 内嵌副本 → APK  —— 清单来自 build.gradle 的 syncPythonSrc（手工）
                                          与 ../../sync-native.sh（从 server.BUNDLE_PY 现拿）

外提纯函数以后每加一个 atv_*.py，漏掉任意一处清单的后果都是**那一种形态
ImportError 起不来**，而且只在手机上复现。所以这里逐条对账：
第 2、3 份清单必须跟着 server.py 的 import 走。

跑法：python3 -m unittest discover -s tests
"""

import subprocess
import sys
import unittest
from pathlib import Path

import server

ROOT = Path(server.__file__).resolve().parent
EMB = ROOT / "android-native" / "app" / "src" / "main" / "python"
BUILD_GRADLE = ROOT / "android-native" / "app" / "build.gradle"
SYNC_SH = ROOT / "sync-native.sh"


class LocalModuleClosureTest(unittest.TestCase):
    """BUNDLE_PY = server.py 沿 import 求出的同目录模块，不多不少。"""

    def test_entry_is_server(self):
        self.assertEqual(server.BUNDLE_PY[0], "server.py")

    def test_closure_covers_every_local_module(self):
        on_disk = {p.name for p in ROOT.glob("atv_*.py")} | {"server.py"}
        self.assertEqual(set(server.BUNDLE_PY), on_disk,
                         "根目录的 atv_*.py 与 server.py 的 import 闭包不一致："
                         "要么新增了没被 import 的模块（不该进包），"
                         "要么有模块没被 server.py 引到")

    def test_closure_is_free_of_third_party(self):
        for name in server.BUNDLE_PY:
            self.assertTrue((ROOT / name).is_file(), "%s 不在根目录里" % name)

    def test_bundle_files_adds_start_sh(self):
        self.assertIn("start.sh", server.BUNDLE_FILES)


class BundlePathsTest(unittest.TestCase):
    """/bundle.tgz 的内容：整份 static/ 都在，凭据只在启用令牌时进包。"""

    def relatives(self):
        return {p.relative_to(ROOT).as_posix() for p in server.bundle_paths()}

    def test_whole_static_dir_is_packaged(self):
        static = sorted((ROOT / "static").rglob("*"))
        expected = {"static/" + p.relative_to(ROOT / "static").as_posix()
                    for p in static if p.is_file()}
        self.assertTrue(expected, "static/ 里应该有文件")
        got = {r for r in self.relatives() if r.startswith("static/")}
        self.assertEqual(got, expected)

    def test_every_module_is_packaged(self):
        got = self.relatives()
        for name in server.BUNDLE_PY:
            self.assertIn(name, got)

    def test_state_json_only_when_token_enabled(self):
        saved = server.AUTH_TOKEN
        try:
            server.AUTH_TOKEN = ""
            self.assertNotIn("state.json", server.bundle_files())
            self.assertNotIn("state.json", self.relatives())
            server.AUTH_TOKEN = "t"
            self.assertIn("state.json", server.bundle_files())
        finally:
            server.AUTH_TOKEN = saved

    def test_signature_and_archive_agree(self):
        """签名与打包必须取同一个清单：不一致的后果是缓存永远认为「没变」而发旧包"""
        sig = {s[0] for s in server.bundle_signature()}
        self.assertEqual(sig, self.relatives())


class GradleEmbeddedCopyTest(unittest.TestCase):
    """build.gradle 的 syncPythonSrc 是手工列表，所以必须逐条对账。"""

    @classmethod
    def setUpClass(cls):
        cls.text = BUILD_GRADLE.read_text(encoding="utf-8")

    def test_every_local_module_is_copied(self):
        for name in server.BUNDLE_PY:
            self.assertIn('rootProject.file("../%s")' % name, self.text,
                          "build.gradle 的 syncPythonSrc 没有拷 %s：APK 里会 ImportError" % name)

    def test_static_is_copied_as_a_directory(self):
        """不显式 into "static" 的话 Copy 会把目录内容平铺，历史上有过 static/static/"""
        self.assertIn('from(rootProject.file("../static"))', self.text)
        self.assertIn('into "static"', self.text)


class SyncScriptTest(unittest.TestCase):
    """sync-native.sh 不手工维护文件名列表，而是现问 server.py。"""

    @classmethod
    def setUpClass(cls):
        cls.text = SYNC_SH.read_text(encoding="utf-8")

    def test_list_comes_from_server(self):
        self.assertIn("server.BUNDLE_PY", self.text)

    def test_no_handwritten_module_names(self):
        for name in server.BUNDLE_PY:
            if name == "server.py":
                continue
            self.assertNotIn(name, self.text,
                             "sync-native.sh 又手工列了 %s，新增模块会漏" % name)

    def test_check_mode_does_not_write(self):
        self.assertIn('"--check"', self.text)


class GeneratedCopiesIgnoredTest(unittest.TestCase):
    """内嵌副本必须整体被 gitignore：以「未跟踪文件」身份被顺手 commit 回去，
    就又是第 5 份事实来源（此前审查的 O1）。boot.py 是例外——它手写、在 git 里。"""

    @classmethod
    def setUpClass(cls):
        try:
            subprocess.run(["git", "--version"], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            raise unittest.SkipTest("没有可用的 git")

    def check_ignored(self, rel):
        p = subprocess.run(["git", "check-ignore", "-q", "--", rel],
                           cwd=str(ROOT), capture_output=True)
        return p.returncode == 0

    def test_generated_copies_are_ignored(self):
        for name in server.BUNDLE_PY:
            self.assertTrue(self.check_ignored(str(EMB / name)), "%s 的副本没被 gitignore" % name)
        self.assertTrue(self.check_ignored(str(EMB / "static" / "app.js")))

    def test_boot_py_stays_tracked(self):
        self.assertFalse(self.check_ignored(str(EMB / "boot.py")),
                         "boot.py 是手写的启动器，被通配规则吃掉了")


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in sys.argv else 1)
