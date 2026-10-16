# -*- coding: utf-8 -*-
"""Apple TV \u539f\u751f\u957f\u6309\uff08InputAction.Hold\uff09\u56de\u5f52\uff08I21\uff09\u3002

\u52a8\u673a\uff1a\u524d\u7aef\u7684\u957f\u6309\u8fde\u53d1\u662f\u5feb\u901f\u70b9\u6309\u591a\u6b21\uff08\u6eda\u5217\u8868\uff09\uff0c
\u800c\u7535\u89c6\u7aef\u81ea\u5df1\u7684\u957f\u6309\u83dc\u5355\uff08\u957f\u6309 OK \u5f39\u4e0a\u4e0b\u6587\u83dc\u5355\uff09\u9700\u8981
pyatv \u7684 InputAction.Hold\u2014\u2014\u70b9\u51fb\u591a\u5c11\u6b21\u90fd\u6362\u4e0d\u6765\u3002
\u6240\u4ee5\u8fde\u53d1\u6ee1\u82e5\u5e72\u6b21\u540e\u505c\u6389\u70b9\u6309\u3001\u6539\u53d1\u4e00\u6b21 hold\u3002

\u53c2\u7167\uff1apyatv const.InputAction = SingleTap / DoubleTap / Hold\uff08const.py:200\uff09\u3002
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


class BackendHoldTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "atv_backend.py").read_text(encoding="utf-8")

    def test_send_keys_accepts_hold_flag(self):
        self.assertIn("def send_keys(self, codes, hold=False, action=None):", self.src)

    def test_uses_input_action_hold(self):
        seg = self.src[self.src.index('def _input_action'):]
        seg = seg[:seg.index("def send_keys", 100)]
        self.assertIn("InputAction.Hold", seg, "长按要映射到 InputAction.Hold”")
        seg2 = self.src[self.src.index("def send_keys"):]
        seg2 = seg2[:seg2.index("def send_text")]
        self.assertIn("_ia is not InputAction.SingleTap", seg2, "只有非单击才传动作参数”")

    def test_hold_falls_back_to_tap_on_mrp(self):
        """MRP 旧转发器不指 Hold：降级回单击，不让长按直接失效。"""
        seg = self.src[self.src.index("def send_keys"):]
        seg = seg[:seg.index("def send_text")]
        self.assertIn("try:", seg) and self.assertIn("return fn(_ia)", seg)
        self.assertLess(seg.index("return fn(_ia)"), seg.index("except Exception:"),
                        "动作调用应在降级分支之前”")
        self.assertIn("return fn()", seg)
        self.assertIn("return fn()", seg)

    def test_stub_signature_aligned(self):
        self.assertEqual(self.src.count("def send_keys(self, codes, hold=False, action=None):"), 2,
                         "\u771f\u7c7b + \u964d\u7ea7\u5360\u4f4d\u7c7b\u5404\u4e00\u4efd")


class RouteHoldTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "server.py").read_text(encoding="utf-8")

    def test_key_branch_passes_hold_flag(self):
        self.assertIn('action=act)', self.src) and self.assertIn('hold=body.get("hold") is True', self.src,
                      "\u8def\u7531\u5c42\u628a hold \u5b57\u6bb5\u900f\u7ed9\u540e\u7aef\uff08\u4e25\u683c bool\uff0c\u9632\u5fa1\u610f\u5916\u503c\uff09")


class FrontendHoldTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_hold_after_repeats_constant(self):
        self.assertIn("HOLD_AFTER_REPEATS = 5", self.js)

    def test_repeat_stops_then_sends_hold(self):
        """\u8fde\u53d1\u5230\u9650\u540e\u5148\u6e05\u8ba1\u65f6\u5668\u3001\u518d\u53d1 hold\uff0c\u4e0d\u80fd\u65e2\u8fde\u53d1\u53c8\u53d1\u957f\u6309\u3002"""
        seg = self.js[self.js.index("if (kmHoldable(bound()))"):]
        seg = seg[:seg.index("// \u957f\u6309\u8fdb\u6539\u952e\u5f39\u7a97")]
        self.assertIn("clearInterval(rep)", seg)
        self.assertIn("holdSend(code)", seg)
        self.assertLess(seg.index("clearInterval(rep)"), seg.index("holdSend(code)"),
                        "\u5148\u505c\u8fde\u53d1\u518d\u53d1 hold\uff0c\u9632\u5fa1\u53cc\u91cd\u89e6\u53d1")

    def test_hold_send_only_for_appletv(self):
        """Android \u6ca1\u6709\u539f\u751f\u957f\u6309\u8bed\u4e49\uff08adb input \u65e0\u957f\u6309\u53c2\u6570\uff09\u3002"""
        seg = self.js[self.js.index("async function sendAction"):]
        seg = seg[:seg.index("\u957f\u6309\u662f\u5426\u8fde\u53d1") if "\u957f\u6309\u662f\u5426\u8fde\u53d1" in seg else seg.index("function kmHoldable")]
        self.assertIn('status.curType !== "appletv"', seg)

    def test_hold_sends_flag(self):
        self.assertIn('action: kind', self.js)


if __name__ == "__main__":
    unittest.main()
