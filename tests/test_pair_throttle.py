# -*- coding: utf-8 -*-
"""Apple TV \u914d\u5bf9 PIN \u8fde\u7eed\u8bd5\u9519\u7684\u8282\u6d41\u56de\u5f52\uff08I20\uff09\u3002

\u53c2\u8003\u6765\u6e90\uff1a\u53cd\u7f16\u8bd1 macOS /usr/libexec/tvremoted \u770b\u5230
deviceWithState:encounteredAuthChallengeOfType:attributes:codeToEnterOnDevice:
throttleSeconds: \u2014\u2014 Apple \u539f\u751f\u5b9e\u73b0\u628a\u201c\u518d\u8bd5\u7b49\u591a\u4e45\u201d\u76f4\u63a5\u4f20\u7ed9\u5ba2\u6237\u7aef\uff0c
\u56e0\u4e3a\u914d\u5bf9\u670d\u52a1\u7aef\u5bf9\u8fde\u7eed\u8bd5\u9519\u4f1a\u4e3b\u52a8\u5c01\u7981\uff0c\u76f2\u76ee\u91cd\u8bd5\u53ea\u4f1a\u8ba9\u5c01\u7981\u65f6\u95f4\u8d8a\u6765\u8d8a\u957f\u3002

\u8fd9\u91cc\u53ea\u9a8c\u8282\u6d41\u5668\u672c\u8eab\u7684\u884c\u4e3a\uff08\u4e0d\u8fde\u771f\u7535\u89c6\uff09\uff1a\u8ba1\u6570\u3001\u51b7\u5374\u3001\u6210\u529f\u6e05\u96f6\u3001\u6362\u8bbe\u5907\u4e0d\u53d7\u7d2f\u53ca\u3002
"""

import unittest
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402
from atv_backend import AppleTvError  # noqa: E402


def _reset():
    server._pair_attempts.update({"key": None, "count": 0, "first": 0.0})


class PairThrottleTest(unittest.TestCase):
    def setUp(self):
        _reset()

    def tearDown(self):
        _reset()

    def _fail_once(self, key):
        """\u590d\u523b server \u91cc finish \u5931\u8d25\u5206\u652f\u7684\u8ba1\u6570\u903b\u8f91\uff08\u4e0d\u8c03 atv_mgr\uff09\u3002"""
        import time
        now = time.time()
        if server._pair_attempts["key"] != key:
            server._pair_attempts.update({"key": key, "count": 1, "first": now})
        else:
            server._pair_attempts["count"] += 1
            if server._pair_attempts["count"] == 1:
                server._pair_attempts["first"] = now

    def test_below_limit_passes(self):
        server._pair_throttle_check("tv-1")  # \u4e0d\u629b
        for _ in range(server.PAIR_ATTEMPT_LIMIT - 1):
            self._fail_once("tv-1")
        server._pair_throttle_check("tv-1")  # \u8fd8\u5728\u9650\u5185\uff0c\u4e0d\u629b

    def test_limit_triggers_throttle(self):
        for _ in range(server.PAIR_ATTEMPT_LIMIT):
            self._fail_once("tv-1")
        with self.assertRaises(AppleTvError) as cm:
            server._pair_throttle_check("tv-1")
        msg = str(cm.exception)
        self.assertIn("\u8fde\u7eed", msg)
        self.assertIn("\u79d2", msg)

    def test_success_resets_counter(self):
        """\u6210\u529f\u914d\u5bf9\u540e\u8ba1\u6570\u6e05\u96f6\uff0c\u4e0b\u4e00\u8f6e\u8bd5\u9519\u4ece 0 \u5f00\u59cb\u3002"""
        for _ in range(server.PAIR_ATTEMPT_LIMIT):
            self._fail_once("tv-1")
        # \u6a21\u62df\u6210\u529f\u5206\u652f\u7684\u6e05\u96f6
        server._pair_attempts["count"] = 0
        server._pair_throttle_check("tv-1")  # \u4e0d\u629b

    def test_other_device_not_affected(self):
        """A \u7535\u89c6\u8bd5\u9519\u4e0d\u80fd\u628a B \u7535\u89c6\u4e5f\u5c01\u4e86\u3002"""
        for _ in range(server.PAIR_ATTEMPT_LIMIT):
            self._fail_once("tv-1")
        with self.assertRaises(AppleTvError):
            server._pair_throttle_check("tv-1")
        server._pair_throttle_check("tv-2")  # \u6362\u8bbe\u5907\u4e0d\u629b

    def test_cooling_window_expiry_resets(self):
        """\u51b7\u5374\u7a97\u8fc7\u5b8c\u540e\u91cd\u65b0\u5f00\u8ba1\uff08\u628a first \u65f6\u95f4\u6233\u62e8\u56de\u53bb\u6a21\u62df\u65f6\u95f4\u6d41\u901d\uff09\u3002"""
        import time
        for _ in range(server.PAIR_ATTEMPT_LIMIT):
            self._fail_once("tv-1")
        server._pair_attempts["first"] -= server.PAIR_THROTTLE_SECONDS * 2
        server._pair_throttle_check("tv-1")  # \u7a97\u5df2\u8fc7\uff0c\u4e0d\u629b\u4e14\u91cd\u7f6e
        self.assertEqual(server._pair_attempts["count"], 0)

    def test_limit_and_window_are_pinned(self):
        """\u53c2\u6570\u522b\u5355\u8fb9\u6539\uff1a\u9650\u6b21\u4e0e\u7a97\u957f\u662f\u4e00\u7ec4\u5bf9\u8282\u594f\u7684\u53c2\u6570\u3002"""
        self.assertEqual(server.PAIR_ATTEMPT_LIMIT, 5)
        self.assertEqual(server.PAIR_THROTTLE_SECONDS, 60.0)


if __name__ == "__main__":
    unittest.main()
