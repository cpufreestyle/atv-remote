#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""正在播放（Now Playing）的解析与路由测试。

dumpsys media_session 没有稳定契约：同一份 ROM 升个号，dump 可能从
`state=PLAYING, pos=61250` 变成 `PlaybackState {state=3, ...}`。这些用例把
「认得出」和「认不出时安静降级」两侧同时钉住——解析崩了遥控器整页挂，
比不显示信息条严重得多。

跑法：python3 -m unittest discover -s tests   （或 ./check.sh）
不依赖 adb / pyatv / 局域网。
"""

import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

import server

# 会话栈 dump（AOSP 风格）：YouTube TV 正在播放
DUMP_PLAYING_AOSP = """
Media session stack (most recent first):
  Record for uid=10123, pid=4567
    packageName=com.google.android.youtube.tv
    state=PLAYING, pos=61250 (ms), buffered position=187000 (ms), speed=1.0
    holder=...
    metadata: {
      title=Rick Astley - Never Gonna Give You Up (Official Video)
      artist=RickAstleyVEVO
      duration=213000
    }
"""

# PlaybackState 大括号风格 + 暂停（Netflix）
DUMP_PAUSED_BRACE = """
Session Record #0
  packageName=com.netflix.ninja
  PlaybackState {state=2, pos=432000, buffered pos=900000, speed=0.0}
  MediaMetadata {
    title=Stranger Things
    artist=Netflix
    duration=3120000
  }
"""

# 屏保 / 空栈：没有任何可展示信息
DUMP_EMPTY = """
Media session stack (most recent first):
  (nothing)
"""

# 多条记录：取栈顶（活跃）会话，别拿后面 paused 的老会话覆盖
DUMP_MULTI = """
  Record for uid=10086
    packageName=com.spotify.tv.android
    state=PLAYING, pos=1000
    title=First Song
  Record for uid=10010
    packageName=com.google.android.youtube.tv
    state=PAUSED, pos=5000
    title=Old Video
"""


class ParseMediaSessionTest(unittest.TestCase):
    def test_aosp_playing(self):
        r = server.parse_media_session(DUMP_PLAYING_AOSP)
        self.assertEqual(r["app"], "com.google.android.youtube.tv")
        self.assertTrue(r["title"].startswith("Rick Astley"))
        self.assertEqual(r["artist"], "RickAstleyVEVO")
        self.assertEqual(r["duration"], 213000)
        self.assertEqual(r["position"], 61250)
        self.assertTrue(r["playing"])
        self.assertFalse(r["empty"])

    def test_brace_paused(self):
        r = server.parse_media_session(DUMP_PAUSED_BRACE)
        self.assertEqual(r["app"], "com.netflix.ninja")
        self.assertFalse(r["playing"])
        self.assertEqual(r["position"], 432000)
        self.assertEqual(r["title"], "Stranger Things")

    def test_empty_dump_degrades(self):
        r = server.parse_media_session(DUMP_EMPTY)
        self.assertTrue(r["empty"])
        self.assertFalse(r["playing"])
        self.assertEqual(r["title"], "")
        self.assertEqual(r["duration"], 0)

    def test_unknown_garbage(self):
        r = server.parse_media_session("boom!!! \x00 \ud83d\ude00")
        self.assertTrue(r["empty"])

    def test_first_record_wins(self):
        r = server.parse_media_session(DUMP_MULTI)
        self.assertEqual(r["app"], "com.spotify.tv.android")
        self.assertEqual(r["title"], "First Song")
        self.assertTrue(r["playing"])


class NowPlayingRouteTest(unittest.TestCase):
    """路由行为：没连电视时给 {connected: False}，前端据此隐藏卡片。"""

    def setUp(self):
        self.saved = server.state
        server.state = {"recent_android": [], "appletvs": [], "current": None}
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.state = self.saved

    def test_not_connected(self):
        from urllib.request import urlopen
        with urlopen("http://127.0.0.1:%d/api/nowplaying" % self.port, timeout=10) as r:
            import json
            self.assertEqual(json.load(r), {"connected": False})


if __name__ == "__main__":
    unittest.main()
