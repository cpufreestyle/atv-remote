#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 ATV Remote 的仓库图标（深空 + 透视网格 + 霓虹辉光）。

与 static/icon.svg、tools/make_icons.py、make_icon.swift 共用同一套
「电视屏 + 播放键 + 支架 + 信号弧」字形坐标，保证识别度一致；只把舞台从
Apple 风格品牌蓝渐变底板换成深空霓虹舞台。

字形布局直接 import make_icons，不另抄一份——「换个舞台连字形也换了」这类
分家事故，历史上已经出过两次。

输出：
  assets/icon.svg             矢量母版（与 PNG 同源，由本脚本生成）
  assets/icon-1024.png        主图（圆角 squircle）
  assets/icon-512.png         512（Gitee 仓库头像）
  assets/icon-256.png         256（小尺寸头像 / favicon 备用）
  assets/icon-1024-circle.png 圆形裁切版（GitHub、圆形头像容器）

用法：python3 tools/make_logo.py（需要 Pillow）
"""

import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_icons  # noqa: E402  —— 字形布局从这里取，不另抄一份

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SS = 4  # 超采样倍率

# 舞台配色（字形色仍走 make_icons 的）
BG_IN = (13, 31, 63)      # 深空中心
BG_OUT = (3, 6, 15)       # 深空边缘
NEON = (110, 186, 255)    # 霓虹描边 / 播放键辉光
BRAND = (47, 126, 255)    # 品牌蓝：透视网格与 halo
DEEP = (6, 79, 218)       # 播放键实体
SCREEN = (240, 246, 255)  # 电视屏

# 1024 字形布局：直接复用 make_icons.py 的常量，杜绝「换个舞台连字形也换了」
SCR = make_icons.SCR
SCR_R = make_icons.SCR_R
TRI = make_icons.TRI
NECK = make_icons.NECK
FOOT = make_icons.FOOT
# make_icons 里 tw/th 是全宽全高，转成绘图好用的半宽半高
TRI_CX, TRI_CY = TRI["cx"], TRI["cy"]
TRI_HW, TRI_HH = TRI["tw"] // 2, TRI["th"] // 2

NECK_R = 8
FOOT_R = 17

TILE_R = 232         # 圆角 squircle
HORIZON = 596        # 透视网格视界高度
ARC_C = (512, 372)   # 屏内信号弧圆心
ARC_R = (76, 42)     # 外弧 / 内弧半径
ARC_W = 15           # 信号弧线宽
ARC_DOT = 11         # 弧心圆点半径
ARC_HALF = 27.0      # 信号弧张口半角，与 Pillow 的 207° / 333° 对应
SCR_PAD = 44         # halo 比屏幕外扩多少


def _n(v):
    """SVG 坐标取一位小数，整数不带小数点。"""
    s = "%.1f" % v
    return s[:-2] if s.endswith(".0") else s


def _bg(w, h):
    """深空径向底：中心深蓝 -> 边缘近黑。"""
    n = 96
    small = Image.new("RGBA", (n, n))
    px = small.load()
    for j in range(n):
        for i in range(n):
            dx = i / (n - 1) - 0.5
            dy = j / (n - 1) - 0.42
            t = min(1.0, (dx * dx + dy * dy) ** 0.5 / 0.74)
            px[i, j] = (
                int(BG_IN[0] + (BG_OUT[0] - BG_IN[0]) * t),
                int(BG_IN[1] + (BG_OUT[1] - BG_IN[1]) * t),
                int(BG_IN[2] + (BG_OUT[2] - BG_IN[2]) * t),
                255,
            )
    return small.resize((w, h), Image.BICUBIC)


def _sheen(w, h, k):
    """左上柔和高光，撑出玻璃质感。"""
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    r = 0.62 * w
    cx, cy = 0.24 * w, 0.16 * h
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 26))
    return layer.filter(ImageFilter.GaussianBlur(r * 0.6))


def _star_pts(n=70):
    """星点与 SVG 版共用同一个种子，保证两版位置一致。"""
    rnd = random.Random(8300)
    return [(rnd.uniform(0.02, 0.98), rnd.uniform(0.03, 0.52),
             rnd.uniform(1.0, 2.6), rnd.randint(50, 175)) for _ in range(n)]


def _stars(w, h, k):
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for fx, fy, fr, fa in _star_pts():
        x, y = fx * w, fy * h
        r = fr * k
        d.ellipse([x - r, y - r, x + r, y + r], fill=(205, 229, 255, fa))
    return layer.filter(ImageFilter.GaussianBlur(0.6 * k))


def _grid(w, h, k):
    """透视网格：竖线从视界扇形展开，横线间距按透视收缩。"""
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    hy = HORIZON * k
    vx = 512 * k
    lw = max(1, int(round(2 * k)))
    for i in range(-9, 10):
        d.line([vx + 145 * i * k, hy, vx + 300 * i * k, h],
               fill=BRAND + (160 if i == 0 else 96,), width=lw)
    for n in range(1, 11):
        y = hy + (h - hy) * (n / 10.0) ** 2.1
        d.line([0, y, w, y], fill=BRAND + (int(180 * (1 - n / 12.0) + 42),), width=lw)
    return layer.filter(ImageFilter.GaussianBlur(1.1 * k))


def _horizon(w, h, k):
    """视界霓虹带：字形左右两侧留出来，形成舞台纵深。"""
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    y = HORIZON * k
    d.rectangle([0, y - 2 * k, w, y + 2 * k], fill=NEON + (210,))
    return layer.filter(ImageFilter.GaussianBlur(7 * k))


def _motif(w, h, k, ox=0.0, oy=0.0):
    """电视屏 + 播放键 + 支架 + 信号弧；ox/oy 为 1024 空间平移。"""

    def X(v):
        return (v + ox) * k

    def Y(v):
        return (v + oy) * k

    tri = [(X(TRI_CX - TRI_HW), Y(TRI_CY - TRI_HH)),
           (X(TRI_CX - TRI_HW), Y(TRI_CY + TRI_HH)),
           (X(TRI_CX + TRI_HW), Y(TRI_CY))]

    # 字形背后的霓虹 halo
    halo = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.rounded_rectangle([X(SCR[0]) - SCR_PAD * k, Y(SCR[1]) - SCR_PAD * k,
                          X(SCR[2]) + SCR_PAD * k, Y(SCR[3]) + SCR_PAD * k],
                         radius=(SCR_R + SCR_PAD) * k, fill=BRAND + (130,))
    out = halo.filter(ImageFilter.GaussianBlur(56 * k))

    screen = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(screen)
    sd.rounded_rectangle([X(SCR[0]), Y(SCR[1]), X(SCR[2]), Y(SCR[3])],
                         radius=SCR_R * k, fill=SCREEN + (255,))
    out.alpha_composite(screen)

    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.polygon(tri, fill=NEON + (200,))
    out.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18 * k)))

    body = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(body)
    bd.polygon(tri, fill=DEEP + (255,))
    out.alpha_composite(body)

    arcs = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ad = ImageDraw.Draw(arcs)
    scx, scy = X(ARC_C[0]), Y(ARC_C[1])
    for r, a in zip(ARC_R, (255, 130)):
        ad.arc([scx - r * k, scy - r * k, scx + r * k, scy + r * k],
               start=207, end=333, fill=DEEP + (a,), width=int(ARC_W * k))
    ad.ellipse([scx - ARC_DOT * k, scy - ARC_DOT * k,
                scx + ARC_DOT * k, scy + ARC_DOT * k], fill=DEEP + (255,))
    ad.rounded_rectangle([X(NECK[0]), Y(NECK[1]), X(NECK[2]), Y(NECK[3])],
                         radius=NECK_R * k, fill=SCREEN + (255,))
    ad.rounded_rectangle([X(FOOT[0]), Y(FOOT[1]), X(FOOT[2]), Y(FOOT[3])],
                         radius=FOOT_R * k, fill=SCREEN + (255,))
    out.alpha_composite(arcs)

    # 屏幕外霓虹描边
    rim = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rim)
    rd.rounded_rectangle([X(SCR[0]), Y(SCR[1]), X(SCR[2]), Y(SCR[3])],
                         radius=SCR_R * k, outline=NEON + (240,),
                         width=max(2, int(7 * k)))
    out.alpha_composite(rim)
    return out


def _vignette(w, h):
    """底部轻压暗，让字形更亮地浮在舞台上。"""
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    y0, steps = 0.58 * h, 36
    for i in range(steps):
        t = i / (steps - 1)
        d.rectangle([0, y0 + (h - y0) * t, w, h], fill=(0, 0, 8, int(120 * t)))
    return layer.filter(ImageFilter.GaussianBlur(min(w, h) * 0.02))


def _stage(c, k):
    img = _bg(c, c)
    img.alpha_composite(_sheen(c, c, k))
    img.alpha_composite(_stars(c, c, k))
    img.alpha_composite(_grid(c, c, k))
    img.alpha_composite(_horizon(c, c, k))
    img.alpha_composite(_motif(c, c, k))
    img.alpha_composite(_vignette(c, c))
    return img


def _mask_squircle(c):
    m = Image.new("L", (c, c), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, c, c], radius=TILE_R * (c / 1024.0), fill=255)
    return m


def _mask_circle(c):
    m = Image.new("L", (c, c), 0)
    ImageDraw.Draw(m).ellipse([0, 0, c, c], fill=255)
    return m


def render_pngs():
    for s in (1024, 512, 256):
        c = s * SS
        k = c / 1024.0
        img = _stage(c, k)
        img.putalpha(_mask_squircle(c))
        img.resize((s, s), Image.LANCZOS).save(ASSETS / ("icon-%d.png" % s))
        print("wrote assets/icon-%d.png" % s)
    c = 1024 * SS
    k = c / 1024.0
    img = _stage(c, k)
    img.putalpha(_mask_circle(c))
    img.resize((1024, 1024), Image.LANCZOS).save(ASSETS / "icon-1024-circle.png")
    print("wrote assets/icon-1024-circle.png")


def render_svg():
    """与 PNG 同源的矢量母版：同一套 1024 字形坐标 + 同一批星点。"""
    a = []
    p = a.append
    scr_w, scr_h = SCR[2] - SCR[0], SCR[3] - SCR[1]
    arc_dx = math.cos(math.radians(ARC_HALF))
    arc_dy = math.sin(math.radians(ARC_HALF))

    p('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">')
    p('  <!-- ATV Remote 仓库图标：深空 + 透视网格 + 霓虹辉光。')
    p('       字形坐标与 static/icon.svg、tools/make_icons.py、make_icon.swift 共用，')
    p('       换舞台不换字形。由 tools/make_logo.py 生成，勿手改。 -->')
    p('  <defs>')
    p('    <radialGradient id="deep" cx="0.5" cy="0.42" r="0.78">')
    p('      <stop offset="0" stop-color="#0d1f3f"/>')
    p('      <stop offset="1" stop-color="#03060f"/>')
    p('    </radialGradient>')
    p('    <linearGradient id="rimg" x1="0" y1="0" x2="0" y2="1">')
    p('      <stop offset="0" stop-color="#6ebaff"/>')
    p('      <stop offset="1" stop-color="#2f7eff"/>')
    p('    </linearGradient>')
    p('    <filter id="bl-xs" x="-60%" y="-60%" width="220%" height="220%">')
    p('      <feGaussianBlur stdDeviation="7"/></filter>')
    p('    <filter id="bl-s" x="-60%" y="-60%" width="220%" height="220%">')
    p('      <feGaussianBlur stdDeviation="1.2"/></filter>')
    p('    <filter id="bl-m" x="-60%" y="-60%" width="220%" height="220%">')
    p('      <feGaussianBlur stdDeviation="18"/></filter>')
    p('    <filter id="bl-l" x="-80%" y="-80%" width="260%" height="260%">')
    p('      <feGaussianBlur stdDeviation="56"/></filter>')
    p('    <clipPath id="tile"><rect width="1024" height="1024" rx="' + str(TILE_R) + '"/></clipPath>')
    p('  </defs>')
    p('  <g clip-path="url(#tile)">')
    p('    <rect width="1024" height="1024" fill="url(#deep)"/>')
    p('    <ellipse cx="245" cy="164" rx="640" ry="620" fill="#ffffff" opacity="0.05" filter="url(#bl-l)"/>')
    p('    <g fill="#cde5ff">')
    for fx, fy, fr, fa in _star_pts():
        p('      <circle cx="' + _n(fx * 1024) + '" cy="' + _n(fy * 1024) + '" r="' +
          _n(fr) + '" opacity="' + _n(fa / 255.0) + '"/>')
    p('    </g>')
    p('    <g stroke="#2f7eff" stroke-width="2" filter="url(#bl-xs)">')
    for i in range(-9, 10):
        p('      <path d="M' + str(512 + 145 * i) + ' ' + str(HORIZON) +
          ' L' + str(512 + 300 * i) + ' 1024" opacity="' + ("0.63" if i == 0 else "0.38") + '"/>')
    for n in range(1, 11):
        y = HORIZON + (1024 - HORIZON) * (n / 10.0) ** 2.1
        p('      <path d="M0 ' + _n(y) + ' H1024" opacity="' +
          _n((180 * (1 - n / 12.0) + 42) / 255.0) + '"/>')
    p('    </g>')
    p('    <rect x="0" y="' + str(HORIZON - 2) + '" width="1024" height="4" fill="#6ebaff"')
    p('          opacity="0.82" filter="url(#bl-xs)"/>')
    p('    <rect x="' + str(SCR[0] - SCR_PAD) + '" y="' + str(SCR[1] - SCR_PAD) +
      '" width="' + str(scr_w + 2 * SCR_PAD) + '" height="' + str(scr_h + 2 * SCR_PAD) +
      '" rx="' + str(SCR_R + SCR_PAD) + '" fill="#2f7eff" opacity="0.51" filter="url(#bl-l)"/>')
    p('    <g fill="#f0f6ff">')
    p('      <rect x="' + str(SCR[0]) + '" y="' + str(SCR[1]) + '" width="' + str(scr_w) +
      '" height="' + str(scr_h) + '" rx="' + str(SCR_R) + '"/>')
    p('      <rect x="' + str(NECK[0]) + '" y="' + str(NECK[1]) + '" width="' +
      str(NECK[2] - NECK[0]) + '" height="' + str(NECK[3] - NECK[1]) + '" rx="' + str(NECK_R) + '"/>')
    p('      <rect x="' + str(FOOT[0]) + '" y="' + str(FOOT[1]) + '" width="' +
      str(FOOT[2] - FOOT[0]) + '" height="' + str(FOOT[3] - FOOT[1]) + '" rx="' + str(FOOT_R) + '"/>')
    p('    </g>')
    tri = ('M' + str(TRI_CX - TRI_HW) + ' ' + str(TRI_CY - TRI_HH) +
           ' L' + str(TRI_CX - TRI_HW) + ' ' + str(TRI_CY + TRI_HH) +
           ' L' + str(TRI_CX + TRI_HW) + ' ' + str(TRI_CY) + ' Z')
    p('    <path d="' + tri + '" fill="#6ebaff" opacity="0.78" filter="url(#bl-m)"/>')
    p('    <path d="' + tri + '" fill="#064fda"/>')
    p('    <g fill="none" stroke="#064fda" stroke-width="' + str(ARC_W) + '" stroke-linecap="round">')
    for r in ARC_R:
        p('      <path d="M' + _n(ARC_C[0] + r * arc_dx) + ' ' + _n(ARC_C[1] - r * arc_dy) +
          ' A' + str(r) + ' ' + str(r) + ' 0 0 0 ' + _n(ARC_C[0] - r * arc_dx) + ' ' +
          _n(ARC_C[1] - r * arc_dy) + '" opacity="' +
          _n(255 / 255.0 if r == ARC_R[0] else 130 / 255.0) + '"/>')
    p('    </g>')
    p('    <circle cx="' + str(ARC_C[0]) + '" cy="' + str(ARC_C[1]) + '" r="' + str(ARC_DOT) +
      '" fill="#064fda"/>')
    p('    <rect x="' + str(SCR[0]) + '" y="' + str(SCR[1]) + '" width="' + str(scr_w) +
      '" height="' + str(scr_h) + '" rx="' + str(SCR_R) +
      '" fill="none" stroke="url(#rimg)" stroke-width="7"/>')
    p('  </g>')
    p('</svg>')
    (ASSETS / "icon.svg").write_text("\n".join(a) + "\n", encoding="utf-8")
    print("wrote assets/icon.svg")


def main():
    ASSETS.mkdir(exist_ok=True)
    render_pngs()
    render_svg()


if __name__ == "__main__":
    main()
