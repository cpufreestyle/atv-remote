"""PWA 离线壳（第十轮）回归：manifest 结构 / 图标文件 / Service Worker 关键约定 / 静态 MIME。

不起端口、不碰设备：读 static/ 文件 + node --check + 解 PNG IHDR 尺寸（不依赖 Pillow）。
"""
import json
import shutil
import struct
import subprocess
import unittest
from pathlib import Path

from server import static_ctype

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


def png_size(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssertionError("不是 PNG")
    if data[12:16] != b"IHDR":
        raise AssertionError("缺 IHDR")
    return struct.unpack(">II", data[16:24])


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.m = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))

    def test_required_fields(self):
        for k in ("name", "short_name", "start_url", "scope", "display",
                  "background_color", "theme_color", "icons"):
            self.assertIn(k, self.m)
        self.assertEqual(self.m["display"], "standalone")

    def test_png_icons_declared_and_present(self):
        want = {"/static/icon-192.png": (192, 192),
                "/static/icon-512.png": (512, 512),
                "/static/icon-maskable-512.png": (512, 512)}
        by_src = {ic["src"]: ic for ic in self.m["icons"]}
        for src, size in want.items():
            self.assertIn(src, by_src, "manifest 缺图标 " + src)
            self.assertEqual(by_src[src]["type"], "image/png")
            p = STATIC / src.rsplit("/", 1)[-1]
            self.assertTrue(p.is_file(), "缺文件 " + str(p))
            self.assertEqual(png_size(p.read_bytes()), size)

    def test_maskable_purpose(self):
        self.assertIn("maskable", {ic.get("purpose") for ic in self.m["icons"]})

    def test_theme_color_is_hex(self):
        self.assertRegex(self.m["theme_color"], r"^#[0-9a-fA-F]{6}$")

    def test_accent_token_exists_in_css(self):
        # theme_color 取强调蓝；CSS 侧是 HSL 令牌，html meta 用背景色、
        # manifest 用强调色是有意分工，这里只保证设计令牌没被删
        css = (STATIC / "style.css").read_text(encoding="utf-8")
        self.assertIn("--accent", css)

    def test_index_head_links(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="apple-touch-icon"', html)
        self.assertIn('rel="manifest"', html)
        self.assertIn("apple-mobile-web-app-capable", html)
        self.assertIn('name="theme-color"', html)


class ServiceWorkerTest(unittest.TestCase):
    def setUp(self):
        self.sw = (STATIC / "sw.js").read_text(encoding="utf-8")

    def test_syntax(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("PATH 里没有 node")
        r = subprocess.run([node, "--check", str(STATIC / "sw.js")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_event_handlers(self):
        for ev in ("install", "activate", "fetch"):
            self.assertIn('addEventListener("' + ev + '"', self.sw)

    def test_api_and_post_bypassed(self):
        self.assertIn('"/api/"', self.sw)
        self.assertIn('req.method !== "GET"', self.sw)

    def test_shell_precached(self):
        for a in ('"/"', '"/static/app.js"', '"/static/style.css"',
                  '"/static/manifest.webmanifest"'):
            self.assertIn(a, self.sw)

    def test_cache_versioned(self):
        self.assertRegex(self.sw, r'CACHE\s*=\s*"atv-shell-v\d+"')


class StaticCtypeTest(unittest.TestCase):
    def test_explicit_map_wins(self):
        self.assertEqual(static_ctype(Path("a.webmanifest")), "application/manifest+json")
        self.assertEqual(static_ctype(Path("a.js")), "text/javascript")
        self.assertEqual(static_ctype(Path("a.svg")), "image/svg+xml")
        self.assertEqual(static_ctype(Path("a.png")), "image/png")

    def test_suffix_case_insensitive(self):
        self.assertEqual(static_ctype(Path("A.WEBMANIFEST")), "application/manifest+json")


if __name__ == "__main__":
    unittest.main()
