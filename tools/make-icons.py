#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成 iOS / PWA 图标（可重复执行，覆盖输出）。

用法：  python tools/make-icons.py

产物（直接放在 app/ 下，与 index.html 同级）：
          app/apple-touch-icon.png   180x180  iOS 添加到主屏幕
          app/icon-192.png           192x192  Android / manifest
          app/icon-512.png           512x512  启动画面
          app/icon-maskable-512.png  512x512  自适应图标（内容缩到安全区）

放在 app/ 根层而不是子目录，是为了让本地宿主的静态白名单继续保持
「只放行扁平文件名、不递归子目录」这条安全约束。

设计：纯黑背景 + 一块深色翻页卡（中缝横穿）+ 细白字 "12"，与时钟本体一致。
只依赖 Pillow。
"""

import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("需要 Pillow：pip install pillow")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "app")

BG = (0, 0, 0)
CARD = (35, 42, 51)          # #232a33，与默认卡片色一致
CARD_EDGE = (58, 68, 82)
SEAM = (10, 12, 15)          # #0a0c0f，与默认中缝一致
DIGIT = (255, 255, 255)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuil.ttf",     # Segoe UI Light：字重与时钟数字接近
    r"C:\Windows\Fonts\seguisli.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]


def load_font(size):
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def draw_icon(size, maskable=False):
    """画一枚图标；maskable=True 时把主体缩到中心 ~68%，留出裁切安全区。"""
    scale = 4                                   # 超采样，边缘更顺滑
    S = size * scale
    img = Image.new("RGB", (S, S), BG)
    d = ImageDraw.Draw(img)

    # 主体缩放系数：maskable 需要把内容收进安全圆内
    k = 0.68 if maskable else 0.80

    cw = int(S * 0.46 * k / 0.80)               # 卡片宽
    ch = int(cw * 1.42)                         # 与页面卡片同比例
    if ch > S * 0.86 * k / 0.80:
        ch = int(S * 0.86 * k / 0.80)
        cw = int(ch / 1.42)
    cx, cy = S // 2, S // 2
    x0, y0 = cx - cw // 2, cy - ch // 2
    x1, y1 = x0 + cw, y0 + ch
    radius = int(cw * 0.12)

    # 卡片（先描一圈极淡的边，黑底上更立体）
    d.rounded_rectangle([x0 - 2, y0 - 2, x1 + 2, y1 + 2], radius=radius + 2, fill=CARD_EDGE)
    d.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=CARD)

    # 数字：细白字，横跨中缝（翻页卡的典型形态）
    text = "12"
    font = load_font(int(ch * 0.72))
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), text, font=font, fill=DIGIT)

    # 中缝压在数字之上
    seam_h = max(scale, int(ch * 0.012))
    d.rectangle([x0, cy - seam_h // 2, x1, cy + seam_h // 2], fill=SEAM)

    return img.resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    targets = [
        ("apple-touch-icon.png", 180, False),
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable-512.png", 512, True),
    ]
    for name, size, maskable in targets:
        path = os.path.join(OUT_DIR, name)
        draw_icon(size, maskable).save(path, "PNG", optimize=True)
        print("写出 %-24s %dx%d  %6d 字节" % (name, size, size, os.path.getsize(path)))
    print("图标目录:", OUT_DIR)


if __name__ == "__main__":
    main()
