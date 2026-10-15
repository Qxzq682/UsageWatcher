# -*- coding: utf-8 -*-
"""AI 生成的图标 PNG → assets/app_icon.ico（多尺寸）。
水印清除：四角 160x160 内的近白像素 → 透明（水印在角落白底区，
表盘白色在中心不受影响）。运行一次即可，产物进 spec datas。"""
import os
import sys

from PIL import Image

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets",
                   "Flat_minimal_app_icon__rounded_2026-10-15T02-52-53.png")
DST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets",
                   "app_icon.ico")
CORNER = 160        # 四角处理区
WHITE = 242         # 近白阈值

img = Image.open(SRC).convert("RGBA")
img = img.crop((12, 12, img.width - 12, img.height - 12))
px = img.load()
w, h = img.size
# 圆角几何裁切：到最近圆心距离 > 半径的像素（圆角方外）一律透明 —— 顺带清掉
# 角落白底上的 AI 水印（灰色，近白阈值抓不到）
R = 192
for yy in range(h):
    cy = min(max(yy, R), h - R)
    for xx in range(w):
        cx = min(max(xx, R), w - R)
        if (xx - cx) ** 2 + (yy - cy) ** 2 > R * R:
            r, g, b, a = px[xx, yy]
            px[xx, yy] = (r, g, b, 0)
sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
img.save(DST, format="ICO", sizes=sizes)
print("saved:", os.path.abspath(DST), os.path.getsize(DST), "bytes")
