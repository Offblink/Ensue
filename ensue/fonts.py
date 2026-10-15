"""本机可用中文字体的探测与取用（印章主要用宋体/仿宋）。

只用 Windows 自带字体，不打包字体文件；探测不到的项直接从列表里去掉。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING

from PIL import ImageFont

if TYPE_CHECKING:  # pragma: no cover
    from PIL import Image

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

# (显示名, 文件名, TrueType Collection 索引)
CANDIDATES: list[tuple[str, str, int]] = [
    ("宋体", "simsun.ttc", 0),
    ("仿宋", "simfang.ttf", 0),
    ("黑体", "simhei.ttf", 0),
    ("楷体", "simkai.ttf", 0),
    ("微软雅黑", "msyh.ttc", 0),
    ("等线", "Deng.ttf", 0),
    ("隶书", "SIMLI.TTF", 0),
    ("幼圆", "SIMYOU.TTF", 0),
    ("方正舒体", "FZSTK.TTF", 0),
    ("方正姚体", "FZYTK.TTF", 0),
    ("新宋体", "simsun.ttc", 1),
]

DEFAULT_FONT = "宋体"
FALLBACK_FONTS = ("宋体", "黑体", "微软雅黑", "等线")


@lru_cache(maxsize=1)
def available_fonts() -> tuple[tuple[str, str, int], ...]:
    """本机真实存在且 PIL 能加载的字体表。"""
    usable: list[tuple[str, str, int]] = []
    for label, filename, index in CANDIDATES:
        path = os.path.join(FONT_DIR, filename)
        if not os.path.exists(path):
            continue
        try:
            ImageFont.truetype(path, 32, index=index)
        except OSError:
            continue
        usable.append((label, path, index))
    if not usable:  # 极端情况：连宋体都没有，退回 PIL 内置位图字体
        raise RuntimeError(f"未在 {FONT_DIR} 找到任何可用中文字体")
    return tuple(usable)


def font_labels() -> list[str]:
    return [label for label, _p, _i in available_fonts()]


def resolve_font_name(name: str) -> str:
    """把任意输入归一到本机存在的字体名。"""
    labels = font_labels()
    if name in labels:
        return name
    for fb in FALLBACK_FONTS:
        if fb in labels:
            return fb
    return labels[0]


@lru_cache(maxsize=256)
def load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """按显示名取字体对象；大小会被就地取整并下限 1。"""
    size = max(1, int(round(size)))
    name = resolve_font_name(name)
    for label, path, index in available_fonts():
        if label == name:
            return ImageFont.truetype(path, size, index=index)
    raise RuntimeError(name)  # pragma: no cover - resolve_font_name 已保证


def measure(text: str, font: ImageFont.FreeTypeFont, spacing: float = 0.0) -> float:
    """逐字累加的排版宽度；spacing 是字距（相对字号的比例）。"""
    if not text:
        return 0.0
    gap = spacing * font.size
    return sum(font.getlength(ch) for ch in text) + gap * (len(text) - 1)


def draw_center(
    ink: Image.Image,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    spacing: float = 0.0,
    anchor_vertical: str = "center",
) -> None:
    """把一行文字逐字画出，整体以 xy 为水平中心。

    逐字画是为了拿到字距控制；PIL 的 ``draw.text(spacing=)`` 只对多行生效。
    """
    from PIL import ImageDraw

    draw = ImageDraw.Draw(ink)
    total = measure(text, font, spacing)
    x = xy[0] - total / 2
    gap = spacing * font.size
    for ch in text:
        draw.text((x, xy[1]), ch, font=font, fill=255, anchor="lm" if anchor_vertical == "center" else "la")
        x += font.getlength(ch) + gap
