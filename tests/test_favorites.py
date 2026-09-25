#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收藏夹 / 快捷启动（第二十三轮）回归。

动机是观察：遥控器的功能越加越多，但用户一天里真正点的还是那几个 App。学浏览器
Speed Dial（把天天进的站钉在新标签页）、VS Code Favorites（常用文件不靠回忆路径）、
Plex「Continue Watching」（没看完的顶到第一排）——共同点都是「固定常用、一键直达」。

这条链路是纯前端的：复用既有的启动通道（launchApp → /api/cmd 的 type=app），
零新增后端，所以这里只验四件事：
  1) 规则段（favorites:begin/end）可搬进 node：harness 从 static/app.js 原样抽出执行，
     上限 / 去重 / 置顶 / 名字兜底有真行为证据；
  2) 持久化只落 localStorage（atv.favApps）——state.json 会被打进 bundle.tgz 分发给
     局域网任何设备，可重放内容不该进去；
  3) 收藏行 / 星标渲染一律 textContent——Apple TV 应用列表来自设备广播，可伪造；
  4) 样式段只用语义令牌、星标状态走 aria-pressed，不靠动效。

只用标准库 + 读仓库文件 + 跑 node harness：不起端口、不碰 adb / pyatv。
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
HARNESS = Path(__file__).resolve().parent / "fav_harness.js"

FAV_BEGIN = "/* ===== favorites:begin"
FAV_END = "/* ===== favorites:end"
CSS_MARK = "收藏夹 / 快捷启动"
FAV_KEY = "atv.favApps"


class PureSegmentTest(unittest.TestCase):
    """规则段：符号齐全、可搬进 node、没碰 DOM / 网络 / 敏感文件。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.server = (ROOT / "server.py").read_text(encoding="utf-8")

    def seg(self):
        return self.js[self.js.index(FAV_BEGIN):self.js.index(FAV_END)]

    def test_segment_has_every_rule(self):
        seg = self.seg()
        for frag in ("const FAV_MAX = 8;", "function favNorm(", "function favIsPinned(",
                     "function favToggle(", "function favResolve("):
            self.assertIn(frag, seg)

    def test_segment_touches_no_dom_or_network(self):
        code = re.sub(r"/\*[\s\S]*?\*/", " ", re.sub(r"//[^\n]*", " ", self.seg()))
        for banned in ("document.", "localStorage", "setTimeout", "fetch(", "innerHTML", "$("):
            self.assertNotIn(banned, code)

    def test_persistence_is_localstorage_only_not_state_json(self):
        # state.json 会带上配对凭据被打包分发：收藏夹内容可重放，但不该进敏感文件
        self.assertIn('localStorage.getItem(FAV_KEY)', self.js)
        self.assertIn('localStorage.setItem(FAV_KEY', self.js)
        self.assertNotIn(FAV_KEY, self.server)


class WiringTest(unittest.TestCase):
    """接线：卡片、星标挂点、收藏行渲染、启动通道复用、命令面板。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_card_markup_exists(self):
        for frag in ('id="favCard"', 'id="favList"', 'id="favEmpty"', 'id="favCount"'):
            self.assertIn(frag, self.html)

    def test_storage_key_is_defined_once(self):
        # 曾经漏写 const FAV_KEY 的定义：favLoad 的 try 把 ReferenceError 吞了，
        # 页面照常加载、星标照常切换，但收藏永远不落盘。单看 getItem/setItem 的
        # usage 齐全查不出问题，只有「定义恰好一次」这种断言拦得住。
        self.assertEqual(self.js.count('const FAV_KEY = "atv.favApps"'), 1)

    def test_pin_point_on_android_presets(self):
        self.assertIn("function renderAppPresets() {", self.js)
        self.assertIn("favStarBtn(a.name, a.pkg)", self.js)

    def test_pin_point_on_appletv_app_list(self):
        i = self.js.index("async function loadAtvApps() {")
        self.assertIn("favStarBtn(a.name || a.id, a.id)", self.js[i:i + 1600])

    def test_favorites_reuse_the_existing_launch_channel(self):
        i = self.js.index("function favRender() {")
        body = self.js[i:self.js.index("\nfunction ", i + 10)]
        self.assertIn('launchApp(a.name, a.pkg)', body)
        self.assertIn('$("#favCount")', body)

    def test_fav_rows_and_stars_render_as_text_only(self):
        for start in ("function favRender() {", "function favStarBtn(",
                      "function renderAppPresets() {"):
            i = self.js.index(start)
            body = self.js[i:self.js.index("\nfunction ", i + 10)]
            self.assertNotIn("innerHTML", body)
            self.assertNotIn("insertAdjacentHTML", body)

    def test_stars_have_one_data_source(self):
        # favRender 统一刷新页面上所有 .favstar：收藏夹是唯一数据源
        self.assertIn('$$(".favstar").forEach', self.js)

    def test_command_palette_entry_exists(self):
        self.assertIn('push("fav:" + a.pkg, "收藏", "★"', self.js)
        self.assertIn('"收藏 固定 常用 favorite pin quick "', self.js)


class HarnessParityTest(unittest.TestCase):
    """harness 抽的就是 static/app.js 里那一段：符号必须同源，且真跑全绿。"""

    @classmethod
    def setUpClass(cls):
        js = (STATIC / "app.js").read_text(encoding="utf-8")
        cls.seg = js[js.index(FAV_BEGIN):js.index(FAV_END)]
        cls.harness = HARNESS.read_text(encoding="utf-8")

    def test_harness_references_the_same_symbols(self):
        for sym in ("FAV_MAX", "favNorm", "favIsPinned", "favToggle", "favResolve",
                    "ALL_FAV_CASES_PASSED"):
            self.assertIn(sym, self.harness)

    def test_harness_runs_green(self):
        if shutil.which("node") is None:
            self.skipTest("没有 node，跳过 harness")
        p = subprocess.run(["node", str(HARNESS)], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("ALL_FAV_CASES_PASSED", p.stdout)


class StyleTest(unittest.TestCase):
    """样式：语义色、行形态、aria 驱动的星标状态。"""

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

    def test_rows_reuse_the_shared_row_look(self):
        seg = self.seg()
        for frag in ("#favList {", ".favrow .btn {", ".favrow .btn.tiny {",
                     ".brow.apps.rows {"):
            self.assertIn(frag, seg)

    def test_star_state_is_aria_driven(self):
        seg = self.seg()
        self.assertIn('.favstar[aria-pressed="true"]', seg)
        self.assertIn('.favstar[aria-pressed="false"]', seg)


if __name__ == "__main__":
    unittest.main()
