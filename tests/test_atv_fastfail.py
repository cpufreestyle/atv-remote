#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apple TV 命令路径的快速失败回归（需要 pyatv，无则跳过）。

背景：电视休眠时一条命令能占住 atv_backend 的 RLock 12s，之后每条按键都排在
锁后干等。O3 的结构性放开（锁横跨等待）要先确认 pyatv 并发安全，先行落地的是
「超时后短暂窗口内快速失败」这半边：在进锁之前就拒绝，第二键不再白等 12s。
"""

import asyncio
import time
import unittest

pyatv = None
try:
    import pyatv  # noqa: F401
except ImportError:
    pass


@unittest.skipIf(pyatv is None, "pyatv 未安装（用 .venv 跑 unittest 才覆盖）")
class FastFailTest(unittest.TestCase):
    def setUp(self):
        from atv_backend import AppleTvError, AppleTvManager
        self.mgr = AppleTvManager()
        self.err = AppleTvError
        self.window = __import__("atv_backend").FAST_FAIL_AFTER_TIMEOUT

    def tearDown(self):
        def _cancel_all():
            for t in asyncio.all_tasks(self.mgr.loop):
                t.cancel()   # 别把挂起的 sleep 留给 GC（会刷 Task was destroyed）
        self.mgr.loop.call_soon_threadsafe(_cancel_all)
        time.sleep(0.05)
        self.mgr.loop.call_soon_threadsafe(self.mgr.loop.stop)

    def test_fast_fail_right_after_timeout(self):
        self.mgr._last_timeout = time.time()
        t0 = time.time()
        with self.assertRaises(self.err) as cm:
            self.mgr.send_keys([19])
        self.assertIn("上一条命令无响应", str(cm.exception))
        self.assertLess(time.time() - t0, 1.0)   # 没进去等 12s

    def test_stale_timeout_does_not_block(self):
        """窗口已过：照常走原路径（这里没连设备，应报未连接而不是快速失败）"""
        self.mgr._last_timeout = time.time() - self.window - 1
        with self.assertRaises(self.err) as cm:
            self.mgr.send_keys([19])
        self.assertIn("未连接", str(cm.exception))

    def test_success_clears_fast_fail(self):
        """窗口已过一次成功执行后清零：别让一次抖动毒化后续所有按键"""
        self.mgr._last_timeout = time.time() - self.window - 1
        self.mgr._atv = object()          # 绕过 _require 的未连接检查

        def factory(atv):
            async def _do():
                return "ok"
            return _do()

        self.assertEqual(self.mgr._call(factory), "ok")
        self.assertEqual(self.mgr._last_timeout, 0.0)

    def test_run_records_timeout(self):
        """run 超时本身要记账，否则 _call 的窗口永远不触发"""
        with self.assertRaises(self.err) as cm:
            self.mgr.run(asyncio.sleep(60), timeout=0.1)
        self.assertIn("超时", str(cm.exception))
        self.assertGreater(self.mgr._last_timeout, 0.0)
        with self.assertRaises(self.err):   # 紧接着第二键快速失败
            self.mgr.send_keys([19])


if __name__ == "__main__":
    unittest.main()
