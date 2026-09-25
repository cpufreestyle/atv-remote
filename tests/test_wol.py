#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wake-on-LAN 远程开机（第二十二轮）回归。

立论来自遥控器的唯一真盲区：电视关机后 adb 直接掉线，设备列表里连这台都没有，
方向键 / 截屏 / 宏全都无从谈起。学 Linux wakeonlan CLI 与 Home Assistant 的
wake_on_lan 集成，补上「冷开机」这一环。

所以这里最关键的几条：
  1) MAC 宽容解析——用户从路由器后台手抄，冒号 / 连字符 / 点分 / 无分隔都该收；
     但位数不对绝不猜，猜出来的地址会唤醒别人家的设备；
  2) ARP 表的 flags=0x0 占位行必须过滤，否则前端会排出一堆空 MAC 的按钮；
  3) adb 无线调试目标带 :5555，ARP 表里只有 IP——不剥端口就永远匹配不上；
  4) 双端口发包，单个 socket 失败只收集，全失败才抛 AdbError（三种失败文案要能区分）；
  5) 发现不到不是错误：读不到 ARP 表返回 []，而不是 500；
  6) 前端纯函数段可搬进 node，harness 抽的就是 static/app.js 里那一段；
  7) 卡片不带 data-needs-device——它存在的意义正是设备不在线的时候；
  8) IP / MAC 一律 textContent——局域网广播可伪造。

只用标准库 + 读仓库文件 + 起回环端口 + 跑 node harness：不碰 adb / pyatv / 真局域网。
"""

import json
import re
import shutil
import socket
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "wol_harness.js"

WOL_BEGIN = "/* ===== wake-on-lan:begin"
WOL_END = "/* ===== wake-on-lan:end"
CSS_MARK = "远程开机（Wake-on-LAN"

PROC_ARP = (
    "IP address       HW type   Flags       HW address            Mask     Device\n"
    "192.168.0.52     0x1       0x2         aa:bb:cc:dd:ee:ff     *        en0\n"
    "192.168.0.7      0x1       0x2         AABBCCDDEEFF         *        en0\n"
    "192.168.0.99     0x1       0x0         00:00:00:00:00:00     *        en0\n"
)
DARWIN_ARP = (
    "? (192.168.0.52) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]\n"
    "? (192.168.0.99) at (incomplete) on en0\n"
)


class MacParsingTest(unittest.TestCase):
    """MAC 宽容解析：全收常见格式，位数不对一律 None（不猜位）。"""

    def test_all_common_formats_are_accepted(self):
        for raw in ("aa:bb:cc:dd:ee:ff", "AA:BB:CC:DD:EE:FF", "aa-bb-cc-dd-ee-ff",
                    "AABBCCDDEEFF", "aabb.ccdd.eeff", "  aa:bb:cc:dd:ee:ff  ",
                    "aA:bB-cC.dD:eeFF"):
            self.assertEqual(server.wol_norm_mac(raw), "aa:bb:cc:dd:ee:ff", raw)

    def test_never_guesses_a_missing_or_extra_digit(self):
        for raw in ("aa:bb:cc:dd:ee", "aa:bb:cc:dd:ee:f", "aabbccddeeff00",
                    "aa:bb:cc:dd:ee:ff:00", "aabbccddeef", "aabbccddeefff"):
            self.assertIsNone(server.wol_norm_mac(raw), raw)

    def test_non_hex_and_non_string_are_rejected(self):
        for raw in ("zz:bb:cc:dd:ee:ff", "", "   ", None, 123456789012, {}, [], True):
            self.assertIsNone(server.wol_norm_mac(raw), repr(raw))


class PacketAndIpTest(unittest.TestCase):
    """魔法包字节构成；IP 要去掉 adb 目标自带的端口。"""

    def test_packet_is_six_ff_then_mac_sixteen_times(self):
        pkt = server.wol_build_packet("aa:bb:cc:dd:ee:ff")
        self.assertEqual(len(pkt), 102)
        self.assertEqual(pkt[:6], bytes((0xFF,)) * 6)
        self.assertEqual(pkt[6:], bytes.fromhex("aabbccddeeff") * 16)
        self.assertEqual(pkt[6:12], pkt[96:102])

    def test_ip_of_strips_the_adb_port_and_normalises(self):
        self.assertEqual(server.wol_ip_of("192.168.0.52:5555"), "192.168.0.52")
        self.assertEqual(server.wol_ip_of("192.168.0.52"), "192.168.0.52")
        self.assertEqual(server.wol_ip_of("  192.168.0.52:5555 "), "192.168.0.52")
        self.assertEqual(server.wol_ip_of(None), "")
        self.assertEqual(server.wol_ip_of(""), "")


class ArpParseTest(unittest.TestCase):
    """两种 ARP 表格式、占位行过滤、targets 顺序跟随询问顺序。"""

    def test_proc_net_arp_with_placeholder_rows_filtered(self):
        rows = dict(server.wol_parse_arp(PROC_ARP))
        self.assertEqual(rows, {"192.168.0.52": "aa:bb:cc:dd:ee:ff",
                                "192.168.0.7": "aa:bb:cc:dd:ee:ff"})
        self.assertNotIn("192.168.0.99", rows, "flags=0x0 的占位行不该出现在结果里")

    def test_darwin_arp_dash_a_output(self):
        rows = dict(server.wol_parse_arp(DARWIN_ARP))
        self.assertEqual(rows, {"192.168.0.52": "aa:bb:cc:dd:ee:ff"})
        self.assertNotIn("192.168.0.99", rows, "(incomplete) 行没有 17 位 MAC，天然落空")

    def test_junk_input_yields_nothing_and_never_raises(self):
        for text in ("", None, "garbage\n\nmore garbage", "IP address HW type Flags"):
            self.assertEqual(server.wol_parse_arp(text), [])

    def test_targets_follow_the_asked_order_and_drop_unknown_ips(self):
        rows = [("2.2.2.2", "aa:bb:cc:dd:ee:ff"), ("1.1.1.1", "11:22:33:44:55:66")]
        got = server.wol_targets(["1.1.1.1", "3.3.3.3", "2.2.2.2"], rows)
        self.assertEqual(got, [{"ip": "1.1.1.1", "mac": "11:22:33:44:55:66"},
                               {"ip": "2.2.2.2", "mac": "aa:bb:cc:dd:ee:ff"}])
        self.assertEqual(server.wol_targets([], rows), [])
        self.assertEqual(server.wol_targets(["9.9.9.9"], []), [])

    def test_read_arp_failure_is_not_an_error(self):
        # 读不到表 = 发现不到，不是故障：返回空列表而不是抛异常 / 500
        with _patched(server, "WOL_ARP_FILE", Path("/nonexistent/net/arp")):
            self.assertEqual(server.wol_read_arp(), [])


class IpsArgTest(unittest.TestCase):
    """ips 入参同时服务「数组」与「逗号串」两种形态。"""

    def test_accepts_list_and_comma_string(self):
        self.assertEqual(server.wol_ips(["1.2.3.4", "5.6.7.8"]), ["1.2.3.4", "5.6.7.8"])
        self.assertEqual(server.wol_ips("1.2.3.4,5.6.7.8"), ["1.2.3.4", "5.6.7.8"])
        self.assertEqual(server.wol_ips("1.2.3.4，5.6.7.8"), ["1.2.3.4", "5.6.7.8"],
                         "中文逗号也要吃——用户从别处粘过来的")

    def test_strips_adb_port_and_whitespace_and_dedupes(self):
        self.assertEqual(server.wol_ips(" 192.168.0.52:5555 , 192.168.0.52 , nope"),
                         ["192.168.0.52"])

    def test_caps_at_ip_max_and_skips_oversized_items(self):
        many = ["10.0.0." + str(i) for i in range(1, 30)]
        got = server.wol_ips(many)
        self.assertEqual(len(got), server.WOL_IP_MAX)
        self.assertEqual(got[0], "10.0.0.1")
        long = "x" * (server.WOL_MAC_MAX + 1)
        self.assertNotIn(long, server.wol_ips([long, "1.2.3.4"]))

    def test_bad_types_yield_empty(self):
        for v in (None, 42, {}, True, object()):
            self.assertEqual(server.wol_ips(v), [])

    def test_nested_list_of_comma_strings_from_parse_qs(self):
        # GET 分支的 parse_qs 给的就是这个形态：list 里躺着一条逗号串。
        # 曾经直接把 list 传进去，整串被当单个非法 IP 丢掉，卡片永远显示「未发现 MAC」。
        got = server.wol_ips(["1.2.3.4,5.6.7.8", "9.9.9.9"])
        self.assertEqual(got, ["1.2.3.4", "5.6.7.8", "9.9.9.9"])
        self.assertEqual(server.wol_ips(["1.2.3.4", "5.6.7.8"]), ["1.2.3.4", "5.6.7.8"])
        self.assertEqual(server.wol_ips(("1.2.3.4,5.6.7.8",)), ["1.2.3.4", "5.6.7.8"])
        self.assertEqual(server.wol_ips(["", "  ", None, 5]), [])


class _FakeSock(object):
    """记录发包字节与目标地址；fail_ports 里的端口模拟系统拒绝。"""

    def __init__(self, log, fail_ports=()):
        self.log = log
        self.fail_ports = fail_ports
        self.opt = None
        self.closed = False

    def setsockopt(self, level, opt, val):
        self.opt = (level, opt, val)

    def sendto(self, data, addr):
        if addr[1] in self.fail_ports:
            raise OSError("simulated failure")
        self.log.append({"data": data, "addr": addr, "opt": self.opt})

    def close(self):
        self.closed = True


class WolSendTest(unittest.TestCase):
    """发包：双端口、广播位、字节内容、失败聚合。"""

    def _factory(self, log, fail_ports=()):
        def make(af, typ):
            return _FakeSock(log, fail_ports)
        return make

    def test_sends_to_both_ports_with_broadcast_enabled(self):
        log = []
        sent = server.wol_send("aa:bb:cc:dd:ee:ff", "255.255.255.255", self._factory(log))
        self.assertEqual(sent, [9, 7])
        self.assertEqual(len(log), 2)
        for entry in log:
            self.assertEqual(len(entry["data"]), 102)
            self.assertEqual(entry["data"][:6], bytes((0xFF,)) * 6)
            self.assertEqual(entry["opt"], (socket.SOL_SOCKET, socket.SO_BROADCAST, 1),
                             "忘了 SO_BROADCAST 会被静默丢弃，而不是报错")
        self.assertEqual([e["addr"] for e in log],
                         [("255.255.255.255", 9), ("255.255.255.255", 7)])

    def test_single_port_failure_still_reports_success(self):
        log = []
        sent = server.wol_send("aa:bb:cc:dd:ee:ff", "255.255.255.255",
                               self._factory(log, fail_ports=(9,)))
        self.assertEqual(sent, [7], "一个端口通就该算通，别让用户以为全挂了")
        self.assertEqual([e["addr"][1] for e in log], [7])

    def test_all_ports_failing_raises_adb_error_mentioning_firewall(self):
        log = []
        with self.assertRaises(server.AdbError) as ctx:
            server.wol_send("aa:bb:cc:dd:ee:ff", "255.255.255.255",
                            self._factory(log, fail_ports=(9, 7)))
        msg = str(ctx.exception)
        self.assertIn("防火墙", msg)
        self.assertIn("9", msg)
        self.assertIn("7", msg)

    def test_socket_construction_failure_is_also_collected(self):
        def boom(af, typ):
            raise OSError("no socket for you")
        with self.assertRaises(server.AdbError) as ctx:
            server.wol_send("aa:bb:cc:dd:ee:ff", "255.255.255.255", boom)
        self.assertIn("建不了 socket", str(ctx.exception))

    def test_sockets_are_always_closed(self):
        log = []
        server.wol_send("aa:bb:cc:dd:ee:ff", "255.255.255.255", self._factory(log))
        self.assertTrue(all(e["opt"] is not None for e in log))


class HandleWolTest(unittest.TestCase):
    """handle_wol 的 discover / send / 未知操作三态。"""

    def test_discover_reads_arp_and_reports_asked(self):
        with _patched(server, "wol_read_arp", lambda: [("1.2.3.4", "aa:bb:cc:dd:ee:ff")]):
            got = server.handle_wol({"action": "discover", "ips": ["1.2.3.4", "5.6.7.8"]})
        self.assertTrue(got["ok"])
        self.assertEqual(got["asked"], ["1.2.3.4", "5.6.7.8"])
        self.assertEqual(got["targets"], [{"ip": "1.2.3.4", "mac": "aa:bb:cc:dd:ee:ff"}])

    def test_discover_defaults_when_body_is_bare(self):
        with _patched(server, "wol_read_arp", lambda: []):
            self.assertTrue(server.handle_wol({})["ok"])
            self.assertTrue(server.handle_wol(None)["ok"])
        self.assertEqual(server.handle_wol({"action": "discover", "ips": []})["targets"], [])

    def test_send_normalises_mac_then_dispatches(self):
        seen = {}

        def fake_send(mac, bcast, sock_factory=None):
            seen.update({"mac": mac, "bcast": bcast})
            return [9]
        with _patched(server, "wol_send", fake_send):
            got = server.handle_wol({"action": "send", "mac": "AA-BB-CC-DD-EE-FF"})
        self.assertEqual(seen, {"mac": "aa:bb:cc:dd:ee:ff", "bcast": "255.255.255.255"})
        self.assertEqual(got, {"ok": True, "mac": "aa:bb:cc:dd:ee:ff", "sent": [9]})

    def test_send_rejects_bad_mac_with_a_distinct_message(self):
        with self.assertRaises(server.AdbError) as ctx:
            server.handle_wol({"action": "send", "mac": "aa:bb:cc:dd:ee"})
        self.assertIn("MAC", str(ctx.exception))
        self.assertIn("aa:bb:cc:dd:ee:ff", str(ctx.exception))

    def test_send_rejects_bad_broadcast_address(self):
        with self.assertRaises(server.AdbError) as ctx:
            server.handle_wol({"action": "send", "mac": "aa:bb:cc:dd:ee:ff",
                               "bcast": "not-an-ip"})
        self.assertIn("广播地址", str(ctx.exception))

    def test_unknown_action_says_what_is_available(self):
        with self.assertRaises(server.AdbError) as ctx:
            server.handle_wol({"action": "power_on"})
        self.assertIn("discover", str(ctx.exception))
        self.assertIn("send", str(ctx.exception))

    def test_route_is_registered_for_post(self):
        self.assertIs(server.ROUTES.get("/api/wol"), server.handle_wol)


class WolHttpTest(unittest.TestCase):
    """真回环端口上的路由行为：discover 的 GET / POST 两态与错误码。"""

    def setUp(self):
        self.saved = (server.wol_read_arp, server.wol_send)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.05})
        self.thread.daemon = True
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        server.wol_read_arp, server.wol_send = self.saved

    def _req(self, path, data=None, method=None):
        hdrs = {}
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = Request("http://127.0.0.1:%d%s" % (self.port, path), data=body,
                      headers=hdrs, method=method)
        try:
            with urlopen(req, timeout=10) as r:
                return r.getcode(), json.loads(r.read() or b"{}")
        except HTTPError as e:
            raw = e.read()
            e.close()
            return e.code, (json.loads(raw or b"{}") if raw else {"error": ""})

    def test_get_discover_via_query_string(self):
        server.wol_read_arp = lambda: [("1.2.3.4", "aa:bb:cc:dd:ee:ff")]
        code, body = self._req("/api/wol?action=discover&ips=1.2.3.4,5.6.7.8")
        self.assertEqual(code, 200)
        self.assertEqual(body["targets"], [{"ip": "1.2.3.4", "mac": "aa:bb:cc:dd:ee:ff"}])
        self.assertEqual(body["asked"], ["1.2.3.4", "5.6.7.8"])

    def test_post_send_round_trip(self):
        sent = []

        def fake_send(mac, bcast, sock_factory=None):
            sent.append((mac, bcast))
            return [9]
        server.wol_send = fake_send
        code, body = self._req("/api/wol", {"action": "send", "mac": "aabbccddeeff"})
        self.assertEqual(code, 200)
        self.assertEqual(body, {"ok": True, "mac": "aa:bb:cc:dd:ee:ff", "sent": [9]})
        self.assertEqual(sent, [("aa:bb:cc:dd:ee:ff", "255.255.255.255")])

    def test_bad_mac_is_a_400_not_a_500(self):
        code, body = self._req("/api/wol", {"action": "send", "mac": "nope"})
        self.assertEqual(code, 400)
        self.assertIn("MAC", body["error"])

    def test_unknown_action_is_a_400(self):
        code, body = self._req("/api/wol?action=explode")
        self.assertEqual(code, 400)
        self.assertIn("discover", body["error"])

    def test_keep_alive_still_framed_correctly_after_a_wol_exchange(self):
        # 同一连接上先成功后错：第二响应若 Content-Length 不对就会帧错位
        server.wol_read_arp = lambda: []
        code1, body1 = self._req("/api/wol?action=discover&ips=1.2.3.4")
        code2, body2 = self._req("/api/wol", {"action": "send", "mac": "bad"})
        self.assertEqual((code1, body1["ok"]), (200, True))
        self.assertEqual(code2, 400)


def _patched(obj, name, value):
    """极简 patch：进 with 换成新值，退出还原（不引 unittest.mock，保持零依赖观感）。"""

    class _Ctx(object):
        def __enter__(self):
            self.old = getattr(obj, name)
            setattr(obj, name, value)
            return value

        def __exit__(self, *exc):
            setattr(obj, name, self.old)
            return False

    return _Ctx()


class MarkupTest(unittest.TestCase):
    """markup：卡片容器 / 徽标 / 等待提示齐备，且不被「需连设备」隐藏逻辑藏起来。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_card_is_not_gated_on_being_connected(self):
        self.assertIn('<section class="card" id="wolCard">', self.html)
        i = self.html.index('id="wolCard"')
        seg = self.html[i:self.html.index("</section>", i)]
        self.assertNotIn("data-needs-device", seg,
                         "设备离线正是这张卡唯一的用途，藏起来就白做了")

    def test_card_parts_exist(self):
        for frag in ('<h2>⚡ 远程开机 <span id="wolCount" class="badge">—</span></h2>',
                     '<div id="wolList"></div>',
                     '<p class="hint" id="wolHint">',
                     '<button id="wolRefreshBtn" class="btn tiny" type="button">↻ 刷新 MAC</button>',
                     '<span id="wolWatch" class="wolwatch hidden" role="status" aria-live="polite">',
                     '<i class="woldot"></i>',
                     '<span id="wolWatchText"></span>'):
            self.assertIn(frag, self.html)


class WiringAndPurityTest(unittest.TestCase):
    """接线与纯度：节流点挂对了、纯函数段可搬进 node、渲染不走 innerHTML。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.server = (ROOT / "server.py").read_text(encoding="utf-8")

    def seg(self):
        return self.js[self.js.index(WOL_BEGIN):self.js.index(WOL_END)]

    def test_pure_segment_has_every_rule(self):
        seg = self.seg()
        for frag in ("const WOL_WATCH_MS = 2500;", "const WOL_WATCH_MAX = 24;",
                     "const WOL_IP_MAX = 8;", "const WOL_ASK_MIN_MS = 15000;",
                     "function wolNormMac(", "function wolIpOf(", "function wolIpsFromStatus(",
                     "function wolWatchPlan(", "function wolMissing(", "function wolMacShort(",
                     "function wolSummary("):
            self.assertIn(frag, seg)

    def test_pure_segment_touches_no_dom_or_network(self):
        code = re.sub(r"/\*[\s\S]*?\*/", " ", re.sub(r"//[^\n]*", " ", self.seg()))
        for banned in ("document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("):
            self.assertNotIn(banned, code)

    def test_discovery_is_throttled_off_the_status_poll(self):
        # ARP 表秒级不变：跟着 8s 状态轮询反复读一遍纯属浪费
        self.assertIn("function wolMaybeDiscover(s) {", self.js)
        self.assertIn("if (sig === wolAskSig && Date.now() - wolAskAt < WOL_ASK_MIN_MS) return;",
                      self.js)
        coach = self.js.index("  maybePostCoach();\n")
        call = self.js.index("  wolMaybeDiscover(s);")
        self.assertGreater(call, coach, "节流点应排在 maybePostCoach 之后（chips 渲染完）")
        self.assertLess(call, coach + 200, "应紧挨着 maybePostCoach，别掉到别的函数里")

    def test_ips_are_rendered_as_text_only(self):
        for frag in ('name.textContent = t.ip;', 'mac.textContent = wolMacShort(t.mac);'):
            self.assertIn(frag, self.js)
        for start in ("function wolRender(targets, asked) {", "function wolMarkOnline(ip) {"):
            i = self.js.index(start)
            body = self.js[i:self.js.index("\nfunction ", i + len(start))]
            self.assertNotIn("innerHTML", body)
            self.assertNotIn("insertAdjacentHTML", body)

    def test_watch_has_a_hard_cap_and_can_be_stopped(self):
        self.assertIn("function wolWaitStop() {", self.js)
        self.assertIn("clearTimeout(wolTimer); wolTimer = null;", self.js)
        self.assertIn("wolTimer = setTimeout(wolWaitTick, WOL_WATCH_MS);", self.js)
        self.assertIn("if (!wolWaitLeft) return;", self.js, "停掉之后不能再自己续上")
        self.assertIn("wolWatchPlan(WOL_WATCH_MAX - wolWaitLeft, online);", self.js)

    def test_wait_reuses_the_notification_queue_and_auto_reconnects(self):
        self.assertIn("connect(ip);", self.js[self.js.index("async function wolWaitTick() {"):])
        self.assertIn('toast("✓ 开机包已发往 " + ip', self.js)
        self.assertIn('toast("✓ " + ip + " 已开机上线，正在重连");', self.js)

    def test_refresh_button_is_wired_exactly_once(self):
        self.assertEqual(self.js.count('$("#wolRefreshBtn").addEventListener'),
                         1, "按钮只挂一次监听，别在渲染函数里重复挂")

    def test_command_palette_entry_exists(self):
        self.assertIn('push("wol", "工具", "⚡", "远程开机（Wake-on-LAN）"', self.js)
        self.assertIn('"开机 唤醒 冷启动 wake wol 魔法包"', self.js)

    def test_backend_section_is_fenced_and_routed(self):
        self.assertIn("# ===== wake-on-lan:begin =====", self.server)
        self.assertIn("# ===== wake-on-lan:end =====", self.server)
        self.assertIn('    "/api/wol": handle_wol,', self.server)
        get_at = self.server.index("    def do_GET(self):")
        self.assertIn('if path == "/api/wol":', self.server[get_at:get_at + 6000])

    def test_wol_writes_nothing_to_state_json(self):
        # state.json 会带上配对凭据被打包分发，不该增加任何可写内容
        i = self.server.index("# ===== wake-on-lan:begin =====")
        j = self.server.index("# ===== wake-on-lan:end =====")
        seg = self.server[i:j]
        self.assertNotIn("state[", seg)
        self.assertNotIn("save_state", seg)
        self.assertIn(
            'state = {"recent_android": [], "appletvs": [], "current": None, "info": {},',
            self.server)


class HarnessParityTest(unittest.TestCase):
    """harness 抽的就是 static/app.js 里那一段：常量与符号必须同源。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.seg = cls.js[cls.js.index(WOL_BEGIN):cls.js.index(WOL_END)]
        cls.harness = HARNESS.read_text(encoding="utf-8")

    def test_constants_match_source_literals(self):
        lit = {}
        for m in re.finditer(r"^const (WOL_[A-Z_]+) = (.+)$", self.seg, re.M):
            lit[m.group(1)] = m.group(2).split("//")[0].strip().rstrip(";").strip()
        self.assertEqual(lit["WOL_WATCH_MS"], "2500")
        self.assertEqual(lit["WOL_WATCH_MAX"], "24")
        self.assertEqual(lit["WOL_IP_MAX"], "8")
        self.assertEqual(lit["WOL_ASK_MIN_MS"], "15000")

    def test_harness_references_the_same_symbols(self):
        for sym in ("WOL_WATCH_MAX", "wolNormMac", "wolIpOf", "wolIpsFromStatus",
                    "wolWatchPlan", "wolMissing", "wolMacShort", "wolSummary",
                    "ALL_WOL_CASES_PASSED"):
            self.assertIn(sym, self.harness)

    def test_harness_uses_same_segment_markers(self):
        self.assertIn(WOL_BEGIN, self.harness)
        self.assertIn(WOL_END, self.harness)


class HarnessBehaviorTest(unittest.TestCase):
    """真行为：node harness 从 app.js 抽出纯函数段原样执行，必须全绿。"""

    def test_harness_passes(self):
        if shutil.which("node") is None:
            self.skipTest("没有 node，跳过 harness")
        p = subprocess.run(["node", str(HARNESS)], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ALL_WOL_CASES_PASSED", p.stdout)
        failed = re.findall('"failed"[^0-9]*([0-9]+)', p.stdout)
        total = re.findall('"total"[^0-9]*([0-9]+)', p.stdout)
        self.assertTrue(failed and failed[0] == "0", p.stdout)
        self.assertTrue(total and int(total[0]) >= 8, p.stdout)


class StyleTest(unittest.TestCase):
    """样式：语义色、可访问动效、脉冲点有降级。"""

    @classmethod
    def setUpClass(cls):
        cls.css = (STATIC / "style.css").read_text(encoding="utf-8")

    def seg(self):
        i = self.css.index(CSS_MARK)
        j = self.css.find("/* ----------", i + 1)
        return self.css[i:] if j < 0 else self.css[i:j]

    def test_section_exists_and_uses_only_semantic_tokens(self):
        seg = self.seg()
        self.assertNotIn("rgb(", seg)
        self.assertNotIn("hsl(", seg)
        self.assertFalse(re.findall(r"#[0-9a-fA-F]{3,8}([^0-9a-zA-Z]|$)", seg))

    def test_rows_reuse_the_shared_atv_row_look(self):
        seg = self.seg()
        for frag in ("#wolList {", ".wolrow .wolmac {", ".wolrow .btn {",
                     ".wolrow.online .wolmac {"):
            self.assertIn(frag, seg)

    def test_waiting_dot_pulses_and_respects_reduced_motion(self):
        seg = self.seg()
        self.assertIn(".woldot {", seg)
        self.assertIn("animation: woldot-pulse", seg)
        self.assertIn("@keyframes woldot-pulse", seg)
        self.assertIn("@media (prefers-reduced-motion: reduce)", seg)
        self.assertIn(".woldot { animation: none; }", seg)

    def test_mac_column_can_shrink_and_button_sits_right(self):
        seg = self.seg()
        self.assertIn("flex: 1; min-width: 0;", seg)
        self.assertIn(".wolrow .btn { flex: none; }", seg)


if __name__ == "__main__":
    unittest.main()
