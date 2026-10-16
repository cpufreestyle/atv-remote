# -*- coding: utf-8 -*-
"""Apple TV \u53cc\u51fb\uff08InputAction.DoubleTap\uff09\u56de\u5f52\uff08I22\uff09\u3002

\u53c2\u7167\uff1apyatv const.InputAction = SingleTap / DoubleTap / Hold\u3002\u53cc\u51fb\u5728 Apple TV \u4e0a
\u662f\u72ec\u7acb\u7684\u4e00\u4e2a\u52a8\u4f5c\uff08\u5982\u53cc\u51fb\u9876\u90e8\u680f\u5c55\u5f00\u63a7\u5236\u4e2d\u5fc3\u7b49\uff09\uff0c\u4e0e\u5355\u51fb\u4e24\u6b21\u4e0d\u540c\u3002

\u8fd9\u91cc\u56fa\u5316\u56db\u4ef6\u4e8b\uff1a\u540e\u7aef\u652f\u6301 action \u5b57\u6bb5\u3001\u8def\u7531\u5c42\u6821\u9a8c\u5408\u6cd5\u503c\u3001
\u524d\u7aef\u4e34\u65f6\u6a21\u5f0f\u4f7f\u4e0b\u4e00\u4e2a\u6309\u952e\u53d1 DoubleTap \u540e\u89e3\u9664\u5f85\u547d\u3001\u53ea\u5bf9 Apple TV \u53d1\u3002
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


class BackendDoubleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "atv_backend.py").read_text(encoding="utf-8")

    def test_action_param_supported(self):
        self.assertIn("def send_keys(self, codes, hold=False, action=None):", self.src)
        self.assertEqual(self.src.count("def send_keys(self, codes, hold=False, action=None):"), 2,
                         "\u771f\u7c7b + \u964d\u7ea7\u5360\u4f4d\u7c7b\u5404\u4e00\u4efd")

    def test_input_action_map_has_double(self):
        seg = self.src[self.src.index("def _input_action"):]
        seg = seg[:seg.index("def send_keys", 100)]
        for want in ('"tap": InputAction.SingleTap', '"hold": InputAction.Hold', '"double": InputAction.DoubleTap'):
            self.assertIn(want, seg, "\u52a8\u4f5c\u6620\u5c04\u7f3a " + want)

    def test_unknown_action_rejected(self):
        seg = self.src[self.src.index("def send_keys"):]
        seg = seg[:seg.index("def send_text")]
        self.assertIn("\u4e0d\u652f\u6301\u7684\u6309\u952e\u52a8\u4f5c", seg, "\u4e0d\u8ba4\u8bc6\u7684 action \u8981\u62d2\u7edd\uff08\u4e0d\u8981\u9759\u9ed8\u56de\u5230 SingleTap\uff09")


class RouteDoubleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "server.py").read_text(encoding="utf-8")

    def test_action_field_validated(self):
        self.assertIn('if act not in (None, "tap", "hold", "double")', self.src,
                      "\u8def\u7531\u5c42\u5fc5\u987b\u767d\u540d\u5355\u6821\u9a8c action\uff0c\u9632\u5fa1\u610f\u5916\u503c")

    def test_action_takes_priority_over_hold(self):
        """\u65e7\u5ba2\u6237\u7aef\u7528 hold\uff0c\u65b0\u5ba2\u6237\u7aef\u7528 action\uff1a\u4e24\u8005\u90fd\u5f97\u8ba9 send_keys \u770b\u5f97\u5230\u3002"""
        self.assertIn('action=act)', self.src)
        self.assertIn('hold=body.get("hold") is True', self.src)


class FrontendDoubleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_button_exists(self):
        self.assertIn('id="atvDoubleBtn"', self.html)

    def test_armed_mode_disarms_after_one_send(self):
        """\u53cc\u51fb\u662f\u4e34\u65f6\u6a21\u5f0f\uff0c\u53d1\u4e00\u6b21\u5c31\u89e3\u9664\uff1a\u907f\u514d\u7528\u6237\u8bef\u628a\u540e\u7eed\u6309\u952e\u90fd\u53d8\u6210\u53cc\u51fb\u3002"""
        seg = self.js[self.js.index("async function sendKey"):]
        seg = seg[:seg.index("async function sendText")]
        self.assertIn("dblArmed = false;", seg, "\u53d1\u4e00\u6b21\u540e\u5fc5\u987b\u89e3\u9664\u5f85\u547d")
        self.assertIn('sendAction(code, "double")', seg)

    def test_action_sent_via_api(self):
        self.assertIn('action: kind', self.js)
        self.assertIn('sendAction(code, "hold")', self.js)
        self.assertIn('sendAction(code, "double")', self.js)

    def test_visibility_follows_device(self):
        self.assertIn("applyAtvDouble();", self.js)


if __name__ == "__main__":
    unittest.main()
