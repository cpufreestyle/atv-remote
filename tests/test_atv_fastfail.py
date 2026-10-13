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

from atv_backend import friendly_error

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


class FriendlyErrorTest(unittest.TestCase):
    """pyatv 异常文案的本地化映射（不依赖 pyatv，两种环境都跑）。"""

    def test_auth_and_credentials_both_map_to_pairing_hint(self):
        # 这两个词在 pyatv 各版本里都出现过（AuthenticationError / NoCredentialsError /
        # InvalidCredentialsError），漏一个用户就会看到一句英文 traceback 而不是「去配对」
        for msg in ("AuthenticationError: nope", "NoCredentialsError",
                    "InvalidCredentialsError", "auth failed"):
            out = friendly_error(msg)
            self.assertTrue(out.startswith("需要配对或配对已失效"), msg)
            self.assertIn(msg, out, "原始文案要留着，报障时贴给别人看")

    def test_keyboard_focus_maps_to_actionable_hint(self):
        out = friendly_error("KeyboardError: no focused field")
        self.assertIn("聚焦", out)
        self.assertIn("方向键", out)

    def test_unknown_error_passes_through_unchanged(self):
        # 认不出的必须原样透出：包一层不知所云的话反而更难查
        self.assertEqual(friendly_error("CommandError: boom"), "CommandError: boom")

    def test_empty_and_none_do_not_crash(self):
        # 这条路径的输入来自 format(type(e).__name__, e)，空串理论上来不了，
        # 但 None / "" 不能把 _call 的异常包装本身变成新的异常
        self.assertEqual(friendly_error(""), "")
        self.assertEqual(friendly_error(None), "")

    def test_static_method_delegates_to_module_function(self):
        # 类上的静态方法只是薄壳，真正的口径在模块级（那边才测得到）。
        # pyatv 缺失时 Apple TV 功能整体禁用、_friendly 不在类上——这正是把它
        # 外提成模块级函数的原因，见 atv_backend.friendly_error 的 docstring
        from atv_backend import AppleTvManager, PYATV_AVAILABLE
        if not PYATV_AVAILABLE:
            self.assertFalse(hasattr(AppleTvManager, "_friendly"),
                             "无 pyatv 时 _friendly 应不可达，否则这条测试等于没跑")
            return
        self.assertEqual(AppleTvManager._friendly("auth x"), friendly_error("auth x"))


if __name__ == "__main__":
    unittest.main()
