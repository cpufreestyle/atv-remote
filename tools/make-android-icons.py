#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 mac/ic_launcher_fg_432.png 生成 Android 自适应图标的前景层（五个密度）。

为什么单独一个脚本：这五张 PNG 是手工放进 res/ 的（build.gradle 没有 copy 任务），
9/26 重做主图标时漏了这里 —— 结果手机上还是 8/27 的旧图（深色底 + 深色字形），
与自适应图标背景 #1d212b 的对比度只有 1.08:1，肉眼就是一块深色方块，
用户报「看不到图标」就是这个。

生成后会自查对比度：前景主体色与自适应背景必须拉开（>= 3:1，WCAG 非文本阈值），
不合格直接非零退出 —— 免得下次又安静地生成一堆看不见的图。

用法：python3 tools/make-android-icons.py（需要 Pillow）
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "mac" / "ic_launcher_fg_432.png"
RES = ROOT / "android-native/app/src/main/res"

# Android 密度档位：108dp 基准
DENSITIES = {
    "mipmap-mdpi": 108,
    "mipmap-hdpi": 162,
    "mipmap-xhdpi": 216,
    "mipmap-xxhdpi": 324,
    "mipmap-xxxhdpi": 432,
}

MIN_CONTRAST = 3.0


def _rel_lum(rgb):
    def f(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2])


def _contrast(a, b):
    la, lb = _rel_lum(a), _rel_lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _bg_color():
    """自适应图标的背景色，来自 res/drawable/ic_launcher_bg.xml。"""
    xml = (RES / "drawable/ic_launcher_bg.xml").read_text(encoding="utf-8")
    i = xml.index("color=") + len("color=") + 1   # 跳过引号
    hexcolor = xml[i:xml.index(chr(34), i)].lstrip("#")
    return tuple(int(hexcolor[k:k + 2], 16) for k in (0, 2, 4))


def main():
    if not SRC.is_file():
        raise SystemExit("缺 %s（先跑 swift make_icon.swift）" % SRC)
    src = Image.open(SRC).convert("RGBA")
    for folder, px in DENSITIES.items():
        out = RES / folder / "ic_launcher_fg.png"
        src.resize((px, px), Image.LANCZOS).save(out)
        print("wrote %s (%dpx)" % (out.relative_to(ROOT), px))

    # 自查：桌面上「看不看得见」全看这一条
    import collections
    bg = _bg_color()
    bad = []
    print("对比度自查（自适应背景 %s）：" % (bg,))
    for folder in DENSITIES:
        im = Image.open(RES / folder / "ic_launcher_fg.png").convert("RGBA")
        body = collections.Counter(
            q[:3] for q in im.getdata() if q[3] > 200).most_common(1)[0][0]
        c = _contrast(body, bg)
        print("  %s %s 主体 %s 对比度 %.2f:1"
              % ("OK " if c >= MIN_CONTRAST else "!! ", folder, body, c))
        if c < MIN_CONTRAST:
            bad.append(folder)
    if bad:
        raise SystemExit("❌ 这些档位前景与背景对比度 < %.1f:1，桌面上会看不清：%s"
                         % (MIN_CONTRAST, ", ".join(bad)))
    print("✅ 五档都够清楚")


if __name__ == "__main__":
    main()

