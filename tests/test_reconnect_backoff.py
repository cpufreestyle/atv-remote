#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""掉线自动重连的退避序列回归。

「为什么是指数、为什么不加抖动」的论证记在 server.py reconnect_delay 的 docstring
（源头是 tenacity 9.1.4 wait_exponential 的 docstring 对两种场景的区分）。
这里只锁行为，不起真 adb、不碰网络。
"""

import unittest

import server


class ReconnectDelayTest(unittest.TestCase):
    def test_first_failure_waits_base(self):
        self.assertEqual(server.reconnect_delay(1), server.RECONNECT_BASE)

    def test_exponential_sequence(self):
        self.assertEqual(
            [server.reconnect_delay(n) for n in (1, 2, 3)],
            [server.RECONNECT_BASE,
             server.RECONNECT_BASE * 2,
             server.RECONNECT_BASE * 4],
        )

    def test_capped_at_max(self):
        self.assertEqual(server.reconnect_delay(50), server.RECONNECT_MAX_DELAY)
        # 失败次数很大也不能溢出成 OverflowError，答案就是上限
        self.assertEqual(server.reconnect_delay(10 ** 6), server.RECONNECT_MAX_DELAY)

    def test_no_failure_means_no_wait(self):
        self.assertEqual(server.reconnect_delay(0), 0.0)
        self.assertEqual(server.reconnect_delay(-3), 0.0)

    def test_base_and_cap_are_injectable(self):
        self.assertEqual(server.reconnect_delay(2, base=1.0, cap=100.0), 2.0)
        self.assertEqual(server.reconnect_delay(3, base=1.0, cap=3.5), 3.5)

    def test_never_shrinks(self):
        prev = 0.0
        for n in range(1, 128):
            cur = server.reconnect_delay(n)
            self.assertGreaterEqual(cur, prev, "第 " + str(n) + " 次反而等得更短")
            prev = cur

    def test_constant_backoff_is_gone(self):
        """老行为是固定 30s：头两次间隔完全一样。现在必须一次比一次长"""
        d1 = server.reconnect_delay(1)
        d2 = server.reconnect_delay(2)
        self.assertNotEqual(d1, d2)
        self.assertLess(d1, server.RECONNECT_MAX_DELAY)


if __name__ == "__main__":
    unittest.main()
