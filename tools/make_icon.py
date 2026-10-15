"""生成 assets/icon.ico 与 assets/ensue-logo.png（品牌图处理，改图标时重跑）。

源图放 Downloads 里（带 AI 生成水印、白底），处理三步：
1. 用「饱和度」找 logo 外接框 —— 顺便把右下角的水印裁在外面；
2. 从四角泛洪抠白底 —— 只去掉与边缘连通的白，logo 内部的浅色高光不动；
3. 居中放进正方形画布，导出多档 .ico 与一张干净的大图。

用法：python tools/make_icon.py [源图路径]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
DEFAULT_SOURCE = Path.home() / "Downloads" / "ensue图标设计（无文字）.png"
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
CLEAN_PX = 1024  # 仓库里存的干净大图边长
FILL_RATIO = 0.92  # logo 在画布里的占比（留点呼吸）
WHITEISH = 22  # 与白的距离小于它就算「近白」，用于泛洪种子


def color_bbox(img: Image.Image, sat_min: int = 40) -> tuple[int, int, int, int]:
    """彩色部分的外接框：白底与灰色水印都不算。"""
    a = np.asarray(img.convert("RGB")).astype(int)
    colorful = (a.max(2) - a.min(2)) > sat_min
    ys, xs = np.where(colorful)
    if not len(xs):
        raise ValueError("图里找不到彩色内容，确认下源图")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _connected_to_border(mask: np.ndarray) -> np.ndarray:
    """mask 里与画面边框连通的部分（4 邻域迭代膨胀，纯 numpy）。

    不用 ``ImageDraw.floodfill``：Pillow 10.4 上它对本机这种 L 模式图是空操作
    （最小复现：40×40 白底中间一个黑块，填完 128 的像素数 = 0），别踩。
    """
    reach = np.zeros_like(mask)
    reach[0, :] |= mask[0, :]
    reach[-1, :] |= mask[-1, :]
    reach[:, 0] |= mask[:, 0]
    reach[:, -1] |= mask[:, -1]
    while True:
        grown = reach.copy()
        grown[1:, :] |= reach[:-1, :]
        grown[:-1, :] |= reach[1:, :]
        grown[:, 1:] |= reach[:, :-1]
        grown[:, :-1] |= reach[:, 1:]
        grown &= mask
        if grown.sum() == reach.sum():
            return grown
        reach = grown


def cut_white_background(img: Image.Image) -> Image.Image:
    """抠掉与边缘连通的白底；被颜色包住的浅色高光不动。"""
    rgba = img.convert("RGBA")
    rgb = np.asarray(rgba.convert("RGB")).astype(int)
    near_white = (255 - rgb.min(2)) <= WHITEISH
    background = _connected_to_border(near_white)
    alpha = np.asarray(rgba.getchannel("A")).copy()
    alpha[background] = 0
    rgba.putalpha(Image.fromarray(alpha))
    return rgba


def square_canvas(logo: Image.Image, side: int, fill: float = FILL_RATIO) -> Image.Image:
    """把 logo 等比缩到 side×fill，居中放到 side 见方的透明画布上。"""
    inner = max(1, int(round(side * fill)))
    k = min(inner / logo.width, inner / logo.height)
    resized = logo.resize((max(1, int(round(logo.width * k))), max(1, int(round(logo.height * k)))), Image.LANCZOS)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(resized, ((side - resized.width) // 2, (side - resized.height) // 2), resized)
    return canvas


def main(source: Path | None = None) -> int:
    src = Path(source) if source else DEFAULT_SOURCE
    if not src.exists():
        print(f"找不到源图：{src}", file=sys.stderr)
        return 1
    with Image.open(src) as raw:
        img = raw.convert("RGB")
    box = color_bbox(img)
    print(f"源图 {img.size}，logo 外接框 {box}（水印在其外，已被裁掉）")
    logo = cut_white_background(img.crop(box))
    alpha_box = logo.getchannel("A").getbbox()
    if alpha_box:
        logo = logo.crop(alpha_box)

    ASSETS.mkdir(parents=True, exist_ok=True)
    clean = square_canvas(logo, CLEAN_PX)
    clean_path = ASSETS / "ensue-logo.png"
    clean.save(clean_path)

    icon_path = ASSETS / "icon.ico"
    canvas = square_canvas(logo, max(w for w, _h in SIZES))
    canvas.save(icon_path, sizes=SIZES)

    # 预览条：各档尺寸放大到同一高度并排，方便肉眼核对小尺寸没糊
    plate = 256 + 24
    gap = 16
    strip = Image.new("RGB", (gap + len(SIZES) * (plate + gap), 300), (246, 246, 246))
    draw = ImageDraw.Draw(strip)
    x = gap
    for size, _h in SIZES:
        thumb = canvas.resize((size, size), Image.LANCZOS).resize((256, 256), Image.NEAREST)
        tile = Image.new("RGBA", (plate, plate), (246, 246, 246, 255))
        tile.paste(thumb, (12, 12), thumb)
        strip.paste(tile.convert("RGB"), (x, 14))
        draw.text((x + plate // 2 - 8, 276), f"{size}", fill=(120, 120, 120))
        x += plate + gap
    strip.save(ASSETS / "icon-preview.png")
    print(f"{icon_path} {icon_path.stat().st_size} B / {clean_path} {clean_path.stat().st_size} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else None))
