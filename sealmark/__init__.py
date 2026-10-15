"""印章包：印章渲染 + 加盖水印。GUI 之外的部分不依赖 Qt，可单独脚本化使用。"""

from __future__ import annotations

from .seal import COLOR_PRESETS, SealSpec, render, render_mask, render_png_bytes
from .stamp import (
    IMAGE_SUFFIXES,
    StampOptions,
    parse_pages,
    stamp_file,
    stamp_image,
    stamp_pdf,
    text_mark,
)

__all__ = [
    "COLOR_PRESETS",
    "IMAGE_SUFFIXES",
    "SealSpec",
    "StampOptions",
    "parse_pages",
    "render",
    "render_mask",
    "render_png_bytes",
    "stamp_file",
    "stamp_image",
    "stamp_pdf",
    "text_mark",
]
__version__ = "0.1.0"
