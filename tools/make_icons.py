#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 ATV Remote 的 PWA / iOS 主图标（PNG）。

与 mac/make_icon.swift（macOS + Android 启动图标）、static/icon.svg 同一套视觉，
即 Apple 风格：品牌蓝对角渐变底 + 左上径向高光 + 底部轻压暗，白色「电视屏 +
播放键」字形带柔和投影。1024 布局坐标与 Swift 版共用（改这里记得同步那边）。

- maskable / apple-touch：全出血，交给 Android 自适应遮罩 / iOS 裁切；
- 普通 PWA 图标：自带圆角（系统不替它裁）。

4x 超采样绘制后降采样，边缘平滑；换色只改下面四个常量，与 style.css 人工同步。

用法：python3 tools/make_icons.py（需要 Pillow）
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

STATIC = Path(__file__).resolve().parent.parent / "static"
SS = 4  # 超采样倍率

BLUE_TOP = (47, 126, 255, 255)    # #2f7eff —— 渐变起点（左上）
BLUE_BOT = (0, 71, 196, 255)      # #0047c4 —— 渐变终点（右下）
SCREEN = (245, 248, 252, 255)    # #f5f8fc —— 字形（电视屏）
TRIANGLE = (6, 79, 218, 255)     # #064fda —— 播放键

# 1024 布局（与 make_icon.swift 完全一致）。y 用「距顶部」描述，画的时候再翻。
SCR = (196, 264, 828, 668)        # 电视屏 x0, y_top, x1, y_bottom
SCR_R = 76
TRI = dict(cx=522, cy=464, tw=132, th=166)   # 播放键中心、半宽半高
NECK = (466, 668, 558, 710)       # 支架颈
FOOT = (378, 714, 646, 748)       # 支架脚
MOTIF_C = (512.0, 518.0)          # 字形包围盒中心（居中用）


def _gradient(size, top, bot):
    """对角渐变（左上 → 右下）。小图算好再放大，过渡才顺。"""
    n = 64
    small = Image.new("RGBA", (n, n))
    px = small.load()
    for j in range(n):
        ty = j / (n - 1)
        for i in range(n):
            t = (i / (n - 1) + ty) / 2.0
            px[i, j] = tuple(int(top[k] + (bot[k] - top[k]) * t) for k in range(4))
    return small.resize((size, size), Image.BICUBIC)


def _glow(size, alpha=56):
    """左上径向高光：一层高斯模糊的亮斑。"""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    r = size * 0.70
    cx, cy = size * 0.28, size * 0.24
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, alpha))
    return layer.filter(ImageFilter.GaussianBlur(r * 0.55))


def _vignette(size, alpha=40):
    """底部轻压暗：从 66% 高度往下渐深。"""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    y0, steps = size * 0.66, 32
    for k in range(steps):
        t = k / float(steps - 1)
        y = y0 + size * 0.34 * t
        d.rectangle([0, y, size, size], fill=(0, 0, 0, int(alpha * t)))
    return layer.filter(ImageFilter.GaussianBlur(size * 0.02))


def _motif(size, scale=1.0, center=True):
    """电视屏 + 播放键 + 支架。scale=1 即 1024 布局原样；center 把包围盒移到画布中心。"""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    s = size / 1024.0 * scale
    ox, oy = 0.0, 0.0
    if center:
        ox = size / 2.0 - MOTIF_C[0] * s
        oy = size / 2.0 - MOTIF_C[1] * s

    def rect(box, radius=0):
        b = [box[0] * s + ox, box[1] * s + oy,
             box[2] * s + ox, box[3] * s + oy]
        if radius:
            d.rounded_rectangle(b, radius=radius * s, fill=SCREEN)
        else:
            d.rectangle(b, fill=SCREEN)

    # 柔和投影：先按同一形状画一层黑，模糊后垫在下面
    sh = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle([SCR[0] * s + ox, SCR[1] * s + oy + 14 * s,
                          SCR[2] * s + ox, SCR[3] * s + oy + 14 * s],
                         radius=SCR_R * s, fill=(0, 0, 0, 80))
    layer.alpha_composite(sh.filter(ImageFilter.GaussianBlur(26 * s)))

    rect(SCR, SCR_R)
    cx, cy = TRI["cx"] * s + ox, TRI["cy"] * s + oy
    tw, th = TRI["tw"] / 2 * s, TRI["th"] / 2 * s
    d.polygon([(cx - tw, cy - th), (cx - tw, cy + th), (cx + tw, cy)],
              fill=TRIANGLE)
    rect(NECK, 8)
    rect(FOOT, 17)
    return layer


def _tile(size, radius_ratio=None):
    """渐变 + 高光 + 压暗；radius_ratio 给值时裁圆角（普通 PWA 图标）。"""
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tile.alpha_composite(_gradient(size, BLUE_TOP, BLUE_BOT))
    tile.alpha_composite(_glow(size))
    tile.alpha_composite(_vignette(size))
    if radius_ratio:
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, size, size], radius=radius_ratio * size, fill=255)
        tile.putalpha(mask)
    return tile


def render(size, maskable=False, apple=False):
    c = size * SS
    img = _tile(c, radius_ratio=None if (maskable or apple) else 0.225)
    if apple:
        img.alpha_composite(_motif(c, scale=1.0, center=True))
    elif maskable:
        # 自适应遮罩会裁成圆：稍微放大并居中，保证圆形里也饱满
        img.alpha_composite(_motif(c, scale=1.15, center=True))
    else:
        img.alpha_composite(_motif(c, scale=1.0, center=True))
    return img.resize((size, size), Image.LANCZOS)


ICONS = [
    ("icon-192.png", dict(size=192)),
    ("icon-512.png", dict(size=512)),
    ("icon-maskable-512.png", dict(size=512, maskable=True)),
    ("apple-touch-icon.png", dict(size=180, apple=True)),
]

for name, kw in ICONS:
    render(**kw).save(STATIC / name)
    print("wrote static/" + name)

