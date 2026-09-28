#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「点了下载 → 无法下载」的三条回归。

模拟器实测复现出来的，三个原因叠在一起，缺一个都还是下不动：

1. **服务器没实现 HEAD** → 回 `501 Unsupported method`。下载管理器与「点链接后先探测」
   的浏览器都会先发 HEAD，看到 501 就放弃了；而直接在地址栏敲 URL（GET）却是好的，
   所以极难查。现在 `do_HEAD` 走同一条 `_serve()`，只回头不回体。
2. **没有 `Content-Disposition`** → 浏览器按 URL 最后一段命名（`app.apk`），
   下载管理器也不把它当安装包。现在明确 `attachment; filename="ATVRemote.apk"`。
3. **Service Worker 把下载搞坏了**：通用分支里 `cache.put()` 没被 await，
   它 reject 时被 `.catch(() => hit)` 接住 —— 于是「缓存写失败」被当成「网络失败」，
   在无缓存命中时 `respondWith(undefined)`，浏览器直接报下载失败。20MB 的 APK 写
   Cache Storage 很容易踩到配额。现在下载类请求直接放行，且缓存写入永不吞响应。

只读文件 + 起一个真端口打 HTTP（不碰 adb / pyatv）。
"""

import http.client
import socket
import threading
import unittest
from pathlib import Path

import server

ROOT = Path(__file__).resolve().parent.parent
SW = ROOT / "static" / "sw.js"


class _Server:
    """起一个真端口的最小服务（用生产 Handler），测完关掉。"""

    def __init__(self):
        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class HeadSupportTest(unittest.TestCase):
    """下载管理器与浏览器都会先发 HEAD。"""

    @classmethod
    def setUpClass(cls):
        cls.srv = _Server()

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def _request(self, method, path):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=10)
        try:
            c.request(method, path)
            r = c.getresponse()
            body = r.read()
            return r.status, dict(r.getheaders()), body
        finally:
            c.close()

    def test_head_is_supported(self):
        for path in ("/", "/app.apk", "/static/app.js", "/api/status"):
            code, _, _ = self._request("HEAD", path)
            self.assertNotEqual(code, 501,
                                "HEAD %s 回 501：下载管理器会直接放弃" % path)
            self.assertEqual(code, 200, "HEAD " + path)

    def test_head_sends_headers_but_no_body(self):
        code, hdrs, body = self._request("HEAD", "/app.apk")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"", "HEAD 不能带 body")
        self.assertEqual(int(hdrs["Content-Length"]),
                         (ROOT / "ATVRemote-native.apk").stat().st_size,
                         "Content-Length 要是「本来会发的字节数」")

    def test_get_still_returns_body(self):
        code, hdrs, body = self._request("GET", "/app.apk")
        self.assertEqual(code, 200)
        self.assertEqual(len(body), int(hdrs["Content-Length"]))
        self.assertGreater(len(body), 1 << 20)


class DownloadHeadersTest(unittest.TestCase):
    """APK 响应要带 Content-Disposition，浏览器才会当「安装包」存盘。"""

    @classmethod
    def setUpClass(cls):
        cls.srv = _Server()
        c = http.client.HTTPConnection("127.0.0.1", cls.srv.port, timeout=10)
        c.request("GET", "/app.apk")
        cls.res = c.getresponse()
        cls.hdrs = {k.lower(): v for k, v in cls.res.getheaders()}
        cls.res.read()
        c.close()

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def test_content_type(self):
        self.assertEqual(self.hdrs.get("content-type"),
                         "application/vnd.android.package-archive")

    def test_content_disposition_attachment(self):
        cd = self.hdrs.get("content-disposition", "")
        self.assertIn("attachment", cd)
        self.assertIn('filename="ATVRemote.apk"', cd)


class ServiceWorkerDownloadTest(unittest.TestCase):
    """SW 绝不能因为「缓存写失败」把下载响应弄丢。"""

    @classmethod
    def setUpClass(cls):
        cls.sw = SW.read_text(encoding="utf-8")

    def test_download_requests_bypass_sw(self):
        self.assertRegex(self.sw, r"\.\(apk\|tgz\|zip\)",
                         "下载类请求要在进缓存分支之前放行")

    def test_cache_put_is_not_awaited_into_the_response(self):
        # 旧写法 cache.put(...) 的 promise 直接进 then 链，reject 会被后面的
        # .catch(() => hit) 接住，于是「写缓存失败」伪装成「网络失败」
        self.assertNotIn("cache.put(req, res.clone());", self.sw,
                         "cache.put 必须自带 catch，不能让它影响返回值")
        self.assertIn("cache.put(req, res.clone()).catch(() => {})", self.sw)

    def test_stale_hit_still_returns_network_on_miss(self):
        self.assertIn("if (hit) {", self.sw)
        self.assertIn("return net;", self.sw)

    def test_cache_version_bumped(self):
        # 改了 fetch 逻辑就必须换缓存名，否则老客户端还在跑旧 SW
        self.assertIn('CACHE = "atv-shell-v5"', self.sw)


if __name__ == "__main__":
    unittest.main()
