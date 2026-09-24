#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""智能唤醒与睡眠定定的逻辑回归（不起真 adb / 不连真电视）。

屏幕熄灭时 `input` 会阻塞到超时：发命令前必须先探屏幕，熄了代发唤醒键并等点亮。
这里用回放脚本的假 adb 把三条路径钉住：已点亮不打扰、熄灭时代发 224、迟迟不亮
时有界返回（不无限轮询）。fire 路径验证 Android 发的是幂等的 223 而不是会翻转
状态的电源键 26。
"""

import threading
import time
import unittest

import server


class _FakeAdb:
    """按脚本回放 dumpsys / input 的返回，记录收到的命令"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def shell(self, serial, cmd, timeout=6):
        self.calls.append(cmd)
        if cmd == "dumpsys power":
            out = self.script.pop(0)
            if out is None:
                raise server.AdbError("设备连接中断，请重新连接")
            return out
        if cmd.startswith("input keyevent"):
            return ""
        raise AssertionError("unexpected command: " + cmd)


class SmartWakeTest(unittest.TestCase):
    def setUp(self):
        self.saved = (server.adb, dict(server._screen_cache))
        server.adb = None
        server._screen_cache = {"ts": 0.0, "awake": True}

    def tearDown(self):
        server.adb, server._screen_cache = self.saved

    def test_awake_device_is_not_disturbed(self):
        server.adb = _FakeAdb(["mWakefulness=Awake"])
        self.assertTrue(server.screen_awake("s"))
        server.ensure_awake("s")
        self.assertEqual(server.adb.calls, ["dumpsys power"])  # 没白发唤醒键

    def test_asleep_device_gets_wakeup_and_polls_until_lit(self):
        fake = _FakeAdb(["mWakefulness=Asleep", "mWakefulness=Awake"])
        server.adb = fake
        server.ensure_awake("s")
        self.assertEqual(fake.calls,
                         ["dumpsys power", "input keyevent 224", "dumpsys power"])
        self.assertTrue(server._screen_cache["awake"])

    def test_asleep_that_stays_asleep_is_bounded(self):
        fake = _FakeAdb(["mWakefulness=Asleep"] * 20)
        server.adb = fake
        t0 = time.time()
        server.ensure_awake("s")
        self.assertLess(time.time() - t0, 4.0)          # 最多约 2s 轮询就返回
        self.assertLessEqual(len(fake.calls), 20)       # 没有无限轮询

    def test_state_probe_failure_does_not_block(self):
        """dumpsys 本身失败（掉线等）时不代发命令，让后续命令用自己的错误说话"""
        fake = _FakeAdb([None])
        server.adb = fake
        server.ensure_awake("s")
        self.assertEqual(fake.calls, ["dumpsys power"])
        self.assertFalse(any(c.startswith("input") for c in fake.calls))

    def test_wake_codes_in_body(self):
        for body in ({"codes": [26]}, {"codes": [19, 223]}, {"code": 224},
                     {"codes": ["26"]}):
            self.assertTrue(server.wake_codes_in_body(body), body)
        for body in ({"code": 19}, {"codes": [85, 66]}, {}, {"codes": ["x"]}):
            self.assertFalse(server.wake_codes_in_body(body), body)


class SleepTimerLoopTest(unittest.TestCase):
    """后台循环要真的把到点的定时吃掉：触发一次、清零、停机即退。
    （曾经漏了 global _sleep_until，循环每秒在日志里刷 UnboundLocalError，
    定时永远不触发 —— 这条用例就是钉这个事的）"""

    def test_loop_fires_once_clears_and_stops(self):
        fired = []
        orig_fire = server._fire_sleep_timer
        saved = (server._sleep_until, server._timer_stop)
        server._fire_sleep_timer = lambda: fired.append(time.time())
        server._sleep_until = time.time() - 1     # 已到期
        server._timer_stop = threading.Event()
        t = threading.Thread(target=server._sleep_timer_loop, daemon=True)
        try:
            t.start()
            deadline = time.time() + 3
            while not fired and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(len(fired), 1)
            self.assertIsNone(server._sleep_until)     # 触发后清零，不会反复点火
            server._timer_stop.set()
            t.join(timeout=2)
            self.assertFalse(t.is_alive())             # 停机信号能退出循环
            time.sleep(1.2)
            self.assertEqual(len(fired), 1)
        finally:
            server._fire_sleep_timer = orig_fire
            server._sleep_until, server._timer_stop = saved


class SleepTimerFireTest(unittest.TestCase):
    """到点执行：Android 走幂等的 SLEEP 键（223），屏幕本来黑着就不再发命令"""

    def setUp(self):
        self.saved = (server.adb, dict(server._screen_cache), server.state["current"])
        server.adb = None
        server._screen_cache = {"ts": 0.0, "awake": True}
        with server.state_lock:
            server.state["current"] = {"type": "android", "target": "1.2.3.4:5555"}

    def tearDown(self):
        server.adb = self.saved[0]
        server._screen_cache = self.saved[1]
        with server.state_lock:
            server.state["current"] = self.saved[2]

    def test_awake_tv_receives_sleep_key(self):
        fake = _FakeAdb(["mWakefulness=Awake"])
        server.adb = fake
        server._fire_sleep_timer()
        self.assertIn("input keyevent 223", fake.calls)
        self.assertNotIn("input keyevent 26", fake.calls)   # 226 会翻转状态，不能用

    def test_already_asleep_tv_gets_nothing(self):
        fake = _FakeAdb(["mWakefulness=Asleep"])
        server.adb = fake
        server._fire_sleep_timer()
        self.assertEqual(fake.calls, ["dumpsys power"])


if __name__ == "__main__":
    unittest.main()
