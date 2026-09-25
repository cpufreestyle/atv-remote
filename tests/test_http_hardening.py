#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP 层加固的回归测试。

check.sh 原本只做「能 import」的语法级检查，管不住行为；这个文件把几处必须成立的
约定钉住：配对凭据不外泄、Host 头不进可执行脚本、异常 body 不弄坏 keep-alive。

跑法：python3 -m unittest discover -s tests   （或直接 ./check.sh）
不依赖 adb / pyatv / 局域网：把回环豁免临时关掉，本机请求就走真实的「非本机来源」分支。
"""

import io
import json
import re
import socket
import sys
import tarfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server

TOKEN = "regression-t0ken"


class _Server(ThreadingHTTPServer):
    """客户端提前 RST 是这些用例故意造成的，别让 socketserver 往基线输出里倒堆栈；
    其它异常照常打印，免得把真问题也一起吞掉。"""

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)


def request(url, headers=None, data=None, method="GET"):
    req = Request(url, headers=headers or {}, data=data, method=method)
    try:
        with urlopen(req, timeout=10) as r:
            return r.getcode(), r.read(), dict(r.headers)
    except HTTPError as e:
        body = e.read()
        hdrs = dict(e.headers)
        e.close()   # 不关掉会在基线输出里刷 ResourceWarning
        return e.code, body, hdrs


class HttpHardeningTest(unittest.TestCase):
    def setUp(self):
        self.saved = (server.AUTH_TOKEN, server.LOOPBACK_HOSTS,
                      server._bundle_cache["sig"], server._bundle_cache["data"],
                      server._sleep_until)
        server.LOOPBACK_HOSTS = ()          # 否则本机请求永远免鉴权，测不到局域网分支
        server._bundle_cache["sig"] = None  # 换令牌后必须重打包，别让缓存跨用例
        server._bundle_cache["data"] = b""
        self.httpd = _Server(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        (server.AUTH_TOKEN, server.LOOPBACK_HOSTS,
         server._bundle_cache["sig"], server._bundle_cache["data"],
         server._sleep_until) = self.saved

    def url(self, path):
        return "http://127.0.0.1:{}{}".format(self.port, path)

    def bundle_members(self, headers=None, query=""):
        code, data, _ = request(self.url("/bundle.tgz") + query, headers=headers)
        self.assertEqual(code, 200, data[:120])
        return tarfile.open(fileobj=io.BytesIO(data)).getnames()

    def has_state_json(self, headers=None, query=""):
        """tar 成员名带目录前缀（atv-remote/state.json），按子串判断"""
        return any(n.endswith("state.json")
                   for n in self.bundle_members(headers=headers, query=query))

    # ---------- 配对凭据 ----------
    def test_bundle_excludes_credentials_without_token(self):
        """未启用令牌 = 局域网里任何设备都能取包，这时包里不能有 state.json"""
        server.AUTH_TOKEN = ""
        names = self.bundle_members()
        self.assertEqual([n for n in names if "state.json" in n], [], names)
        self.assertTrue([n for n in names if n.endswith("server.py")], names)
        self.assertTrue([n for n in names if n.endswith("app.js")], names)

    def test_bundle_keeps_pairing_sync_when_token_enabled(self):
        """启用令牌后：无令牌 401，持令牌仍能拿到 state.json（手机免二次配对）"""
        server.AUTH_TOKEN = TOKEN
        self.assertEqual(request(self.url("/bundle.tgz"))[0], 401)
        self.assertTrue(self.has_state_json(headers={"Cookie": "atv_token=" + TOKEN}))
        self.assertTrue(self.has_state_json(headers={"X-ATV-Token": TOKEN}))
        self.assertTrue(self.has_state_json(query="?token=" + TOKEN))   # 走 ?token= 查询串
        code, _, _ = request(self.url("/bundle.tgz") + "?token=wrong")
        self.assertEqual(code, 401)

    # ---------- /install 的 Host 头 ----------
    def test_install_rejects_unsanitized_host(self):
        """脚本会被用户 curl … | bash 执行，Host 头可伪造，不能原样进脚本"""
        server.AUTH_TOKEN = ""
        evil = "evil.example.com/oops"
        text = request(self.url("/install"), headers={"Host": evil})[1].decode()
        self.assertNotIn(evil, text)
        # 回退到「这次访问实际用到的本机地址」，而不是 lan_ip() 可能挑错的网卡
        self.assertIn("http://127.0.0.1:{}".format(self.port), text)
        good = "backup.example.com:8300"
        self.assertIn(good, request(self.url("/install"), headers={"Host": good})[1].decode())
        self.assertIn("pyatv==", text)  # 版本要与 requirements.txt 对齐，不能漂

    def test_install_appends_port_to_host_without_one(self):
        """合法但不带端口的 Host（反代 / 手写 IPv6 字面量）要补上真实监听端口，
        否则生成的 curl 地址指向 80，手机连不上。"""
        server.AUTH_TOKEN = ""
        for host in ("[::1]", "tunnel.example.invalid"):
            text = request(self.url("/install"), headers={"Host": host})[1].decode()
            self.assertIn("http://{}:{}/".format(host, self.port), text)
            self.assertNotIn("http://{}/".format(host), text)  # 端口缺失 = 手机连不上

    # ---------- 异常 body 与 keep-alive ----------
    def pipeline(self, payload, want=2):
        """同一条连接上灌 payload，再发一个正常请求，看后面的帧还对不对"""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            s.sendall(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass  # 服务端提前关连接正是要的行为
        try:
            s.sendall(b"GET /next HTTP/1.1\r\nHost: x\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        s.settimeout(3)
        buf = b""
        try:
            while buf.count(b"HTTP/1.1 ") < want:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
        except (socket.timeout, ConnectionResetError):
            pass  # 服务端读完就 RST 掉（body 没读干净时才会这样），收尾即可
        s.close()
        return buf

    @staticmethod
    def statuses(buf):
        """状态行按正则取：两个响应之间没有换行分隔，按行切会漏掉后一个"""
        return re.findall(rb"HTTP/1\.1 (\d{3})", buf)

    def test_oversized_body_closes_connection(self):
        """声明超大 body 时不能不读干净就返回：那条长连接上的下个请求会从半截 body 开始解析"""
        buf = self.pipeline(b"POST /api/cmd HTTP/1.1\r\nHost: x\r\n"
                            b"Content-Length: 200000\r\n\r\n" + b"A" * 200000)
        self.assertTrue(buf.startswith(b"HTTP/1.1 400"), buf[:24])
        self.assertIn(b"Connection: close", buf)
        self.assertEqual(buf.count(b"HTTP/1.1 "), 1)

    def test_bad_content_length_is_business_error(self):
        buf = self.pipeline(b"POST /api/cmd HTTP/1.1\r\nHost: x\r\n"
                            b"Content-Length: abc\r\n\r\n")
        self.assertTrue(buf.startswith(b"HTTP/1.1 400"), buf[:24])
        self.assertNotIn(b"Traceback", buf)

    def test_unknown_route_still_drains_body(self):
        """判 404 之前也要读完 body，否则复用这条长连接的下个请求解析不出来"""
        buf = self.pipeline(b"POST /api/nope HTTP/1.1\r\nHost: x\r\n"
                            b"Content-Length: 2\r\n\r\n{}")
        self.assertEqual(self.statuses(buf), [b"404", b"404"], buf[:60])

    def test_token_cookie_is_httponly(self):
        server.AUTH_TOKEN = TOKEN
        _, _, headers = request(self.url("/next"), headers={"X-ATV-Token": TOKEN})
        self.assertIn("HttpOnly", headers.get("Set-Cookie", ""))

    # ---------- PWA 离线壳 ----------
    def test_sw_response_allows_root_scope(self):
        """SW 脚本在 /static/sw.js，默认 scope 锁死在 /static/；缺这个头浏览器
        直接 SecurityError，注册静默失败、离线壳装不上"""
        code, _, headers = request(self.url("/static/sw.js"))
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Service-Worker-Allowed"), "/")

    def test_manifest_served_with_pwa_ctype(self):
        """.webmanifest 在部分平台被 mimetypes 猜成八位组流，manifest 就废了"""
        code, _, headers = request(self.url("/static/manifest.webmanifest"))
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Content-Type"), "application/manifest+json")

    # ---------- 睡眠定时 ----------
    def _timer(self, payload):
        code, body, _ = request(self.url("/api/cmd"), method="POST",
                                data=json.dumps(payload).encode())
        self.assertEqual(code, 200, body[:200])
        return json.loads(body)

    def test_sleep_timer_set_status_cancel(self):
        """定时经 /api/cmd 的 timer 类型设置；剩余秒数与 /api/status 都要可见，取消即清零"""
        server.AUTH_TOKEN = ""
        server._sleep_until = None
        r = self._timer({"type": "timer", "action": "set", "minutes": 30})
        self.assertTrue(r["ok"])
        self.assertTrue(r["sleep_timer"]["active"])
        self.assertTrue(25 * 60 < r["sleep_timer"]["remaining"] <= 30 * 60)
        status = json.loads(request(self.url("/api/status"))[1])
        self.assertTrue(status["sleep_timer"]["active"])
        r = self._timer({"type": "timer", "action": "cancel"})
        self.assertFalse(r["sleep_timer"]["active"])
        self.assertFalse(json.loads(request(self.url("/api/status"))[1])["sleep_timer"]["active"])

    def test_sleep_timer_rejects_bad_minutes(self):
        """分钟数不受控会把定时变成永久占位或立刻触发的意外电源命令，一律 400"""
        server.AUTH_TOKEN = ""
        for bad in (0, -5, 100000, "30", None, True):
            code, body, _ = request(self.url("/api/cmd"), method="POST",
                                    data=json.dumps({"type": "timer",
                                                     "action": "set",
                                                     "minutes": bad}).encode())
            self.assertEqual(code, 400, (json.loads(body)["error"] + str(bad))[:120])


if __name__ == "__main__":
    unittest.main()
