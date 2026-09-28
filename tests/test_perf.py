"""调用时间线（perf waterfall，第二十轮）回归：环形缓冲 / 埋点覆盖 / 钩子 / 路由 / 前端契约。

立论是「看不见的调用没法优化」：把 adb fork、常驻 shell 命令、pyatv 协议调用、devices 缓存
命中这四类事件按发生顺序记进服务端环形缓冲，前端据此画瀑布（学 Chrome DevTools Network）。
所以这里最关键的三条测试是：缓冲真有上限（不会无限涨）、每条路径都记了数（不漏不失真）、
前端拿到的快照形状稳定（ago / window_ms / stats）。

只用标准库 + 读仓库文件 + 跑 node harness：不起端口、不联网、没有 fake adb 时相关用例自动 skip。
"""

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import server
import atv_backend

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "perf_waterfall_harness.js"


def fake_adb_path():
    """预览期的假 adb（/tmp/fakebin/adb）是真可执行文件；CI 上没有就跳过相关用例。"""
    cand = os.environ.get("ATV_TEST_ADB") or "/tmp/fakebin/adb"
    if os.path.isfile(cand) and os.access(cand, os.X_OK):
        return cand
    return None


def perf_seg(js):
    """抽出 perf-waterfall 纯函数段（marker 之间），与 node harness 同一套边界。"""
    s = js.index("/* ===== perf-waterfall:begin")
    e = js.index("/* ===== perf-waterfall:end")
    return js[s:e]


class PerfIsolationTest(unittest.TestCase):
    """每个用例跑前清空环形缓冲、跑后还原，互不污染。"""

    def setUp(self):
        self._saved = list(server._perf_events)
        self._saved_seq = server._perf_seq
        with server._perf_lock:
            server._perf_events.clear()

    def tearDown(self):
        with server._perf_lock:
            server._perf_events.clear()
            server._perf_events.extend(self._saved)
        server._perf_seq = self._saved_seq

    def snap(self):
        return server.perf_snapshot()


class PerfBufferTest(PerfIsolationTest):
    """环形缓冲与快照形状：上限、截断、统计口径。"""

    def test_ring_buffer_keeps_last_perf_max(self):
        for i in range(server.PERF_MAX + 25):
            server.perf_record("adb", "cmd-{}".format(i), 1.0)
        evs = self.snap()["events"]
        self.assertEqual(len(evs), server.PERF_MAX)
        self.assertEqual(len(server._perf_events), server.PERF_MAX)
        self.assertEqual(evs[0]["label"], "cmd-25")
        self.assertEqual(evs[-1]["label"], "cmd-84")
        self.assertLess(evs[0]["seq"], evs[-1]["seq"])

    def test_record_truncates_and_clamps(self):
        server.perf_record("bogus-kind", "x" * 200, -5, err="e" * 300)
        e = self.snap()["events"][-1]
        self.assertEqual(e["kind"], "adb")          # 白名单外回落 adb，前端阈值表才够用
        self.assertEqual(len(e["label"]), 80)
        self.assertEqual(len(e["err"]), server.PERF_ERR_LEN)
        self.assertEqual(e["ms"], 0)
        self.assertFalse(e["cache"])

    def test_snapshot_shape_has_no_absolute_timestamp(self):
        server.perf_record("shell", "input keyevent 26", 12)
        e = self.snap()["events"][-1]
        self.assertEqual(set(e), {"seq", "kind", "label", "ms", "cache", "err", "ago"})
        self.assertGreaterEqual(e["ago"], 0)
        st = self.snap()["stats"]
        self.assertEqual(set(st),
                         {"n", "p50", "p95", "max", "errs", "cache", "cache_rate", "hidden"})
        self.assertGreaterEqual(self.snap()["window_ms"], 2000)

    def test_events_older_than_window_are_hidden(self):
        server.perf_record("adb", "old-cmd", 3)
        server.perf_record("shell", "fresh-cmd", 4)
        with server._perf_lock:
            server._perf_events[0]["t"] -= (server.PERF_WINDOW_MS / 1000.0) + 5
        data = self.snap()
        labels = [e["label"] for e in data["events"]]
        self.assertNotIn("old-cmd", labels)
        self.assertIn("fresh-cmd", labels)
        self.assertGreaterEqual(data["stats"]["hidden"], 1)

    def test_cache_stats_and_percentiles(self):
        for i in range(4):
            server.perf_record("shell", "c{}".format(i), (i + 1) * 100, cache=True)
        server.perf_record("adb", "fork", 900)
        st = self.snap()["stats"]
        self.assertEqual(st["n"], 5)
        self.assertEqual(st["cache"], 4)
        self.assertEqual(st["cache_rate"], 0.8)
        self.assertEqual(st["max"], 900)
        self.assertLessEqual(st["p50"], st["p95"])

    def test_single_sample_percentiles_equal_the_sample(self):
        server.perf_record("shell", "only", 77)
        st = self.snap()["stats"]
        self.assertEqual((st["p50"], st["p95"], st["max"]), (77, 77, 77))

class AdbInstrumentationTest(PerfIsolationTest):
    """真埋点：拿假 adb 跑 devices / version，确认每条 adb 路径都留下了事件。"""

    @classmethod
    def setUpClass(cls):
        cls.adb_path = fake_adb_path()

    def setUp(self):
        super().setUp()
        if not self.adb_path:
            self.skipTest("没有 fake adb（/tmp/fakebin/adb），跳过真实埋点用例")
        self.adb = server.Adb(self.adb_path)

    def test_devices_and_cache_hit_are_recorded(self):
        first = self.adb.devices()
        self.assertTrue(first)
        self.assertEqual(first[0]["serial"], "192.168.9.9:5555")
        kinds = [e["kind"] for e in self.snap()["events"]]
        self.assertIn("adb", kinds)
        self.adb.devices()                     # TTL 内第二次：应命中缓存
        hits = [e for e in self.snap()["events"] if e["kind"] == "devices"]
        self.assertTrue(hits)
        self.assertTrue(all(e["cache"] and e["ms"] == 0 for e in hits))

    def test_reset_shell_and_invalidate_leave_zero_ms_marks(self):
        self.adb.invalidate_devices()
        self.adb.reset_shell()
        labels = sorted(e["label"] for e in self.snap()["events"])
        self.assertIn("invalidate_devices", labels)
        self.assertIn("reset_shell", labels)
        self.assertTrue(all(e["ms"] == 0 for e in self.snap()["events"]))

    def test_adb_timeout_is_recorded_before_raising(self):
        orig = subprocess.run

        def boom(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="adb version", timeout=1)

        subprocess.run = boom
        try:
            with self.assertRaises(server.AdbError):
                # version() 内部会把 AdbError 吞成 "unknown"，这里直接测 run()
                self.adb.run("version")
        finally:
            subprocess.run = orig
        evs = [e for e in self.snap()["events"] if e["label"] == "version"]
        self.assertTrue(evs)
        self.assertTrue(evs[-1]["err"])

    def test_missing_adb_binary_is_recorded_before_raising(self):
        orig = subprocess.run

        def boom(*a, **kw):
            raise FileNotFoundError("no such file")

        subprocess.run = boom
        try:
            with self.assertRaises(server.AdbError):
                self.adb.run("devices")
        finally:
            subprocess.run = orig
        self.assertTrue([e for e in self.snap()["events"] if e["err"]])


class InstrumentationCoverageTest(unittest.TestCase):
    """源码切片计数：新增调用路径若忘了埋点，这里红。"""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "server.py").read_text(encoding="utf-8")
        cls.back = (ROOT / "atv_backend.py").read_text(encoding="utf-8")

    def test_adb_run_records_every_path(self):
        seg = self.src[self.src.index("    def run(self, *args"):self.src.index("    def exists(self")]
        self.assertGreaterEqual(seg.count("perf_record("), 4)

    def test_shell_records_every_path(self):
        seg = self.src[self.src.index("    def shell(self, serial"):
                       self.src.index("# ---------------- 全局状态")]
        self.assertGreaterEqual(seg.count("perf_record("), 5)

    def test_devices_cache_hit_marker(self):
        seg = self.src[self.src.index("    def devices(self"):self.src.index("    def connect(self")]
        self.assertIn('perf_record("devices", "devices", 0.0, cache=True)', seg)

    def test_zero_ms_marker_lines(self):
        self.assertIn('perf_record("adb", "invalidate_devices", 0.0)', self.src)
        self.assertIn('perf_record("adb", "reset_shell", 0.0)', self.src)

    def test_backend_hook_is_wired(self):
        self.assertIn("def set_perf_recorder(fn):", self.back)
        self.assertIn('def _perf(label, ms, err=""):', self.back)
        self.assertIn('fn("pyatv", label, ms, False, err)', self.back)
        self.assertIn("atv_backend.set_perf_recorder(perf_record)", self.src)

    def test_backend_reraises_after_recording(self):
        seg = self.back[self.back.index("    def run(self, coro"):self.back.index("    @staticmethod")]
        self.assertIn("_perf(name, (time.monotonic() - t0) * 1000)", seg)
        self.assertIn("_perf(name, (time.monotonic() - t0) * 1000, str(e))", seg)
        self.assertIn("raise", seg)


class AtvBackendTimingTest(PerfIsolationTest):
    """钩子侧：pyatv 调用无论成功失败都记一笔，且异常原样透传不改语义。"""

    @classmethod
    def setUpClass(cls):
        cls.mgr = server.atv_mgr

    def setUp(self):
        super().setUp()
        if not hasattr(self.mgr, "run"):
            self.skipTest("pyatv 未安装（AppleTvManager 是降级占位），跳过")
        self.seen = []
        atv_backend.set_perf_recorder(
            lambda kind, label, ms, cache=False, err="": self.seen.append((kind, label, ms, cache, err)))

    def tearDown(self):
        atv_backend.set_perf_recorder(server.perf_record)
        super().tearDown()

    def test_failure_is_recorded_and_reraised(self):
        async def boom():
            raise RuntimeError("kaboom")

        with self.assertRaises(RuntimeError):
            self.mgr.run(boom(), label="boom")
        self.assertEqual(len(self.seen), 1)
        kind, label, ms, cache, err = self.seen[0]
        self.assertEqual((kind, label, err), ("pyatv", "boom", "kaboom"))
        self.assertGreaterEqual(ms, 0)
        self.assertFalse(cache)

    def test_success_records_no_error(self):
        async def fast():
            return 42

        self.assertEqual(self.mgr.run(fast(), label="fast"), 42)
        kind, label, ms, cache, err = self.seen[-1]
        self.assertEqual((kind, label, err), ("pyatv", "fast", ""))

    def test_default_label_falls_back_to_coro_name(self):
        async def my_probe():
            return 1

        self.mgr.run(my_probe())
        self.assertEqual(self.seen[-1][1], "my_probe")


class RouteTest(unittest.TestCase):
    """/api/perf：快照 + adb 环境信息，走统一 _send，不自己判权限。"""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "server.py").read_text(encoding="utf-8")

    def test_route_uses_shared_send(self):
        i = self.src.index('if path == "/api/perf":')
        seg = self.src[i:self.src.index("if path == ", i + 10)]
        self.assertIn("perf_snapshot()", seg)
        self.assertIn("return self._send(200,", seg)
        self.assertNotIn("self.wfile.write", seg)
        self.assertNotIn("_check_auth", seg)

    def test_route_sits_next_to_status(self):
        self.assertLess(self.src.index('if path == "/api/status":'),
                        self.src.index('if path == "/api/perf":'))

class FrontendContractTest(unittest.TestCase):
    """前端契约：纯函数段可搬进 node、卡片 markup 齐全、DOM 侧一律 textContent。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_pure_segment_touches_no_dom_or_network(self):
        seg = perf_seg(self.js)
        for banned in ("document.", "localStorage", "fetch(", "setInterval", "/api/", "innerHTML"):
            self.assertNotIn(banned, seg)
        self.assertIn("const PERF_SLOW_FE = { adb: 150, shell: 250, pyatv: 250, devices: 0 };", seg)

    def test_pure_segment_has_all_helpers(self):
        seg = perf_seg(self.js)
        for frag in ("function perfLevel(e)", "function perfPct(sorted, p)",
                     "function perfSummary(evs)", "function perfWindowMs(evs)",
                     "function perfBar(e, win)", "function perfBars(evs, opts)",
                     "const PERF_KIND_NAMES_FE =", "const PERF_MS_FMT_FE =",
                     "const perfClamp01 ="):
            self.assertIn(frag, seg)

    def test_geometry_does_not_fake_widths(self):
        seg = perf_seg(self.js)
        self.assertIn("const right = perfClamp01(1 - ago / w);", seg)
        self.assertIn("const width = perfClamp01(ms / w);", seg)
        self.assertIn("left: perfClamp01(right - width), width", seg)

    def test_html_card_markup(self):
        for frag in ('<section class="card" id="perfCard" data-needs-device data-collapsible data-state="open" data-collapse-name="调用时间线">',
                     '<div class="perfsum" id="perfSum"></div>',
                     '<div class="perfrows" id="perfRows" aria-live="polite"></div>',
                     '<button id="perfRefreshBtn" class="btn tiny">',
                     '<button id="perfOnlyBtn" class="btn tiny" aria-pressed="false">',
                     '<span class="hint" id="perfMeta"></span>'):
            self.assertIn(frag, self.html)

    def test_card_sits_between_tools_and_macros(self):
        self.assertLess(self.html.index('id="perfCard"'), self.html.index('id="macrosCard"'))

    def test_polling_is_gated_by_visibility_and_viewport(self):
        for frag in ("const PERF_POLL_FE = 2000;",
                     "setInterval(() => { if (pageVisible && perfCardInViewport()) perfFetch(); }, PERF_POLL_FE);",
                     "if (perfLastData) perfRender(perfLastData);   // 本地重画，不再发请求",
                     'fill.style.left = (b.left * 100).toFixed(2) + "%";',
                     'fill.style.width = (b.width * 100).toFixed(3) + "%";',
                     'row.append(perfMk("span", "pnote", b.note));'):
            self.assertIn(frag, self.js)

    def test_render_never_uses_inner_html(self):
        i = self.js.index("function perfRender(data) {")
        j = self.js.index("async function perfFetch() {")
        seg = self.js[i:j]
        self.assertNotIn("innerHTML", seg)
        self.assertNotIn("insertAdjacentHTML", seg)
        self.assertIn("el.textContent = txt;", self.js)   # perfMk 的统一出口

    def test_selector_ids_do_not_drift(self):
        ids = set(re.findall("[(].?#(perf[A-Za-z0-9_-]*).?[)]", self.js))
        for need in ("perfSum", "perfRows", "perfCard", "perfRefreshBtn", "perfOnlyBtn"):
            self.assertIn(need, ids)
            self.assertIn('id="{}"'.format(need), self.html)


class StyleTest(unittest.TestCase):
    """瀑布样式：只用语义色变量、无 hex、看不见的行也有 min-width。"""

    @classmethod
    def setUpClass(cls):
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def seg(self):
        i = self.css.index("调用时间线（waterfall）")
        return self.css[i:]

    def test_section_exists_and_has_no_hardcoded_colors(self):
        seg = self.seg()
        self.assertNotIn("rgb(", seg)
        self.assertNotIn("hsl(", seg)
        self.assertFalse(re.findall("#[0-9a-fA-F]{3,8}([^0-9a-zA-Z]|$)", seg))

    def test_level_selectors_present(self):
        seg = self.seg()
        for frag in (".perfsum .pchip", ".perfsum.err .pchip", ".prow {", ".prow.ok {",
                     ".prow.warn {", ".prow.err {", ".prow.cache {", ".prow .pkind {",
                     ".prow .plabel {", ".prow .ptrack {", ".prow .pfill {",
                     ".prow .pms {", ".prow .pnote {", ".prow.ok .pfill {",
                     ".prow.warn .pfill {", ".prow.err .pfill {", ".prow.cache .pfill {"):
            self.assertIn(frag, seg)

    def test_semantic_tokens_only(self):
        seg = self.seg()
        for frag in ("var(--ok)", "var(--warn)", "var(--danger)", "var(--ok-text)",
                     "var(--warn-text)", "var(--danger-text)", "var(--line-soft)",
                     "var(--line)", "var(--accent)", "var(--faint)"):
            self.assertIn(frag, seg)

    def test_invisible_rows_stay_visible(self):
        self.assertIn("min-width: 2px", self.seg())

    def test_rows_container_scrolls_instead_of_growing(self):
        # 40 行会把卡片撑到 1269px 高，手机上成了巨型卡片：容器限高 + 内滚
        seg = self.seg()
        self.assertIn(".perfrows {", seg)
        self.assertIn("max-height:", seg)
        self.assertIn("overflow-y: auto", seg)

    def test_wide_layout_keeps_card_together(self):
        self.assertIn("#macrosCard, #sleepCard, #perfCard { break-inside: avoid; }", self.css)


class HarnessBehaviorTest(unittest.TestCase):
    """真行为：node harness 从 app.js 抽出纯函数段原样执行，必须全绿。"""

    def test_harness_passes(self):
        if shutil.which("node") is None:
            self.skipTest("没有 node，跳过 harness")
        p = subprocess.run(["node", str(HARNESS)], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ALL_PERF_CASES_PASSED", p.stdout)
        failed = re.findall('"failed"[^0-9]*([0-9]+)', p.stdout)
        total = re.findall('"total"[^0-9]*([0-9]+)', p.stdout)
        self.assertTrue(failed and failed[0] == "0", p.stdout)
        self.assertTrue(total and int(total[0]) >= 12, p.stdout)


if __name__ == "__main__":
    unittest.main()
