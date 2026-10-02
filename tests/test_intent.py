#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""说话即遥控：自然语言意图层（第三十一轮）回归。

动机：语音识别结果原来直接 sendText——说「声音小一点」会把这五个字打进电视搜索框，
用户的意图根本没被理解；同时命令面板 palScore 只有四档，打错一个字母就 0 命中。
本轮的做法是加一层意图理解：同义词别名优先，其次动词+对象起应用，最后整句直中；
不命中原样当文本下发（搜索词绝不能被误判成命令）。

学习源与实现口径见 static/app.js intent 段首注释（Fuse.js 7.1.0 的 Bitap 错误预算
与近似子串匹配、rapidfuzz 3.14.3 的 ratio/partial_ratio 组合），此处不复述。

这里只验六件事，全部离线可跑（标准库 + 读仓库文件 + 跑 node harness）：
  1) 规则段（intent:begin/end）可搬进 node：归一化 / 别名 / 容错 / 取对象 /
     不误判 / 面板计分有真行为证据（tests/intent_harness.js，15 组用例）；
  2) 安全底线钉在测试里：没听懂与关掉开关，两条出口都必须是 sendText 原样下发；
  3) 命令 id 与 palCommands() 的 push id 同源——别名表里出现的每个 id 必须真的
     被 push 过，否则语音听懂了一个不存在的命令，点了没反应；
  4) 偏好只落 localStorage（atv.intent），不进 state.json（敏感文件）；
  5) 意图层可关，且执行前先给反馈（误识别时用户立刻知道按了什么）；
  6) 渲染不用 innerHTML（设备名 / IP 禁令的延伸）。
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "intent_harness.js"

INTENT_BEGIN = "/* ===== intent:begin"
INTENT_END = "/* ===== intent:end"
INTENT_KEY = "atv.intent"
MIN_HARNESS_CASES = 15


def _strip_comments(text):
    return re.sub(r"/\*[\s\S]*?\*/", " ", re.sub(r"//[^\n]*", " ", text))


class PureSegmentTest(unittest.TestCase):
    """规则段：标记唯一、可搬进 node、没碰 DOM / 网络 / 敏感文件。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.begin = cls.js.index(INTENT_BEGIN)
        cls.end = cls.js.index(INTENT_END)
        assert cls.end > cls.begin, "intent 段标记顺序错误"

    def test_segment_markers(self):
        self.assertEqual(self.js.count(INTENT_BEGIN), 1, "begin 标记必须唯一")
        self.assertEqual(self.js.count(INTENT_END), 1, "end 标记必须唯一")

    def test_no_dom_or_storage_in_pure_segment(self):
        seg = _strip_comments(self.js[self.begin:self.end])
        for banned in ["document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("]:
            self.assertNotIn(banned, seg, "纯函数段里出现禁用引用 " + banned)

    def test_harness_is_green_and_never_shorter(self):
        if shutil.which("node") is None:
            self.skipTest("环境没有 node")
        r = subprocess.run(["node", str(HARNESS)], capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ALL_INTENT_CASES_PASSED", r.stdout)
        total = json.loads(r.stdout.strip().splitlines()[-2])["total"]
        self.assertGreaterEqual(total, MIN_HARNESS_CASES, "intent harness 用例数减少了")


class SafetyFloorTest(unittest.TestCase):
    """安全底线：听不懂就原样下发，绝不猜。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        seg = cls.js[cls.js.index("function intentSpeak"):]
        cls.glue = seg[:seg.index("\n}")]

    def test_unmatched_text_falls_through_to_sendtext(self):
        # 两条出口都必须是原样 sendText：没听懂，以及用户在设置里关掉了意图层
        self.assertEqual(self.js.count("return sendText(text, false);"), 2,
                         "意图未命中/已关闭都必须原样 sendText")
        self.assertIn("if (!hit) return sendText(text, false);", self.glue,
                      "没听懂必须原样下发，不能悄悄吞掉")
        self.assertIn("if (!intentEnabled()) return sendText(text, false);", self.glue,
                      "关掉开关后行为要与加功能之前一致")

    def test_feedback_before_execute(self):
        self.assertLess(self.glue.index("toast("), self.glue.index("hit.cmd.run()"),
                        "必须先给反馈再执行")

    def test_intent_errors_never_break_voice(self):
        self.assertIn("try { hit = intentParse(text, palCommands()); } catch (err) { hit = null; }",
                      self.glue, "意图层抛异常必须退化成原样下发")

    def test_unmatched_phrases_are_explicitly_covered(self):
        # harness 里必须真的断言过这些搜索词/闲聊返回 null
        src = HARNESS.read_text(encoding="utf-8")
        for phrase in ("周杰伦", "看电视", "打开电视机顶盒"):
            self.assertIn('intentParse("%s", CMDS) === null' % phrase, src,
                          "安全底线缺少用例：" + phrase)


class CommandIdConsistencyTest(unittest.TestCase):
    """别名表里的 id 必须真的存在于 palCommands()——否则语音听懂个不存在的命令。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        seg = cls.js[cls.js.index(INTENT_BEGIN):cls.js.index(INTENT_END)]
        cls.alias_ids = set(re.findall(r'\["[^"]+", "([^"]+)"\]', seg))
        pal = cls.js[cls.js.index("function palCommands()"):]
        pal = pal[:pal.index("\n}\n") + 2]
        cls.pushed_ids = set(re.findall(r'push\("([^"]+)"', pal))
        # 按键类 id 是动态拼的：push("key:" + code, ...)，code 来自 PAL_KEYS
        km = cls.js[cls.js.index("const PAL_KEYS = "):]
        km = km[:km.index("];")]
        cls.pushed_ids |= {"key:" + c for c in re.findall(r"\[(\d+),", km)}

    def test_alias_ids_exist_in_palette(self):
        missing = sorted(self.alias_ids - self.pushed_ids)
        self.assertEqual(missing, [], "别名表里有命令 id 不存在于 palCommands()：" + ", ".join(missing))

    def test_shot_command_is_reachable(self):
        self.assertIn("shot", self.pushed_ids, "palCommands 里必须有 shot（截图）")


class PreferenceStorageTest(unittest.TestCase):
    """偏好只进 localStorage；state.json 绝不被意图层碰。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_pref_key_is_local_only(self):
        self.assertIn(INTENT_KEY, self.js)
        glue = self.js[self.js.index("function intentSpeak"):]
        glue = glue[:glue.index("\n}")]
        for path in ("state.json", "save_state(", "ATV_STATE"):
            self.assertNotIn(path, glue, "意图层胶水不应触碰 " + path)

    def test_toggle_defaults_on(self):
        # 默认开：原来语音只能当字面文本，这一层是修 bug 不是加负担
        self.assertIn('function intentEnabled() { return localStorage.getItem(INTENT_KEY) !== "0"; }',
                      self.js)


class MarkupContractTest(unittest.TestCase):
    """设置弹窗里的开关：ARIA 契约与 CSS 驱动一致（与 hapticBtn 同一套）。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_switch_markup(self):
        for frag in ('id="intentBtn"', 'class="switch"', 'role="switch"',
                     'aria-checked="true"', 'aria-label="语音意图识别"'):
            self.assertIn(frag, self.html)

    def test_no_innerhtml_for_intent_glue(self):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        seg = js[js.index("function intentSpeak"):]
        seg = seg[:seg.index("\n}")]
        self.assertNotIn("innerHTML", seg)


if __name__ == "__main__":
    unittest.main(verbosity=2)

