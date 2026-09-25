#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键宏与首启自动令牌的回归（不起真 adb / 不连真电视）。

宏要在请求线程里同步校验（拼错立刻 400），在线程里串行执行、单步失败不拖垮整条；
令牌策略的四个分支（显式 / 回环 / 自动生成且落盘 / --no-token）必须各自成立。
"""

import threading
import unittest

import server


import time


class _FakeAdb:
    """记录收到的 shell 命令；input 放行，dumpsys power 报屏幕已亮，
    fail_pkgs 里的包名模拟「没装这个 App」（monkey 会失败）"""

    def __init__(self, fail_pkgs=()):
        self.calls = []
        self.fail_pkgs = set(fail_pkgs)

    def shell(self, serial, cmd, timeout=6):
        self.calls.append(cmd)
        if cmd == "dumpsys power":
            return "mWakefulness=Awake"
        if cmd.startswith("monkey -p "):
            pkg = cmd.split()[2]
            if pkg in self.fail_pkgs:
                raise server.AdbError("monkey 无法启动 {}".format(pkg))
        return ""

    def devices(self):
        return [{"serial": "s", "state": "device"}]


class MacroValidateTest(unittest.TestCase):
    def test_valid_steps_pass_through(self):
        steps = [{"type": "key", "codes": [25, 25]},
                 {"type": "app", "pkg": "com.netflix.ninja", "delay": 100},
                 {"type": "text", "text": "你好", "delay": 5000}]
        out = server.validate_macro_steps(steps)
        self.assertEqual(out, steps)

    def test_empty_or_oversized_steps_rejected(self):
        for bad in ([], [{"type": "key", "code": 25}] * 21, "key", None):
            with self.assertRaises(server.AdbError):
                server.validate_macro_steps(bad)

    def test_unknown_step_type_rejected(self):
        with self.assertRaises(server.AdbError):
            server.validate_macro_steps([{"type": "swipe", "x1": 0}])

    def test_key_step_requires_numeric_codes(self):
        with self.assertRaises(server.AdbError):
            server.validate_macro_steps([{"type": "key", "code": "abc"}])
        with self.assertRaises(server.AdbError):
            server.validate_macro_steps([{"type": "key", "codes": [1] * 33}])

    def test_text_step_rejects_empty_and_oversize(self):
        with self.assertRaises(server.AdbError):
            server.validate_macro_steps([{"type": "text", "text": "   "}])
        with self.assertRaises(server.AdbError):
            server.validate_macro_steps([{"type": "text", "text": "a" * (server.MAX_TEXT_LEN + 1)}])

    def test_app_step_normalizes_pkgs(self):
        steps = server.validate_macro_steps(
            [{"type": "app", "pkgs": ["ok.pkg", "bad pkg", "ok2.pkg"]}])
        self.assertEqual(steps[0]["pkgs"], ["ok.pkg", "ok2.pkg"])


class MacroRunTest(unittest.TestCase):
    def setUp(self):
        self.saved = (server.adb, dict(server.state),
                      server._macro_running.is_set(), server._macro_stop.is_set())
        self.saved_prog = (dict(server._macro_prog), server._macro_seq)
        server.adb = _FakeAdb()
        server.state.clear()
        server.state.update({"current": {"type": "android", "target": "s"}})
        server._screen_cache = {"ts": time.time(), "awake": True}
        server._macro_stop.clear()
        server._macro_running.clear()

    def tearDown(self):
        (server.adb, server.state,
         run, stop) = self.saved
        server._macro_running.set() if run else server._macro_running.clear()
        server._macro_stop.set() if stop else server._macro_stop.clear()
        prog, seq = self.saved_prog
        server._macro_prog.clear()
        server._macro_prog.update(prog)
        server._macro_seq = seq

    def test_steps_run_in_order_and_stop_event_halts_mid_run(self):
        steps = [{"type": "key", "code": 25},
                 {"type": "key", "code": 24},
                 {"type": "key", "code": 164}]
        server._macro_stop.clear()
        server._macro_worker("测试", steps)          # 同步跑完整条
        self.assertEqual(server.adb.calls,
                         ["input keyevent 25",
                          "input keyevent 24",
                          "input keyevent 164"])

    def test_cancel_during_delay_stops_before_next_step(self):
        calls = []
        real_wait = server._macro_stop.wait

        def stop_after_first(timeout):
            calls.append(timeout)
            return True          # 模拟「取消发生在延时里」
        server._macro_stop.wait = stop_after_first
        try:
            server._macro_worker("测试", [
                {"type": "key", "code": 25},
                {"type": "key", "code": 24, "delay": 3000}])
        finally:
            server._macro_stop.wait = real_wait
        self.assertEqual(calls, [3.0])               # 只等了第一步前的延时
        self.assertEqual(server.adb.calls, ["input keyevent 25"])   # 第二步没跑

    def test_failing_step_does_not_abort_whole_macro(self):
        server.adb = _FakeAdb(fail_pkgs=["not.installed.pkg"])
        steps = [{"type": "app", "pkg": "not.installed.pkg"},
                 {"type": "key", "code": 25}]
        server._macro_worker("测试", steps)          # 第一步失败不影响第二步
        self.assertEqual(server.adb.calls,
                         ["monkey -p not.installed.pkg -c android.intent.category.LAUNCHER 1",
                          "input keyevent 25"])

    def test_app_step_tries_pkgs_in_order_until_success(self):
        server.adb = _FakeAdb(fail_pkgs=["not.installed.pkg"])
        steps = [{"type": "app", "pkgs": ["not.installed.pkg", "ok.pkg"]},
                 {"type": "key", "code": 25}]
        server._macro_worker("测试", steps)
        self.assertEqual(server.adb.calls,
                         ["monkey -p not.installed.pkg -c android.intent.category.LAUNCHER 1",
                          "monkey -p ok.pkg -c android.intent.category.LAUNCHER 1",
                          "input keyevent 25"])

    def test_handle_macro_validate_before_thread(self):
        with self.assertRaises(server.AdbError):
            server.handle_macro({"steps": [{"type": "nope"}]})

    def test_handle_macro_runs_async_and_reports_state(self):
        done = threading.Event()
        real = server._macro_worker

        def fake_worker(name, steps):
            done.set()
        server._macro_worker = fake_worker
        try:
            r = server.handle_macro({"name": "演示", "steps": [{"type": "key", "code": 25}]})
        finally:
            server._macro_worker = real
        self.assertTrue(r["running"])
        self.assertTrue(done.wait(2))

    def _wait_idle(self):
        """等线程里的宏跑完（最多 3s），返回结束后的进度快照"""
        for _ in range(300):
            if not server._macro_running.is_set():
                break
            time.sleep(0.01)
        self.assertFalse(server._macro_running.is_set(), "宏没在 3s 内跑完")
        return server.macro_state()

    def test_progress_reports_total_done_and_failed(self):
        server.adb = _FakeAdb(fail_pkgs=["not.installed.pkg"])
        server.handle_macro({"name": "演示", "steps": [
            {"type": "app", "pkg": "not.installed.pkg"},
            {"type": "key", "code": 25},
            {"type": "key", "code": 24}]})
        s = self._wait_idle()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["done"], 3)
        self.assertEqual(s["failed"], 1)      # 失败计数：以前只进 stderr，现在可见
        self.assertEqual(s["name"], "演示")
        self.assertFalse(s["cancelled"])
        self.assertFalse(s["running"])
        self.assertGreater(s["run"], 0)
        # 三步都真的跑了：单步失败不中断整条宏
        self.assertEqual(server.adb.calls,
                         ["monkey -p not.installed.pkg -c android.intent.category.LAUNCHER 1",
                          "input keyevent 25",
                          "input keyevent 24"])

    def test_cancel_sets_cancelled_flag_in_progress(self):
        real_wait = server._macro_stop.wait
        server._macro_stop.wait = lambda t: True    # 模拟「取消发生在延时里」
        try:
            server.handle_macro({"name": "演示", "steps": [
                {"type": "key", "code": 25},
                {"type": "key", "code": 24, "delay": 3000}]})
        finally:
            server._macro_stop.wait = real_wait
        s = self._wait_idle()
        self.assertTrue(s["cancelled"])
        self.assertEqual(s["done"], 1)
        self.assertEqual(server.adb.calls, ["input keyevent 25"])

    def test_run_id_increments_between_runs(self):
        first = server.macro_state()["run"]
        server._macro_worker("直跑", [{"type": "key", "code": 25}])
        self.assertEqual(server.macro_state()["run"], first)   # worker 不碰运行代号
        server.handle_macro({"name": "甲", "steps": [{"type": "key", "code": 25}]})
        a = self._wait_idle()
        server.handle_macro({"name": "乙", "steps": [{"type": "key", "code": 24}]})
        b = self._wait_idle()
        self.assertGreater(a["run"], first)
        self.assertGreater(b["run"], a["run"])
        self.assertEqual(b["name"], "乙")


class TokenPolicyTest(unittest.TestCase):
    def test_explicit_token_wins(self):
        self.assertEqual(server.resolve_token("abc", "0.0.0.0", False, {}), "abc")

    def test_explicit_empty_token_means_no_auth(self):
        # 历史行为：显式 --token "" = 不鉴权，不该被自动生成顶掉
        self.assertEqual(server.resolve_token("", "0.0.0.0", False, {}), "")

    def test_env_token_used_when_flag_absent(self):
        self.assertEqual(server.resolve_token(None, "0.0.0.0", False, {}, "envtok"), "envtok")

    def test_explicit_overrides_env(self):
        self.assertEqual(server.resolve_token("flagtok", "0.0.0.0", False, {}, "envtok"), "flagtok")

    def test_loopback_host_needs_no_token(self):
        self.assertEqual(server.resolve_token(None, "127.0.0.1", False, {}), "")
        self.assertEqual(server.resolve_token(None, "localhost", False, {}), "")

    def test_no_token_flag_disables_auto_generation(self):
        self.assertEqual(server.resolve_token(None, "0.0.0.0", True, {}), "")

    def test_autogenerated_token_is_persisted_and_stable(self):
        st = {}
        tok = server.resolve_token(None, "0.0.0.0", False, st)
        self.assertTrue(tok)
        self.assertEqual(st["token"], tok)
        # 重启（state 读回来）后必须是同一个令牌，否则手机每次重启都要重新配
        self.assertEqual(server.resolve_token(None, "0.0.0.0", False, dict(st)), tok)

    def test_delay_only_step_is_valid(self):
        self.assertEqual(server.validate_macro_steps([{"delay": 300}]), [{"delay": 300}])

    def test_presets_are_valid(self):
        for m in server.PRESET_MACROS:
            server.validate_macro_steps(m["steps"], m["name"])


if __name__ == "__main__":
    unittest.main()
