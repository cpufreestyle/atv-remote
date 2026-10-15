# -*- coding: utf-8 -*-
"""Apple TV \u8f93\u5165\u8bed\u4e49\uff08\u8ffd\u52a0 / \u6574\u6bb5\u66ff\u6362\uff09\u56de\u5f52\u3002

\u52a8\u673a\u6765\u81ea\u53cd\u7f16\u8bd1 macOS /usr/libexec/tvremoted \u5f97\u5230\u7684\u534f\u8bae\u5bf9\u6bd4\uff1a
Android \u4fa7 input text \u662f\u8ffd\u52a0\u8bed\u4e49\uff0c\u800c Apple TV \u4fa7 pyatv \u7684 text_set \u662f\u6574\u6bb5\u66ff\u6362\u2014\u2014
\u540c\u4e00\u4e2a\u201c\u53d1\u9001\u201d\u52a8\u4f5c\u5728\u4e24\u7aef\u884c\u4e3a\u4e0d\u4e00\u81f4\uff0c\u7528\u6237\u5728 Apple TV \u4e0a\u53d1\u4e00\u53e5\u5c31\u4f1a
\u628a\u641e\u5e72\u7535\u89c6\u8f93\u5165\u6846\u91cc\u5df2\u6709\u7684\u65e7\u5173\u952e\u8bcd\u3002

Companion \u534f\u8bae\uff08tvOS 16+\uff09\u6709 text_append\uff0c\u6240\u4ee5\u9ed8\u8ba4\u8d70\u8ffd\u52a0\u628a\u4e24\u7aef\u5bf9\u9f50\uff1b
MRP\uff08\u65e7\u8f6c\u53d1\u5668\uff09\u6ca1\u6709 Keyboard \u5b9e\u73b0\uff0c\u6ca1\u6709 text_append\uff0c\u5b89\u88c5\u65f6\u4f1a\u629b
NotSupportedError\u2014\u2014\u90a3\u5c31\u56de\u5230 text_set\uff0c\u81f3\u5c11\u8fd8\u80fd\u7528\u3002

\u8fd9\u91cc\u53ea\u9a8c\u8bc1\u4e09\u4ef6\u4e8b\uff08\u4e0d\u8d77\u771f pyatv / \u4e0d\u8fde\u771f\u7535\u89c6\uff09\uff1a
  1) \u8def\u7531\u5c42 mode \u5b57\u6bb5\u7684\u89e3\u8bfb\uff08append \u9ed8\u8ba4 / set \u663e\u5f0f\uff09\uff1b
  2) atv_backend.send_text \u7684\u964d\u7ea7\u94a9\u5b50\uff1aappend \u4e0d\u652f\u6301\u65f6\u56de\u5230 text_set\uff0c\u800c\u4e0d\u662f\u6574\u6761\u53d1\u9001\u5931\u8d25\uff1b
  3) \u524d\u7aef\u9ed8\u8ba4\u503c\u4e0e\u6309\u94ae\u5b58\u5728\u3002
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


class RouteModeTest(unittest.TestCase):
    """\u5199\u7aef\u70b9\u7684 mode \u5b57\u6bb5\uff1a\u9ed8\u8ba4\u8ffd\u52a0\uff0c\u663e\u5f0f set \u624d\u66ff\u6362\u3002"""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "server.py").read_text(encoding="utf-8")

    def test_mode_defaults_to_append(self):
        # \u9ed8\u8ba4\u5fc5\u987b\u662f append\uff1a\u65b0\u5ba2\u6237\u7aef / \u8001\u811a\u672c\u4e0d\u5e26 mode \u4e5f\u4e0d\u8be5\u628a\u7535\u89c6\u7aef\u641e\u5e72\u51fa\u53bb
        self.assertIn('body.get("mode", "append")', self.src,
                      "\u9ed8\u8ba4\u5e94\u4e3a append\uff08\u4e0e Android input text \u4e00\u81f4\uff09")

    def test_set_is_explicit_opt_out(self):
        self.assertIn('!= "set"', self.src, "\u53ea\u6709\u663e\u5f0f set \u624d\u8d70\u66ff\u6362\u5206\u652f")


class BackendFallbackTest(unittest.TestCase):
    """append \u4e0d\u53ef\u7528\u65f6\u5fc5\u987b\u964d\u7ea7\u800c\u4e0d\u662f\u5931\u8d25\u3002"""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "atv_backend.py").read_text(encoding="utf-8")

    def test_send_text_accepts_append_flag(self):
        self.assertIn("def send_text(self, text, enter=False, append=False):", self.src)

    def test_append_falls_back_to_set(self):
        """\u8ffd\u52a0\u4e0d\u652f\u6301\uff08MRP / \u65e7 pyatv\uff09\u65f6\u56de\u5230 text_set\u3002"""
        seg = self.src[self.src.index("def send_text"):]
        seg = seg[:seg.index("def tap")]
        self.assertIn("text_append", seg, "\u8ffd\u52a0\u5206\u652f\u8981\u7528 text_append")
        self.assertIn("text_set", seg, "\u964d\u7ea7\u56de\u5230 text_set")
        # \u964d\u7ea7\u5fc5\u987b\u53ea\u5bf9 append \u751f\u6548\uff1aset \u6a21\u5f0f\u4e0b\u7684\u5f02\u5e38\u8981\u539f\u6837\u629b
        self.assertIn("if not append:", seg, "set \u6a21\u5f0f\u4e0d\u5f97\u88ab\u541e\u5f02\u5e38")

    def test_stub_signature_aligned(self):
        """\u964d\u7ea7\u5360\u4f4d\u7c7b\u7684\u7b7e\u540d\u8981\u4e0e\u771f\u7c7b\u4e00\u81f4\uff0c\u5426\u5219\u8c03\u7528\u65b9\u4f20 append \u4f1a TypeError\u3002"""
        self.assertIn("def send_text(self, text, enter=False, append=False):", self.src)
        stubs = self.src.count("def send_text(self, text, enter=False, append=False):")
        self.assertEqual(stubs, 2, "\u771f\u7c7b + \u964d\u7ea7\u5360\u4f4d\u7c7b\u5404\u4e00\u4efd")


class FrontendTest(unittest.TestCase):
    """\u524d\u7aef\u9ed8\u8ba4\u503c\u4e0e\u6309\u94ae\u5b58\u5728\u3002"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_default_is_append(self):
        # \u53ea\u6709\u663e\u5b58\u8fc7 "0" \u624d\u5173\u95ed\uff1a\u7b2c\u4e00\u6b21\u7528\u7684\u4eba\u9ed8\u8ba4\u5c31\u662f\u8ffd\u52a0
        self.assertIn('localStorage.getItem("atv.append") !== "0"', self.js)

    def test_button_and_hint_exist(self):
        self.assertIn('id="atvAppendBtn"', self.html)
        self.assertIn('id="atvAppendHint"', self.html)

    def test_mode_sent_only_for_appletv(self):
        """Android \u603b\u662f\u8ffd\u52a0\uff0c\u4e0d\u5e26 mode \u5b57\u6bb5\u3002"""
        self.assertIn('status.curType === "appletv"', self.js)
        self.assertIn('mode: atvAppend.on ? "append" : "set"', self.js)

    def test_visibility_follows_device_type(self):
        self.assertIn("applyAtvAppend();", self.js)


if __name__ == "__main__":
    unittest.main()
