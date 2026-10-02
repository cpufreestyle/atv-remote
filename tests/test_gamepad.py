"""手柄当 D-pad（第三十五轮）回归：纯函数段契约 / 出处可 grep / node harness 真行为。

立论是「安卓的 D-pad 没有斜向，而 adb shell input 只认离散按键」：所以手柄那两根模拟轴
必须在浏览器侧就翻成方向键，不能指望设备端。翻的每一档都能在参照项目里找到根：

  · 死区与重标度、50ms/600ms 两个截止 —— xf86-input-joystick 1.6.4
    （src/jstk_axis.c、src/backend_joystick.c，Ubuntu pool 源码包已下载核对）
  · 「没变就不发键」的边沿状态机、以及轴跳变后卡住不松键的坑 —— qjoypad 4.3.1
    （src/axis.cpp:203-236，Debian 源码包已下载核对）

第三十六轮 UI 接线已落地（index.html 手柄卡 + Gamepad API 轮询 + /api/cmd），所以本轮
补上按钮与胶水存在的断言：规则段仍只在段内，DOM 与网络一律在段外，两边都守住。
行号出处在 docs/opensource-references.md：第 10 节（第三十五轮，xorg / qjoypad）与
第 11 节（第三十六轮，xboxdrv 0.8.8）。

只用标准库 + 读仓库文件 + 跑 node harness：不起端口、不碰真 adb、不联网。
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "gamepad_harness.js"
DOCS = ROOT / "docs" / "opensource-references.md"

# 纯函数段禁用词与 node harness 同一套，外加本仓库自己的 api() 封装、XHR 与事件轮询。
# 不含 "/api/"——注释里本来就要点名接口，禁它只会逼人把注释写含糊。
BANNED = ("document.", "localStorage", "fetch(", "XMLHttpRequest", "setInterval",
          "setTimeout", "innerHTML", "$(", "navigator.", "api(")

HELPERS = ("function gpNum(", "function gpClampDeadzone(", "function gpResidual(",
           "function gpDirOf(", "function gpStep(", "function gpAxisEvents(",
           "function gpArbitrate(", "function gpPwmCycle(", "function gpRepeatMs(",
           # 第三十六轮：一帧 -> 该发哪些键（xboxdrv 口径）
           "function gpNormPad(", "function gpBtnDir(", "function gpDirKey(",
           "function gpFrame(", "function gpNextFireMs(")

# 段首注释里点名的出处：每一条都要能在 docs/opensource-references.md 里 grep 到。
# 前六条在第 10 节（第三十五轮，xorg / qjoypad），后五条在第 11 节（第三十六轮，xboxdrv）。
SOURCES = ("jstk_axis.c:83", "jstk_axis.c:411-416", "backend_joystick.c:172",
           "axis.cpp:205-233", "jstk_axis.c:494-505", "jstk_axis.c:518-551",
           "uinput_options.cpp:189-198", "four_way_restrictor_modifier.cpp:44-59",
           "autofire_button_filter.cpp:27-28", "autofire_button_filter.cpp:82-97",
           "controller_slot_config.cpp:207-212")


class SegmentContractTest(unittest.TestCase):
    """前端契约：段边界、禁用词、函数都在，常量按参照钉住。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def seg(self):
        s = self.js.index("/* ===== gamepad:begin")
        e = self.js.index("/* ===== gamepad:end")
        self.assertLess(s, e, "段标记顺序不对")
        return self.js[s:e]

    def test_markers_are_unique(self):
        # 抽段靠 indexOf 取第一次，出现两次就会静默抽错段
        for marker in ("/* ===== gamepad:begin", "/* ===== gamepad:end"):
            self.assertEqual(1, self.js.count(marker), marker)

    def test_pure_segment_touches_no_dom_or_network(self):
        seg = self.seg()
        for banned in BANNED:
            self.assertNotIn(banned, seg)

    def test_pure_segment_has_all_helpers(self):
        seg = self.seg()
        for frag in HELPERS:
            self.assertIn(frag, seg)
        # 抽段执行要求段内自洽：不能出现赋值给段外同名量的写法
        self.assertNotIn("gpResidual = function", seg)

    def test_constants_trace_back_to_the_driver(self):
        seg = self.seg()
        # 默认死区 0.15 约等于 xorg 5000/32768 = 0.1526；两相下限与截止照驱动
        self.assertIn("const GP_DEADZONE_DEFAULT = 0.15;", seg)
        self.assertIn("const GP_PWM_MIN_PHASE_MS = 50;", seg)
        self.assertIn("const GP_PWM_HOLD_MS = 600;", seg)
        # 自己的取值必须单独标出来，不能混在参照常量里
        self.assertIn("const GP_REPEAT_MIN_MS = 60;", seg)
        self.assertIn("const GP_ARBIT_MARGIN = 1.25;", seg)

    def test_deadzone_boundary_matches_the_drivers_comparator(self):
        # 驱动判死区用小于号（backend_joystick.c:172 / axis.cpp:281），所以等于阈值算顶出
        seg = self.seg()
        self.assertIn("if (x >= d) return 1;", seg)
        self.assertIn("if (x <= -d) return -1;", seg)
        self.assertIn("if (a < d) return 0;", seg)

    def test_jump_over_releases_before_pressing(self):
        seg = self.seg()
        self.assertIn("if (held !== 0) out.push({ i: i, dir: held, press: false });", seg)
        self.assertIn("if (cur !== 0) out.push({ i: i, dir: cur, press: true });", seg)

    def test_button_wiring_is_landed(self):
        # 第三十六轮起接线落地：手柄卡、开关、死区滑块、状态行都要在 html 里
        for frag in ('id="gpCard"', 'id="gpEnableBtn"', 'id="gpDz"',
                     'id="gpDzVal"', 'id="gpDzReset"', 'id="gpState"'):
            self.assertIn(frag, self.html, frag)
        # 默认关：不开手柄就不占 80ms 轮询
        self.assertIn('aria-checked="false"', self.html)
        # 手柄 id 不可信，一律 textContent 渲染
        self.assertNotIn('innerHTML', self.html)


    def test_round_36_constants_are_pinned(self):
        seg = self.seg()
        # 首拍延时与方向键表都是 xboxdrv 口径的落点，写死数字
        self.assertIn("const GP_REPEAT_FIRST_MS = 300;", seg)
        self.assertIn("const GP_DIR_KEYS = { up: 19, down: 20, left: 21, right: 22 };", seg)
        # 消斜的单一出口：十字键胜出就整帧让位，轴直接给 invalid
        self.assertIn("if (btn !== null) return { dir: btn, axis: null, val: 0 };", seg)
        self.assertIn('if (u || d) return u ? "up" : "down";', seg)
        # 死区内一个键都不发——默认就开连发时这是唯一的安全带
        self.assertIn("if (!(r > 0)) r = GP_REPEAT_MIN_MS;", seg)

    def test_dom_glue_lives_outside_the_pure_segment(self):
        # 胶水（轮询 / localStorage / textContent / 发键）必须在段外，否则 node harness
        # 也会被 DOM 拖着跑不起来。段内出现这些词就是回归。
        seg = self.seg()
        for frag in ("navigator.getGamepads", "localStorage", "textContent",
                     "addEventListener", "sendKey", "pageVisible"):
            self.assertIn(frag, self.js, frag + " 胶水不见了")
            self.assertNotIn(frag, seg, frag + " 漏进了纯函数段")

    def test_tick_interval_is_its_own_80ms(self):
        # 连发节奏经不起 8s 的状态轮询，必须单独一拍
        self.assertIn("const GP_TICK_MS = 80;", self.js)
        self.assertIn("setInterval(gpTick, GP_TICK_MS);", self.js)

class SourceTraceTest(unittest.TestCase):
    """出处可 grep：段首注释点名的 file:line 必须能在台账里找到。"""

    @classmethod
    def setUpClass(cls):
        cls.doc = DOCS.read_text(encoding="utf-8")
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_doc_has_round_35_section(self):
        head = self.doc.split(chr(10))[0]
        self.assertIn("开源项目参考台账", head)
        self.assertIn("第 10 节", self.doc)
        self.assertIn("第三十五轮", self.doc)

    def test_every_cited_line_is_reachable_in_the_doc(self):
        for ref in SOURCES:
            self.assertIn(ref, self.doc, ref)

    def test_doc_has_round_36_section(self):
        # 第三十六轮的口径全部落在第 11 节，别和第 10 节混在一起
        self.assertIn("第 11 节", self.doc)
        self.assertIn("第三十六轮", self.doc)
        self.assertIn("xboxdrv", self.doc)
        # 反面结论也要在册：xboxdrv 默认什么都不挂，别照抄
        self.assertIn("autofire_map", self.doc)

    def test_doc_states_the_negative_result_about_scrcpy(self):
        # 「参照不做客户端死区」这条负面结论必须留在台账里，否则下轮又去挖一遍
        self.assertIn("scrcpy", self.doc)
        self.assertIn("死区", self.doc)

    def test_segment_header_points_at_the_doc(self):
        s = self.js.index("/* ===== gamepad:begin")
        e = self.js.index("/* ===== gamepad:end")
        self.assertIn("opensource-references.md", self.js[s:e])


class HarnessBehaviorTest(unittest.TestCase):
    """真行为：node harness 从 app.js 抽出纯函数段原样执行，必须全绿。"""

    def test_harness_passes(self):
        if shutil.which("node") is None:
            self.skipTest("没有 node，跳过 harness")
        p = subprocess.run(["node", str(HARNESS)], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ALL_GAMEPAD_CASES_PASSED", p.stdout)
        failed = re.findall(r'"failed"[^0-9]*([0-9]+)', p.stdout)
        total = re.findall(r'"total"[^0-9]*([0-9]+)', p.stdout)
        self.assertTrue(failed and failed[0] == "0", p.stdout)
        self.assertTrue(total and int(total[0]) >= 22, p.stdout)

    def test_harness_refuses_to_extract_a_drifted_segment(self):
        # 段标记被改坏时 harness 必须以非 0 退出，而不是安静地跑 0 个用例
        if shutil.which("node") is None:
            self.skipTest("没有 node，跳过 harness")
        original = HARNESS.read_text(encoding="utf-8")
        broken = original.replace("const BEGIN = '/* ===== gamepad:begin';",
                                  "const BEGIN = '/* ===== nope:begin';")
        self.assertNotEqual(original, broken, "补丁没生效")
        tmp = HARNESS.with_name("gamepad_harness_broken.js")
        try:
            tmp.write_text(broken, encoding="utf-8")
            p = subprocess.run(["node", str(tmp)], cwd=str(ROOT),
                               capture_output=True, text=True, timeout=60)
            self.assertNotEqual(0, p.returncode)
            self.assertIn("段标记", p.stdout + p.stderr)
        finally:
            tmp.unlink()


if __name__ == "__main__":
    unittest.main()
