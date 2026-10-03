#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""「看不到图标」的两类回归。

用户报「看不到图标」时，实测查出两个独立原因，都不是图标文件画得不好：

1. **浏览器标签页 / 书签图标**：`index.html` 里最后一条 `rel=icon` 是早期写死的
   emoji data-URI（📺）。浏览器取「最后一个能用的 rel=icon」当主图标，所以新做的
   品牌蓝矢量图标被它顶掉了。现在那条已删，并锁住顺序：位图在前、SVG 在后
   （不认 SVG 的浏览器跳过它、退回位图）。

2. **手机桌面图标**：Android 自适应图标 = 背景 `#1d212b` + `res/mipmap-*/ic_launcher_fg.png`。
   9/26 重做图标时只更新了 mac/ 与 static/，漏了 res/ —— 那五张还是 8/27 的旧图
   （深色底 + 深色字形），与自适应背景的对比度只有 **1.08:1**，肉眼就是一块深色方块。
   现在由 `tools/make-android-icons.py` 从 `mac/ic_launcher_fg_432.png` 生成，
   对比度 15.11:1，且断言盯住「主体色必须与背景拉得开」。

只用标准库 + Pillow（没装 Pillow 时跳过像素断言，其余照跑）。
"""

import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
INDEX = STATIC / "index.html"
RES = ROOT / "android-native/app/src/main/res"
BG_XML = RES / "drawable/ic_launcher_bg.xml"
MIPMAPS = ("mipmap-mdpi", "mipmap-hdpi", "mipmap-xhdpi",
           "mipmap-xxhdpi", "mipmap-xxxhdpi")

try:
    from PIL import Image
except ImportError:  # 环境没装 Pillow 时只跑结构断言
    Image = None


class IconLinkParser(HTMLParser):
    """按出现顺序收集 <link rel=...icon...>，保留 href 与 type。"""

    def __init__(self):
        super().__init__()
        self.icons = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        d = dict(attrs)
        # 只要主图标那几条（rel=icon / shortcut icon）；apple-touch-icon 是
        # 「加桌面」专用，不参与标签页图标的挑选，别混进来搅乱顺序断言
        if (d.get("rel") or "").strip() in ("icon", "shortcut icon"):
            self.icons.append(d)


def _rel_lum(rgb):
    def f(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2])


def contrast(a, b):
    la, lb = _rel_lum(a), _rel_lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


class FaviconOrderTest(unittest.TestCase):
    """index.html 的 rel=icon 顺序 + 没有写死的占位图。"""

    @classmethod
    def setUpClass(cls):
        cls.html = INDEX.read_text(encoding="utf-8")
        p = IconLinkParser()
        p.feed(cls.html)
        cls.icons = p.icons

    def test_no_data_uri_icon(self):
        # 早期写死的 emoji data-URI 会顶掉真图标：浏览器认最后一条 rel=icon
        data_uris = [i for i in self.icons if (i.get("href") or "").startswith("data:")]
        self.assertEqual(data_uris, [],
                         "不要再往 head 里塞 data-URI 图标占位：它会盖掉真图标")

    def test_svg_is_last(self):
        self.assertTrue(self.icons, "head 里没有 rel=icon")
        last = self.icons[-1]
        self.assertEqual(last.get("type"), "image/svg+xml",
                         "最后一条应是 SVG（现代浏览器取最后一条可用的）")
        self.assertTrue((last.get("href") or "").endswith("/icon.svg"))

    def test_png_fallback_before_svg(self):
        types = [i.get("type") for i in self.icons]
        self.assertIn("image/png", types, "要有 PNG 兜底（不认 SVG 的浏览器）")
        self.assertLess(types.index("image/png"), types.index("image/svg+xml"),
                        "PNG 必须排在 SVG 之前，否则不认 SVG 的浏览器没有兜底")

    def test_declared_icons_exist(self):
        for i in self.icons:
            href = i.get("href") or ""
            if href.startswith("data:"):
                continue
            p = STATIC / href.rsplit("/", 1)[-1]
            self.assertTrue(p.is_file(), "缺文件 " + str(p))


class AndroidAdaptiveIconTest(unittest.TestCase):
    """自适应图标的前景必须与背景拉得开，否则桌面上就是一块纯色。"""

    @classmethod
    def setUpClass(cls):
        xml = BG_XML.read_text(encoding="utf-8")
        i = xml.index('color="') + len('color="')
        hexcolor = xml[i:xml.index('"', i)].lstrip("#")
        cls.bg = tuple(int(hexcolor[k:k + 2], 16) for k in (0, 2, 4))

    def test_five_densities_present(self):
        for d in MIPMAPS:
            p = RES / d / "ic_launcher_fg.png"
            self.assertTrue(p.is_file(), "缺 " + str(p))
            self.assertGreater(p.stat().st_size, 2000,
                               str(p) + " 太小，不像真图")

    @unittest.skipIf(Image is None, "没装 Pillow")
    def test_foreground_contrasts_with_background(self):
        import collections
        for d in MIPMAPS:
            im = Image.open(RES / d / "ic_launcher_fg.png").convert("RGBA")
            px = list(im.getdata())
            opaque = [q[:3] for q in px if q[3] > 200]
            self.assertTrue(opaque, d + " 整张图都是透明的")
            body = collections.Counter(opaque).most_common(1)[0][0]
            c = contrast(body, self.bg)
            self.assertGreaterEqual(
                c, 3.0,
                "%s 的主体色 %s 与自适应背景 %s 对比度只有 %.2f:1（<3 就几乎看不见）"
                % (d, body, self.bg, c))

    @unittest.skipIf(Image is None, "没装 Pillow")
    def test_foreground_matches_master(self):
        """五张必须同源，别出现「某档忘了换」。"""
        master = (ROOT / "mac/ic_launcher_fg_432.png")
        if not master.is_file():
            self.skipTest("没有 mac/ic_launcher_fg_432.png")
        ref = Image.open(master).convert("RGBA").resize((108, 108), Image.LANCZOS)
        got = Image.open(RES / "mipmap-mdpi/ic_launcher_fg.png").convert("RGBA")
        # 允许重采样误差：逐像素平均差应很小
        diff = sum(abs(a - b) for pa, pb in zip(ref.getdata(), got.getdata())
                   for a, b in zip(pa, pb)) / (108 * 108 * 4)
        self.assertLess(diff, 6.0,
                        "mdpi 前景与 mac 主图不像同一张（平均像素差 %.1f）" % diff)


class GeneratorTest(unittest.TestCase):
    """生成脚本要在仓库里，别让这五张图又变成「只存在于某台机器上」。"""

    def test_generator_exists(self):
        p = ROOT / "tools/make-android-icons.py"
        self.assertTrue(p.is_file(), "缺 tools/make-android-icons.py")
        src = p.read_text(encoding="utf-8")
        for d in MIPMAPS:
            self.assertIn(d, src, "生成脚本要覆盖 " + d)


class DesktopShortcutIconTest(unittest.TestCase):
    """桌面 .app 别顶着一张 AppleScript 默认羊皮纸。"""

    def test_script_recompiles_assets_car(self):
        """osacompile 自带一副「羊皮纸」Assets.car（图标栈名也叫 applet），Finder 优先读
        它、盖掉 applet.icns；生成脚本必须用 AppIcon.icns 重编译同名 car 盖回去。"""
        s = (ROOT / "mac/make-desktop-shortcut.sh").read_text(encoding="utf-8")
        self.assertIn("iconutil -c iconset", s, "脚本要把 AppIcon.icns 转回 iconset")
        self.assertIn("--app-icon applet", s,
                      "重编译的图标栈必须叫 applet（对上 CFBundleIconName）")
        self.assertIn("Contents/Resources", s, "car 要编译进 app 的 Resources")
        self.assertIn("actool", s)


class RepoLogoTest(unittest.TestCase):
    """仓库图标 assets/ 与 README 引用别分家。"""

    ASSETS = ROOT / "assets"

    PNG_SIZES = (("icon-256.png", 256), ("icon-512.png", 512),
                 ("icon-1024.png", 1024), ("icon-1024-circle.png", 1024))

    def test_files_exist(self):
        svg = self.ASSETS / "icon.svg"
        self.assertTrue(svg.is_file(), "缺 assets/icon.svg")
        self.assertIn("<svg", svg.read_text(encoding="utf-8"))
        for name, _ in self.PNG_SIZES:
            p = self.ASSETS / name
            self.assertTrue(p.is_file(), "缺 " + str(p))
            self.assertGreater(p.stat().st_size, 2000,
                               str(p) + " 太小，不像真图")

    @unittest.skipIf(Image is None, "没装 Pillow")
    def test_png_dimensions(self):
        for name, size in self.PNG_SIZES:
            with Image.open(self.ASSETS / name) as im:
                self.assertEqual(im.size, (size, size),
                                 name + " 应是 %d×%d" % (size, size))

    def test_readme_references_logo(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("assets/icon-1024.png", readme,
                      "README 要引用 assets/icon-1024.png，否则仓库页没有 logo")

    def test_logo_generator_reuses_glyph_layout(self):
        src = (ROOT / "tools/make_logo.py").read_text(encoding="utf-8")
        self.assertIn("import make_icons", src,
                      "字形布局必须 import make_icons，别另抄一份常量")
        for name in ("icon.svg", "icon-1024-circle.png"):
            self.assertIn(name, src, "生成脚本要产出 " + name)

    def test_make_icons_no_import_side_effect(self):
        src = (ROOT / "tools/make_icons.py").read_text(encoding="utf-8")
        self.assertIn("def main():", src)
        self.assertIn('if __name__ == "__main__":', src,
                      "make_icons.py 必须收进 main() 保护，"
                      "否则被 import 时会静默重写 static/")


if __name__ == "__main__":
    unittest.main()
