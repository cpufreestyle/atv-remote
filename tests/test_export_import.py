#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置搬家（换机 / 换浏览器把设置一次带走，第三十七轮）契约测试。

立论：换机不是高频操作，但真发生的那次如果配置搬不过去、或者只搬过去半份，比不搬更
糟——半份配置（主题换了、手柄开关没换）很难查。所以本功能的验收不只看「能导出能
导入」，还要看「坏档一个字都不写」与「账目说得清」。三条口径来自 chezmoi v2.73.0，
源码引用与行号见 docs/opensource-references.md 第 12 节：

1) 坏档在写任何东西之前就失败：internal/cmd/config.go:998-1035 的读取链
   （decodeConfigFile → decodeConfigContents → decodeConfigMap）任一步失败即 return，
   语义互斥校验（config.go:1028-1033）排在最后也照样是「写之前」。
2) 写入只认「完整的一份」：internal/chezmoi/realsystem_unix.go:68-104 经 renameio
   写同目录临时文件（:89 tempDir、:96 defer Cleanup、:103 CloseAtomicallyReplace），
   读者只见到旧或新的完整文件。
3) 账目化：sourcestate.go:917-926 把每个 ignore 命中记进 s.ignoredRelPaths，
   ignoredcmd.go:18/37 的 ignored 命令原样打印。

本文件钉段边界、白名单、敏感副闸、HTML 接线与台账出处；真行为证据在
tests/backup_harness.js（node 直接抽 cfgxfer 段执行，21 组用例），这里以子进程调它。
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "backup_harness.js"
DOCS = ROOT / "docs" / "opensource-references.md"
UI_STUDY = ROOT / "docs" / "ui-study.md"

BEGIN = "/* ===== cfgxfer:begin"
END = "/* ===== cfgxfer:end"
INTENT_BEGIN = "/* ===== intent:begin"

# 与 harness 同套：纯函数段禁全局态。注意段首注释里点名了这些词（给人看的说明），
# 所以一切检查都必须先剥注释。
BANNED = ["document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$(",
          "XMLHttpRequest"]

HELPERS = ["CFG_MAGIC", "CFG_VER", "CFG_EXPORTABLE", "CFG_LABELS",
           "CFG_SENSITIVE_RE", "cfgLabelOf", "cfgIsExportable", "cfgPlanExport",
           "cfgBuildBackup", "cfgParseBackup", "cfgApplyBackup"]

GLUE = ["cfgStatus", "cfgExport", "cfgReloadUI", "cfgImportText",
        'addEventListener("click", () => { cfgExport(); buzz(12); });',
        "new FileReader()"]

# 白名单钉死：可搬家的全部配置正好这 13 项
WHITELIST = ["atv.theme", "atv.haptics", "atv.intent", "atv.gamepad", "atv.gamepadDz",
              "atv.padSens", "atv.favApps", "atv.phrases", "atv.collapsed.v1",
              "atv.palRecent", "atv.recentApps", "atv.keymap_v1", "atv_macros_v1"]

# 明确不导：推导数据 / 一次性状态 / 可能夹带隐私文本
NON_EXPORTABLE = ["atv.kbhist", "atv.notif.history", "atv.notif.seen",
                  "atv.coached.pre", "atv.coached.post", "atv.privacy"]

# 台账第 12 节必须能 grep 到的出处（引错行比不引更糟，逐条 sed 复核过）
SOURCES = ["config.go:998-1035", "config.go:1028-1033", "realsystem_unix.go:68-104",
           "realsystem_unix.go:89", "realsystem_unix.go:96", "realsystem_unix.go:103",
           "sourcestate.go:917-926", "ignoredcmd.go:18", "ignoredcmd.go:37",
           "format.go:180", "DisallowUnknownField", "CloseAtomicallyReplace",
           "Copyright (c) 2018 Tom Payne",
           "c99c43dd1724f4cea26ecab00bd06bb31bb28b23710b92eb3c1852e3609a14a1"]


def _strip_comments(text):
    return re.sub(r"/\*[\s\S]*?\*/", " ", re.sub(r"//[^\n]*", " ", text))


class SegmentContractTest(unittest.TestCase):
    """段边界与纯函数纪律：标记唯一、段内禁全局态、helper 在段内、胶水在段外。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def _segment(self):
        s = self.js.index(BEGIN)
        e = self.js.index(END)
        self.assertLess(s, e, "段标记顺序不对")
        return self.js[s:e]

    def test_markers_unique(self):
        self.assertEqual(self.js.count(BEGIN), 1, "begin 必须唯一")
        self.assertEqual(self.js.count(END), 1, "end 必须唯一")
        self.assertLess(self.js.index(BEGIN), self.js.index(END))

    def test_pure_segment_has_no_global_state(self):
        seg = _strip_comments(self._segment())
        for banned in BANNED:
            self.assertNotIn(banned, seg, "禁用引用漏进纯函数段: " + banned)

    def test_helpers_inside_segment(self):
        seg = self._segment()
        for helper in HELPERS:
            self.assertIn(helper, seg, helper + " 不在 cfgxfer 段内")

    def test_glue_outside_segment(self):
        seg = self._segment()
        for glue in GLUE:
            self.assertIn(glue, self.js, glue + " 胶水不见了")
            self.assertNotIn(glue, seg, glue + " 漏进了纯函数段")

    def test_whitelist_pinned_to_13_keys(self):
        seg = self._segment()
        start = seg.index("const CFG_EXPORTABLE = [")
        block = seg[start:seg.index("];", start)]
        for key in WHITELIST:
            self.assertIn('"' + key + '"', block, "白名单少了键 " + key)
        self.assertEqual(block.count('"'), 2 * len(WHITELIST), "白名单键数应为 13")

    def test_derived_and_sensitive_keys_not_in_whitelist(self):
        block = self.js[self.js.index("const CFG_EXPORTABLE = ["):]
        block = block[:block.index("];")]
        for key in NON_EXPORTABLE:
            self.assertNotIn('"' + key + '"', block, key + " 不该进导出白名单")

    def test_sensitive_gate_regex_pinned(self):
        # 副闸是第二道：白名单误加 / 别的工具塞键都兜得住
        self.assertIn(
            "const CFG_SENSITIVE_RE = /token|cred|pairing|secret|password|cookie/i;",
            self.js)

    def test_state_json_not_referenced_in_code(self):
        # state.json 只允许出现在注释里（说明为什么不导）；代码中不许出现
        start = self.js.index(BEGIN)
        end = self.js.index(INTENT_BEGIN)
        code = _strip_comments(self.js[start:end])
        self.assertNotIn("state.json", code, "代码里不许出现 state.json（凭据文件）")

    def test_import_glue_checks_before_writing(self):
        # 口径 1 的胶水形态：parse 不过直接 return，cfgApplyBackup 根本不被调用
        glue = self.js[self.js.index("function cfgImportText"):]
        glue = glue[:glue.index("\n}")]
        self.assertIn("if (!parsed.ok) {", glue, "坏档分支不见了")
        self.assertIn("cfgApplyBackup(parsed.backup)", glue)
        self.assertLess(glue.index("if (!parsed.ok) {"), glue.index("cfgApplyBackup"))


class MarkupContractTest(unittest.TestCase):
    """HTML 接线：卡片控件齐全，状态行只走 textContent。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")

    def test_card_wiring_present(self):
        for frag in ('id="cfgExportBtn"', 'id="cfgImportBtn"', 'id="cfgxferStatus"',
                     'id="cfgFileInput"', 'accept="application/json,.json"',
                     'aria-live="polite"'):
            self.assertIn(frag, self.html, frag)

    def test_status_line_is_textcontent_only(self):
        # AGENTS.md 纪律：任何来源的文本都一律 textContent，禁 innerHTML
        # 注释里点名了 innerHTML（「禁 innerHTML」的告示），先剥注释再查
        glue = self.js[self.js.index("function cfgStatus"):]
        glue = _strip_comments(glue[:glue.index("\n}")])
        self.assertNotIn("innerHTML", glue)
        self.assertIn("textContent", glue)


class HarnessBehaviorTest(unittest.TestCase):
    """真行为：harness 从 app.js 抽出 cfgxfer 段原样执行，必须全绿且不可被悄悄缩水。"""

    @classmethod
    def setUpClass(cls):
        cls.src = HARNESS.read_text(encoding="utf-8")

    def test_harness_passes(self):
        if shutil.which("node") is None:
            self.skipTest("没有 node，跳过 harness")
        p = subprocess.run(["node", str(HARNESS)], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ALL_BACKUP_CASES_PASSED", p.stdout)
        m = re.search(r"cases=(\d+) failed=(\d+)", p.stdout)
        self.assertIsNotNone(m, p.stdout)
        self.assertEqual(int(m.group(2)), 0, p.stdout)
        self.assertGreaterEqual(int(m.group(1)), 21, "用例被删了？")

    def test_harness_refuses_a_broken_segment(self):
        # 段标记漂移时 harness 必须非 0 退出，而不是安静地跑 0 个用例
        if shutil.which("node") is None:
            self.skipTest("没有 node，跳过 harness")
        broken = self.src.replace("const BEGIN = '/* ===== cfgxfer:begin';",
                                  "const BEGIN = '/* ===== nope:begin';")
        self.assertNotEqual(self.src, broken, "补丁没生效")
        tmp = HARNESS.with_name("backup_harness_broken.js")
        try:
            tmp.write_text(broken, encoding="utf-8")
            p = subprocess.run(["node", str(tmp)], cwd=str(ROOT),
                               capture_output=True, text=True, timeout=60)
            self.assertNotEqual(0, p.returncode)
            self.assertIn("段标记", p.stdout + p.stderr)
        finally:
            tmp.unlink()


class SourceTraceTest(unittest.TestCase):
    """出处可 grep：本轮引用的 file:line 与 sha256 必须都在台账第 12 节里。"""

    @classmethod
    def setUpClass(cls):
        cls.doc = DOCS.read_text(encoding="utf-8")
        cls.ui = UI_STUDY.read_text(encoding="utf-8")

    def test_doc_has_round_37_section(self):
        self.assertIn("开源项目参考台账", self.doc.split(chr(10))[0])
        self.assertIn("第 12 节", self.doc)
        self.assertIn("第三十七轮", self.doc)
        self.assertIn("chezmoi", self.doc)
        self.assertIn("v2.73.0", self.doc)
        self.assertIn("MIT", self.doc)

    def test_every_cited_line_is_reachable_in_the_doc(self):
        for ref in SOURCES:
            self.assertIn(ref, self.doc, ref)

    def test_doc_states_the_three_corrections(self):
        # 三条「凭印象写错、逐行核对后改正」的记录必须留在册，否则下轮重犯
        self.assertIn("format.go:172", self.doc, "172 不是校验点这条更正要在册")
        self.assertIn("Apache-2.0", self.doc, "许可更正（MIT 而非 Apache-2.0）要在册")
        self.assertIn("ignoredcmd.go:18", self.doc)
        self.assertIn("ignoredcmd.go:37", self.doc)

    def test_ui_study_has_round_37(self):
        self.assertIn("第三十七轮", self.ui)


if __name__ == "__main__":
    unittest.main()
