#!/usr/bin/env python3
"""生成 ATV Remote 的 PWA / iOS 主图标（PNG）。

规范来源：Android 自适应图标（maskable，安全区直径 80%）+ iOS 主图标安全区。
配色沿用 static/style.css 的令牌：深色底 #121315、品牌蓝 #006afd、近白字形。
4x 超采样绘制后降采样，边缘平滑；换色只改下面三个常量，与 CSS 保持人工同步。

用法：python3 tools/make_icons.py（需要 Pillow）
"""
from pathlib import Path

from PIL import Image, ImageDraw

STATIC = Path(__file__).resolve().parent.parent / "static"
SS = 4  # 超采样倍率

BG = (18, 19, 21, 255)       # #121315 —— style.css 的 --bg 令牌
ACCENT = (0, 106, 253, 255)  # #006afd —— --accent 令牌
WHITE = (232, 234, 238, 255) # 近白字形，小尺寸下比纯白更耐看


def draw_glyph(d, box):
    # 「电视 + 播放键」：坐标 0..1 归一化后映射进 box。屏幕=品牌蓝圆角矩形，
    # 播放三角与支架=白色——48px 缩略图也认得出。
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0

    def m(px, py):
        return (x0 + px * w, y0 + py * h)

    d.rounded_rectangle([m(0.22, 0.28), m(0.78, 0.62)], radius=int(0.055 * w), fill=ACCENT)
    d.polygon([m(0.45, 0.355), m(0.45, 0.545), m(0.61, 0.45)], fill=WHITE)
    d.rectangle([m(0.47, 0.62), m(0.53, 0.705)], fill=WHITE)
    d.rounded_rectangle([m(0.36, 0.705), m(0.64, 0.755)], radius=int(0.014 * w), fill=WHITE)


def render(size, maskable=False, apple=False):
    c = size * SS
    img = Image.new("RGBA", (c, c), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if maskable or apple:
        # 全出血：系统（Android 自适应 / iOS）会自行裁切，露透明边很难看
        d.rectangle([0, 0, c, c], fill=BG)
    else:
        d.rounded_rectangle([0, 0, c, c], radius=0.225 * c, fill=BG)
    if apple:
        box = (0.15 * c, 0.15 * c, 0.85 * c, 0.85 * c)      # iOS 安全区
    elif maskable:
        box = (0.10 * c, 0.10 * c, 0.90 * c, 0.90 * c)      # maskable 直径 80%
    else:
        box = (0.20 * c, 0.19 * c, 0.80 * c, 0.81 * c)
    draw_glyph(d, box)
    return img.resize((size, size), Image.LANCZOS)


ICONS = [
    ("icon-192.png", dict(size=192)),                        # manifest any
    ("icon-512.png", dict(size=512)),                        # manifest any
    ("icon-maskable-512.png", dict(size=512, maskable=True)),  # Android 自适应图标
    ("apple-touch-icon.png", dict(size=180, apple=True)),      # iOS 加桌面
]


if __name__ == "__main__":
    for name, kw in ICONS:
        out = STATIC / name
        render(**kw).save(out, "PNG", optimize=True)
        print("wrote", out)
