#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KEY_MAP 的 pyatv 能力过滤回归。

动机：Android/chaquo 因 cryptography / zeroconf 的 Android wheel 天花板只能锁
pyatv 0.13.0，而 macOS/.venv 是 0.18.x。0.13.0 的 RemoteControl 没有 guide /
control_center，映射里若照留这两个键，用户按到就 getattr(iface, method) 抛
AttributeError——它不在 PYATV_ERRORS 里，直接变 500。atv_backend 用
_filter_key_map 在定义时按已装 pyatv 裁剪，本测试锁住这个行为：

  1) 过滤函数按能力裁剪：capable 拒绝的方法连同键码一起消失；
  2) MRP 的 82 是 home（不是 control_center），老版本也该留，只有 guide 该裁；
  3) 无 pyatv 时恒真、全保留（历史降级行为不变）；
  4) 映射里出现未登记接口名时保守保留，不误裁；
  5) 真装了 pyatv 时，两条 KEY_MAP 里每个方法都存在于对应接口（不漏会 500 的键），
     且能力足够的版本不该被误裁 guide / control_center。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import atv_backend as backend


class FilterKeyMapTest(unittest.TestCase):
    """_filter_key_map 的能力裁剪语义（stub 接口，离线可跑）。"""

    def setUp(self):
        self._orig = backend._PYATV_IFACE

    def tearDown(self):
        backend._PYATV_IFACE = self._orig

    def test_drops_methods_missing_from_iface(self):
        # 造一个没有 guide / control_center 的 RemoteControl（模拟 pyatv 0.13.0）
        class RC:
            up = down = left = right = select = menu = home = 1
            top_menu = play_pause = skip_forward = skip_backward = 1
            volume_up = volume_down = screensaver = 1

        class Audio:
            volume_up = volume_down = 1

        class Power:
            turn_on = turn_off = 1

        backend._PYATV_IFACE = {"remote_control": RC, "audio": Audio, "power": Power}
        mapping = {
            19: ("remote_control", "up"),
            165: ("remote_control", "guide"),          # 0.13.0 没有 → 裁掉
            82: ("remote_control", "control_center"),  # 0.13.0 没有 → 裁掉
            24: ("audio", "volume_up"),                # 在 → 保留
            26: ("power", "turn_off"),                 # 在 → 保留
        }
        filtered = backend._filter_key_map(mapping)
        self.assertEqual(sorted(filtered), [19, 24, 26])
        self.assertNotIn(165, filtered)
        self.assertNotIn(82, filtered)

    def test_mrp_keeps_home_but_drops_guide_on_old_pyatv(self):
        # MRP 的 82 映射到 home（存在），只有 165 guide 该被裁——别把 home 也误伤
        class RC:
            home = up = 1
        backend._PYATV_IFACE = {"remote_control": RC, "audio": RC, "power": RC}
        filtered = backend._filter_key_map({
            82: ("remote_control", "home"),
            165: ("remote_control", "guide"),
        })
        self.assertIn(82, filtered)
        self.assertNotIn(165, filtered)

    def test_no_pyatv_keeps_everything(self):
        # 无 pyatv：_PYATV_IFACE 为空，_iface_capable 恒真，映射原样保留
        backend._PYATV_IFACE = {}
        mapping = {165: ("remote_control", "guide"),
                   82: ("remote_control", "control_center")}
        self.assertEqual(backend._filter_key_map(mapping), mapping)

    def test_unknown_iface_kept(self):
        # 映射里出现 _PYATV_IFACE 没登记的接口名时保守保留，不误裁
        backend._PYATV_IFACE = {"remote_control": type("RC", (), {"up": 1})}
        mapping = {1: ("some_future_iface", "whatever")}
        self.assertEqual(backend._filter_key_map(mapping), mapping)


@unittest.skipUnless(backend.PYATV_AVAILABLE, "pyatv 未安装，跳过真映射校验")
class RealKeyMapTest(unittest.TestCase):
    """真装了 pyatv 时校验模块级 KEY_MAP 与已装版本自洽。"""

    def test_every_mapped_method_exists_on_iface(self):
        # 过滤后不该残留任何当前 pyatv 不存在的方法（否则按到就 500）
        for name in ("KEY_MAP_MRP", "KEY_MAP_COMPANION"):
            mapping = getattr(backend, name)
            for code, (iface_name, method) in mapping.items():
                self.assertTrue(
                    backend._iface_capable(iface_name, method),
                    "{}[{}]=({}, {}) 在当前 pyatv 不存在，按到会 500".format(
                        name, code, iface_name, method))

    def test_capable_version_keeps_guide_and_control_center(self):
        # 能力足够的版本（0.15+ 有 guide/control_center）不该被误裁。
        # 用当前已装 pyatv 的真实能力判断，不把测试钉死在某一个版本上。
        if backend._iface_capable("remote_control", "guide"):
            self.assertIn(165, backend.KEY_MAP_MRP)
            self.assertIn(165, backend.KEY_MAP_COMPANION)
        if backend._iface_capable("remote_control", "control_center"):
            self.assertIn(82, backend.KEY_MAP_COMPANION)


if __name__ == "__main__":
    unittest.main()
