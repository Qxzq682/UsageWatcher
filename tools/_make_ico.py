# -*- coding: utf-8 -*-
r"""把 AI 生成的图标 PNG 加工成可用的 app_icon.ico / app_icon.png。

输入图的问题（实测）：background=transparent 没生效——四周是烤进像素里的
灰色棋盘格（alpha=255），右下角还有生成器水印。本脚本：
  1. 按色彩判别抠图：彩色像素（深青/绿/白刻度）保留，灰色棋盘（R≈G≈B 且
     120<R<220）置透明；纯白 255 不在灰区间，白刻度不受影响。
  2. 右下角水印矩形强制置透明（该区域在徽章外，安全）。
  3. 裁剪到内容 bounding box，补成正方形画布。
  4. 输出 app_icon.png（512）与 app_icon.ico（256/128/64/48/32/16）。
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = next((ROOT / "assets").glob("Flat_modern_app_icon_*.png"))
PNG_OUT = ROOT / "assets" / "app_icon.png"
ICO_OUT = ROOT / "assets" / "app_icon.ico"


def is_grayish(px: tuple) -> bool:
    r, g, b = px[0], px[1], px[2]
    return abs(r - g) < 24 and abs(g - b) < 24 and 120 < r < 225


def main() -> None:
    img = Image.open(SRC).convert("RGBA")
    px = img.load()
    w, h = img.size

    # 水印矩形（右下角，徽章外）先整体置透明
    for y in range(930, h):
        for x in range(760, w):
            px[x, y] = (0, 0, 0, 0)

    # 颜色抠图：灰棋盘 -> 透明
    for y in range(h):
        for x in range(w):
            p = px[x, y]
            if p[3] != 0 and is_grayish(p):
                px[x, y] = (0, 0, 0, 0)

    bbox = img.getbbox()   # 非零（含 alpha>0）内容的边界
    img = img.crop(bbox)

    # 补成正方形（短边两侧加透明），内容居中
    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.size[0]) // 2, (side - img.size[1]) // 2))

    canvas.resize((512, 512), Image.LANCZOS).save(PNG_OUT)
    canvas.save(ICO_OUT, sizes=[(256, 256), (128, 128), (64, 64),
                                (48, 48), (32, 32), (16, 16)])
    # 小尺寸预览（人眼核对 16/32 是否可辨）
    canvas.resize((32, 32), Image.LANCZOS).save(ROOT / "assets" / "_preview_32.png")
    print("OK:", PNG_OUT.name, ICO_OUT.name,
          "ico 大小:", ICO_OUT.stat().st_size, "字节")


if __name__ == "__main__":
    main()
