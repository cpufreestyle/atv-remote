#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""音量读取（/api/volume）的解析与路由测试。

和 media_session 一个道理：读音量的两条 adb 命令（media volume / dumpsys audio）
都没有稳定契约，ROM 换个版本输出就变形。这些用例把「认得出」和「认不出时安静
降级」两侧同时钉住——解析崩了整页遥控器挂，比不显示格数严重得多。

跑法：python3 -m unittest discover -s tests   （或 ./check.sh）
不依赖真 adb / 局域网：adb.shell 用假件替身，路由测试走本机回环。
"""

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

import server

# media volume --stream 3 --get 的标准输出
MV_OUTPUT = "volume is 7 in range [0..15]\n"

# dumpsys audio：AOSP 多流堆叠，只认 STREAM_MUSIC（别拿 ALARM 的 9 冒充媒体音量）
DUMP_AUDIO = """
Audio stack stats:
  - STREAM_VOICE_CALL:
      Mute count: 0
      Current: 3, Latest: 3
  - STREAM_MUSIC:
      Mute count: 0
      Max: 15
      Current: 7, Latest: 7
  - STREAM_ALARM:
      Current: 9
"""

# 老 ROM：无 Max、Mute count 非零（静音）
DUMP_AUDIO_OLD = """
- STREAM_MUSIC:
   Mute count: 2
   Current: 4
"""


class FakeAdb:
    """按命令文本回包的 adb 替身：media volume 走 _media，其余回 dumpsys audio。"""

    def __init__(self, media=None, audio=None):
        self.calls = []
        self._media = media
        self._audio = audio

    def shell(self, serial, cmd, timeout=5):
        self.calls.append(cmd)
        if "media volume" in cmd:
            if self._media is None:
                raise server.AdbError("/system/bin/sh: media: not found")
            return self._media
        if self._audio is None:
            raise server.AdbError("device offline")
        return self._audio


class ParseVolumeTest(unittest.TestCase):
    def test_media_volume_parses(self):
        self.assertEqual(server.parse_media_volume(MV_OUTPUT), {"level": 7, "max": 15})

    def test_media_volume_garbage(self):
        # 老系统没有 media 命令：sh 回一行 not found，必须安静降级
        self.assertEqual(server.parse_media_volume("/system/bin/sh: media: not found"), {})

    def test_dumpsys_music_block_wins(self):
        r = server.parse_dumpsys_audio(DUMP_AUDIO)
        self.assertEqual(r["level"], 7)       # 不是 ALARM 的 9
        self.assertEqual(r["max"], 15)
        self.assertNotIn("muted", r)          # Mute count: 0 = 没提，归到「未知」

    def test_dumpsys_old_rom_mute_count(self):
        r = server.parse_dumpsys_audio(DUMP_AUDIO_OLD)
        self.assertEqual(r["level"], 4)
        self.assertTrue(r["muted"])
        self.assertNotIn("max", r)

    def test_dumpsys_no_music_stream(self):
        self.assertEqual(server.parse_dumpsys_audio("garbage \x00"), {})


class VolumeQueryTest(unittest.TestCase):
    def setUp(self):
        self.saved = server.adb
        self.saved_cache = dict(server._volume_cache)
        server._volume_cache.update(ts=0.0, serial=None, val=None)

    def tearDown(self):
        server.adb = self.saved
        server._volume_cache.update(**self.saved_cache)

    def test_media_first_dumpsys_fallback(self):
        server.adb = FakeAdb(media=MV_OUTPUT, audio=DUMP_AUDIO)
        v = server.volume("1.2.3.4")
        self.assertTrue(v["supported"])
        self.assertEqual(v["level"], 7)
        # media 命令不报静音位：muted=None（「不知道」），前端才不会被真值抹掉静音反馈
        self.assertIsNone(v["muted"])
        # 新系统的 media 命令成功就不该再跑 dumpsys（省一次 adb）
        self.assertEqual(len(server.adb.calls), 1)

    def test_dumpsys_fallback_when_media_missing(self):
        server.adb = FakeAdb(media=None, audio=DUMP_AUDIO)
        v = server.volume("1.2.3.4")
        self.assertTrue(v["supported"])
        self.assertEqual(v["level"], 7)
        self.assertEqual(len(server.adb.calls), 2)

    def test_neither_channel_degrades(self):
        server.adb = FakeAdb()
        v = server.volume("1.2.3.4")
        self.assertFalse(v["supported"])
        self.assertEqual(v["level"], -1)      # 前端据此退图标模式，不出格数
        self.assertIn("error", v)

    def test_ttl_cache_shares_query(self):
        server.adb = FakeAdb(media=MV_OUTPUT)
        v1 = server.volume("1.2.3.4")
        v2 = server.volume("1.2.3.4")
        self.assertEqual(v1, v2)
        self.assertEqual(len(server.adb.calls), 1)

    def test_new_serial_requeries(self):
        server.adb = FakeAdb(media=MV_OUTPUT)
        server.volume("1.2.3.4")
        server.volume("5.6.7.8")
        self.assertEqual(len(server.adb.calls), 2)

    def test_dumpsys_mute_state_reaches_frontend(self):
        # dumpsys 通道读得到静音（Mute count 非零）→ 布尔值，前端可以照信
        server.adb = FakeAdb(media=None, audio=DUMP_AUDIO_OLD)
        v = server.volume("1.2.3.4")
        self.assertTrue(v["supported"])
        self.assertIs(v["muted"], True)
        self.assertEqual(v["level"], 4)


class VolumeRouteTest(unittest.TestCase):
    def setUp(self):
        self.saved_state = server.state
        self.saved_adb = server.adb
        self.saved_cache = dict(server._volume_cache)
        server.state = {"recent_android": [], "appletvs": [], "current": None}
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        server.state = self.saved_state
        server.adb = self.saved_adb
        server._volume_cache.update(**self.saved_cache)

    def _get(self):
        with urlopen("http://127.0.0.1:%d/api/volume" % self.port, timeout=10) as r:
            return json.load(r)

    def test_not_connected(self):
        self.assertEqual(self._get(), {"connected": False})

    def test_appletv_unsupported(self):
        server.state["current"] = {"type": "appletv", "id": "aa:bb"}
        self.assertEqual(self._get(), {"connected": False})

    def test_android_connected(self):
        server.state["current"] = {"type": "android", "target": "1.2.3.4:5555"}
        server.adb = FakeAdb(media=MV_OUTPUT)
        v = self._get()
        self.assertTrue(v["connected"])
        self.assertTrue(v["supported"])
        self.assertEqual(v["level"], 7)
        self.assertIsNone(v["muted"])         # media 通道：静音状态「未知」


if __name__ == "__main__":
    unittest.main()
