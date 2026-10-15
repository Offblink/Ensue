"""把印章（或平铺文字）盖到图片 / PDF 上。

三种水印来源都归一成一张 RGBA「水印图」，后续走同一套逻辑：
缩放到目标宽度的比例 → 旋转 → 乘不透明度 → 单处摆放或平铺。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

from . import fonts

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
OUTPUT_SUFFIX = "_已盖章"


@dataclass
class StampOptions:
    layout: str = "single"  # single 单处盖章 / tile 平铺铺满
    x: float = 0.70  # 单处：印章中心相对位置
    y: float = 0.78
    width_ratio: float = 0.30  # 水印宽度 / 目标宽度
    angle: float = 0.0  # 旋转角度（度，逆时针）
    opacity: float = 1.0
    gap: float = 1.6  # 平铺间距 = 水印尺寸 × gap
    stagger: bool = True  # 平铺错行


def resolve_mark(mark: Image.Image | Callable[[int], Image.Image], width: int) -> Image.Image:
    """水印可以是现成图，也可以是「按目标宽度现画一个」的工厂。

    工厂存在的理由：细笔画的文字/印章先按固定分辨率画好、再缩到目标大小会被
    缩糊（实测 20% 灰字的有效浓度只剩一半）。按最终宽度现画就没有二次缩放。
    """
    if callable(mark):
        return mark(max(4, int(width)))
    return mark


def prepare_tile(mark: Image.Image | Callable[[int], Image.Image], target_w: int, opt: StampOptions) -> Image.Image:
    """按目标宽度缩放 + 旋转 + 乘不透明度，得到可直接贴的瓦片。"""
    w = max(6, int(round(target_w * opt.width_ratio)))
    source = resolve_mark(mark, w)
    if abs(source.width - w) > 1:
        h = max(2, int(round(source.height * w / source.width)))
        tile = source.resize((w, h), Image.LANCZOS)
    else:
        tile = source
    if abs(opt.angle) > 0.01:
        tile = tile.rotate(opt.angle, resample=Image.BICUBIC, expand=True)
    k = max(0.0, min(1.0, opt.opacity))
    if k < 0.999:
        alpha = tile.getchannel("A").point(lambda p: int(p * k))
        tile = tile.copy()
        tile.putalpha(alpha)
    return tile


def compose_overlay(size: tuple[int, int], tile: Image.Image, opt: StampOptions) -> Image.Image:
    """把瓦片摆到 (0,0)-(W,H) 的透明画布上：单处或平铺。"""
    W, H = size
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if opt.layout == "tile":
        step_x = max(4, int(tile.width * max(1.02, opt.gap)))
        step_y = max(4, int(tile.height * max(1.02, opt.gap)))
        row = 0
        y = -tile.height
        while y < H:
            offset = step_x // 2 if (opt.stagger and row % 2) else 0
            x = -tile.width + offset
            while x < W:
                layer.paste(tile, (int(x), int(y)), tile)
                x += step_x
            y += step_y
            row += 1
    else:
        cx, cy = opt.x * W, opt.y * H
        layer.paste(tile, (int(round(cx - tile.width / 2)), int(round(cy - tile.height / 2))), tile)
    return layer


def remove_white_background(img: Image.Image, threshold: int = 236, softness: int = 28) -> Image.Image:
    """把白底扫描件的白背抠成透明：亮度越高越透明，红章本体保留。"""
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    lum = ImageChops.lighter(ImageChops.lighter(r, g), b)
    lo, hi = max(1, threshold - softness), threshold
    white = lum.point(lambda p: 255 if p >= hi else (0 if p <= lo else int(255 * (p - lo) / (hi - lo))))
    img.putalpha(ImageChops.multiply(a, ImageChops.invert(white)))
    return img


def text_mark(
    text: str,
    size_px: int = 160,
    color: str = "#8A8A8A",
    font_name: str | None = None,
    opacity: float = 1.0,
) -> Image.Image:
    """一行文字做成 RGBA 水印图（透明底），供平铺用。"""
    from .seal import _hex_to_rgb

    text = text.strip()
    if not text:
        raise ValueError("水印文字为空")
    size_px = max(12, int(size_px))
    font = fonts.load_font(font_name or fonts.DEFAULT_FONT, size_px)
    width = int(fonts.measure(text, font, 0.05)) + size_px
    height = int(size_px * 1.5)
    mask = Image.new("L", (width, height), 0)
    fonts.draw_center(mask, (width / 2, height / 2), text, font, 0.05)
    rgb = _hex_to_rgb(color)
    out = Image.new("RGBA", (width, height), (*rgb, 255))
    k = max(0.0, min(1.0, opacity))
    out.putalpha(mask.point(lambda p: int(p * k)))
    return out


def parse_pages(expr: str, npages: int) -> list[int]:
    """'1-3,5' / '全部' / '' → 0 基页号列表（越界自动丢弃）。"""
    expr = (expr or "").strip()
    if not expr or expr in {"全部", "所有", "all", "*"}:
        return list(range(npages))
    picked: list[int] = []
    for part in re.split(r"[,，、;；\s]+", expr):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*(?:[-~－]\s*(\d+))?", part)
        if not m:
            raise ValueError(f"页码写法看不懂：{part!r}（示例：1-3,5）")
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        if start > end:
            start, end = end, start
        for p in range(start, end + 1):
            if 1 <= p <= npages and (p - 1) not in picked:
                picked.append(p - 1)
    if not picked:
        raise ValueError(f"页码范围超出文档（共 {npages} 页）：{expr!r}")
    return picked


def stamp_image(
    target: Image.Image, mark: Image.Image | Callable[[int], Image.Image], opt: StampOptions
) -> Image.Image:
    """在图片上盖水印，返回 RGBA（保留原图透明通道）。"""
    base = ImageOps.exif_transpose(target).convert("RGBA")
    tile = prepare_tile(mark, base.width, opt)
    return Image.alpha_composite(base, compose_overlay(base.size, tile, opt))


def _overlay_png(overlay: Image.Image) -> tuple[bytes, tuple[int, int, int, int]]:
    bbox = overlay.getchannel("A").getbbox() or (0, 0, 0, 0)
    buf = BytesIO()
    overlay.crop(bbox).save(buf, "PNG", optimize=True)
    return buf.getvalue(), bbox


def stamp_pdf(
    src: str | Path,
    dst: str | Path,
    mark: Image.Image | Callable[[int], Image.Image],
    opt: StampOptions,
    pages_expr: str = "",
    *,
    raster_dpi: int = 200,
) -> list[int]:
    """把水印叠加到 PDF 指定页（overlay，不动原文内容），返回处理过的页号。"""
    import pymupdf

    doc = pymupdf.open(str(src))
    try:
        pages = parse_pages(pages_expr, doc.page_count)
        scale = raster_dpi / 72.0
        for pno in pages:
            page = doc[pno]
            W = max(1, int(round(page.rect.width * scale)))
            H = max(1, int(round(page.rect.height * scale)))
            tile = prepare_tile(mark, W, opt)
            overlay = compose_overlay((W, H), tile, opt)
            data, bbox = _overlay_png(overlay)
            if bbox == (0, 0, 0, 0):
                continue
            rect = pymupdf.Rect(*(v / scale for v in bbox))
            page.insert_image(rect, stream=data, overlay=True, keep_proportion=False)
        doc.save(str(dst), garbage=4, deflate=True)
    finally:
        doc.close()
    return pages


def output_path(src: str | Path, out_dir: str | Path | None = None, suffix: str = OUTPUT_SUFFIX) -> Path:
    src = Path(src)
    folder = Path(out_dir) if out_dir else src.parent
    return folder / f"{src.stem}{suffix}{src.suffix}"


def stamp_file(
    src: str | Path,
    mark: Image.Image | Callable[[int], Image.Image],
    opt: StampOptions,
    *,
    out_dir: str | Path | None = None,
    pages_expr: str = "",
    suffix: str = OUTPUT_SUFFIX,
    jpeg_quality: int = 95,
) -> Path:
    """按扩展名分派：图片走 PIL，PDF 走 pymupdf。返回输出文件路径。"""
    src = Path(src)
    dst = output_path(src, out_dir, suffix)
    ext = src.suffix.lower()
    if ext == ".pdf":
        stamp_pdf(src, dst, mark, opt, pages_expr)
        return dst
    if ext not in IMAGE_SUFFIXES:
        raise ValueError(f"不支持的文件类型：{src.name}")
    with Image.open(src) as im:
        has_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        out = stamp_image(im, mark, opt)
    if ext in {".jpg", ".jpeg"} and not has_alpha:
        out.convert("RGB").save(dst, quality=jpeg_quality, subsampling=0)
    else:
        out.save(dst)
    return dst


__all__ = [
    "IMAGE_SUFFIXES",
    "OUTPUT_SUFFIX",
    "StampOptions",
    "compose_overlay",
    "output_path",
    "parse_pages",
    "prepare_tile",
    "remove_white_background",
    "resolve_mark",
    "stamp_file",
    "stamp_image",
    "stamp_pdf",
    "text_mark",
]
