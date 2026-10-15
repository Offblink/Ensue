"""印章渲染：把几行文字排成圆形公章 / 椭圆形章 / 长方形章，输出透明底 RGBA。

设计要点
- **墨迹蒙版**：所有笔画先画在单通道 ``L`` 蒙版上，最后统一上色。做旧、模糊、
  墨色浓度都只动蒙版，不用逐像素碰颜色。
- **比例排版**：文字半径、星的大小、文字位置全部按章体宽度 ``D`` 的比例给，
  所以把尺寸从 40mm 改到 20mm，版式不会走形（真实印章排版参数也是这么定的）。
- **超采样**：4 倍画布 + LANCZOS 缩回，圆环、五角星、逐字旋转的弧排字
  都靠这一步拿到平滑边缘（PIL 的 polygon 本身不抗锯齿）。
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import NamedTuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import fonts

MM_PER_INCH = 25.4
SUPERSAMPLE = 4

# ---- 版式比例（相对章体宽度 D）----
# 这组数字是从「Desktop\印章水印\印章-矢量源文件.svg」与用户给的实拍章样张上量出来的：
# 外圈 18/480=0.0375D、星半径 68/480=0.142D、弧排字与中心行 50/480=0.104D，
# 中心行落在 0.5R 处、弧排字基线在 0.733R 处。
RING_TEXT_GAP = 0.030  # 外圈内沿 → 弧排文字外沿
INNER_RING_GAP = 0.022  # 外圈内沿 → 内细圈外沿
STAR_CY = -0.070  # 五角星中心（实拍样张里星略高于章心）
STAR_R = 0.145  # 五角星外接半径
CENTER_DY = 0.210  # 星下那行文字的中心（有星时）
CENTER_DY_NO_STAR = 0.020
CENTER_MAX_W = 0.560
CENTER_FS_MAX = 0.115
ARC_FS_MAX = 0.115  # 弧排文字字号上限
BOTTOM_FS_MAX = 0.080  # 底部编号字号上限
BOTTOM_DY_NORM = 0.80  # 编号行位置 = 0.80 × 内圈可用半高
BOTTOM_ARC_SPAN = 80.0  # 编号走弧排时的跨度（度）；超过 80° 会蹭到中心那行字

COLOR_PRESETS: tuple[tuple[str, str], ...] = (
    ("印泥红", "#E60012"),
    ("朱砂红", "#E34234"),
    ("深红", "#B01B1B"),
    ("藏青", "#1F4E9C"),
    ("墨黑", "#222222"),
)

CENTER_PRESETS = ("合同专用章", "财务专用章", "发票专用章", "诊断专用章", "人事专用章", "公章")


@dataclass
class SealSpec:
    """一枚印章的全部可调参数（可 JSON 往返）。"""

    kind: str = "round"  # round 圆形 / oval 椭圆 / rect 长方形
    arc_text: str = "示例科技有限公司"  # 外圈弧排文字（椭圆同理）
    center_text: str = "合同专用章"  # 星下那一行
    bottom_prefix: str = "(22)"  # 编号前缀，如 (22)
    bottom_text: str = ""  # 底部编号
    bottom_arc: bool = False  # 编号沿下弧排列（不勾=横排）
    star: bool = True
    size_mm: float = 40.0  # 圆=直径，椭圆/长方形=宽度
    aspect: float = 0.68  # 椭圆/长方形的高宽比
    columns: int = 0  # 长方形每行字数，0=自动
    color: str = "#E60012"
    density: float = 1.0  # 墨色浓度 0.3~1.0
    font: str = fonts.DEFAULT_FONT
    border_mm: float = 1.5
    inner_ring: bool = False
    arc_span_deg: float = 200.0  # 弧排文字跨度上限
    arc_scale: float = 1.0  # 弧排字号 % （相对自动适配字号）
    center_scale: float = 1.0
    bottom_scale: float = 1.0
    letter_spacing: float = 0.08
    aging: float = 0.20  # 做旧强度
    blur: float = 0.30  # 边缘柔化半径（像素）
    opacity: float = 1.0  # 整体不透明度
    seed: int = 2026
    dpi: int = 600

    def clone(self, **patch) -> SealSpec:
        return SealSpec(**{**asdict(self), **patch})


# --------------------------------------------------------------------------- #
# 几何
# --------------------------------------------------------------------------- #
def pixel_size(spec: SealSpec) -> tuple[int, int]:
    """印章的成品像素尺寸（省 dpi 前的原始尺寸）。"""
    w = max(24, int(round(spec.size_mm / MM_PER_INCH * spec.dpi)))
    if spec.kind == "round":
        return w, w
    return w, max(12, int(round(w * spec.aspect)))


def _point(a: float, b: float, cx: float, cy: float, t: float) -> tuple[float, float, float, float]:
    """椭圆参数 t（度，0=正右，逆时针）上的点与朝外法线（图像坐标系，y 向下）。"""
    r = math.radians(t)
    ct, st = math.cos(r), math.sin(r)
    px, py = cx + a * ct, cy - b * st
    nx, ny = b * ct, -a * st
    norm = math.hypot(nx, ny) or 1.0
    return px, py, nx / norm, ny / norm


def _arc_table(a: float, b: float, cx: float, cy: float, t0: float, t1: float, n: int = 720):
    """把一段椭圆弧采样成 (点, 累计弧长, 总长, 法线) 表，供按弧长排字用。"""
    pts = [_point(a, b, cx, cy, t0 + (t1 - t0) * i / n) for i in range(n + 1)]
    acc = [0.0]
    for i in range(1, len(pts)):
        acc.append(acc[-1] + math.dist(pts[i][:2], pts[i - 1][:2]))
    return pts, acc, acc[-1]


def _arc_at(table, s: float):
    """按弧长取点（线性插值），s 会被钳在 [0, 总长]。"""
    pts, acc, total = table
    s = max(0.0, min(total, s))
    lo, hi = 0, len(acc) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if acc[mid] <= s:
            lo = mid
        else:
            hi = mid
    span = acc[hi] - acc[lo]
    f = 0.0 if span <= 0 else (s - acc[lo]) / span
    p0, p1 = pts[lo], pts[hi]
    return (
        p0[0] + (p1[0] - p0[0]) * f,
        p0[1] + (p1[1] - p0[1]) * f,
        p0[2] + (p1[2] - p0[2]) * f,
        p0[3] + (p1[3] - p0[3]) * f,
    )


def _fit_size(text: str, font_name: str, max_len: Callable[[int], float], spacing: float, hi: int) -> int:
    """二分出「排版长度 ≤ max_len(字号)」的最大字号；max_len 随字号单调收紧。"""
    if not text:
        return 1
    lo, hi = 1, max(2, hi)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if fonts.measure(text, fonts.load_font(font_name, mid), spacing) <= max_len(mid):
            lo = mid
        else:
            hi = mid - 1
    return lo


def _glyph_tile(ch: str, font) -> Image.Image:
    """单字蒙版：固定 em 方框、字形居中——保证每个字旋转时绕同一个中心。"""
    side = max(2, int(round(font.size * 1.7)))
    tile = Image.new("L", (side, side), 0)
    ImageDraw.Draw(tile).text((side / 2, side / 2), ch, font=font, fill=255, anchor="mm")
    return tile


def _paste_char(ink: Image.Image, ch: str, font, pos, rot: float) -> None:
    tile = _glyph_tile(ch, font)
    if abs(rot) > 0.01:
        tile = tile.rotate(rot, resample=Image.BICUBIC, expand=True)
    ink.paste(tile, (int(round(pos[0] - tile.width / 2)), int(round(pos[1] - tile.height / 2))), tile)


def _ring(ink: Image.Image, box: tuple[float, float, float, float], width: float) -> None:
    """用两个实心椭圆相减画圆环（比 draw.ellipse(width=) 的内外沿可控）。"""
    draw = ImageDraw.Draw(ink)
    x0, y0, x1, y1 = box
    draw.ellipse((x0, y0, x1, y1), fill=255)
    inner = (x0 + width, y0 + width, x1 - width, y1 - width)
    if inner[2] > inner[0] and inner[3] > inner[1]:
        draw.ellipse(inner, fill=0)


def star_points(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    """五角星的 10 个顶点（外接半径 r、内半径 0.382r、一个角朝正上）。"""
    ri = r * 0.382
    pts = []
    for i in range(10):
        rad = r if i % 2 == 0 else ri
        ang = math.radians(-90 + i * 36)
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    return pts


def _star(ink: Image.Image, cx: float, cy: float, r: float) -> None:
    ImageDraw.Draw(ink).polygon(star_points(cx, cy, r), fill=255)


class CharPlacement(NamedTuple):
    """弧排里一个字的位置：字心坐标、旋转角（度）、字号。"""

    char: str
    x: float
    y: float
    rot: float
    size: int


def arc_placements(
    text: str,
    a: float,
    b: float,
    cx: float,
    cy: float,
    t0: float,
    t1: float,
    font_name: str,
    spacing: float,
    scale: float,
    fs_max: float,
    *,
    inward: bool = False,
    jitter: float = 0.0,
    rng: random.Random | None = None,
) -> tuple[list[CharPlacement], int]:
    """沿椭圆弧排一行字的纯计算部分：按弧长定字心，按法线定字朝向。

    ``t0 → t1`` 的方向决定阅读方向：上弧要 **t 递减**（左→顶→右），
    下弧要 **t 递增**（左下→底→右下），否则整行字会镜像（这个坑真踩过）。
    """
    text = text.strip()
    if not text:
        return [], 0
    table = lambda fs: _arc_table(a - fs * 0.62, b - fs * 0.62, cx, cy, t0, t1)  # noqa: E731
    if table(1)[2] <= 1:
        return [], 0
    fs = _fit_size(text, font_name, lambda s: table(s)[2], spacing, int(fs_max))
    fs = max(4, int(fs * scale))
    pts, acc, total = table(fs)
    font = fonts.load_font(font_name, fs)
    width = fonts.measure(text, font, spacing)
    gap = spacing * fs
    s = max(0.0, (total - width) / 2)
    out: list[CharPlacement] = []
    for ch in text:
        adv = font.getlength(ch)
        px, py, nx, ny = _arc_at((pts, acc, total), s + adv / 2)
        # 弧排字的「上」方向 = 朝外法线（inward 时取反向，用于下弧）
        upx, upy = (-nx, -ny) if inward else (nx, ny)
        rot = math.degrees(math.atan2(-upy, upx)) - 90
        if jitter > 0 and rng is not None:
            rot += rng.uniform(-1.6, 1.6) * jitter
            px += rng.uniform(-1.0, 1.0) * jitter * fs * 0.03
            py += rng.uniform(-1.0, 1.0) * jitter * fs * 0.03
        out.append(CharPlacement(ch, px, py, rot, fs))
        s += adv + gap
    return out, fs


def draw_placements(ink: Image.Image, placements: list[CharPlacement], font_name: str) -> None:
    """把弧排结果画到墨迹蒙版上。"""
    for item in placements:
        _paste_char(ink, item.char, fonts.load_font(font_name, item.size), (item.x, item.y), item.rot)


def _draw_arc_text(
    ink: Image.Image,
    text: str,
    a: float,
    b: float,
    cx: float,
    cy: float,
    t0: float,
    t1: float,
    font_name: str,
    spacing: float,
    scale: float,
    inward: bool,
    jitter: float,
    rng: random.Random,
    fs_max: float,
) -> None:
    placements, _fs = arc_placements(
        text, a, b, cx, cy, t0, t1, font_name, spacing, scale, fs_max, inward=inward, jitter=jitter, rng=rng
    )
    draw_placements(ink, placements, font_name)


# --------------------------------------------------------------------------- #
# 三种章体的绘制
# --------------------------------------------------------------------------- #
def _draw_round(ink: Image.Image, spec: SealSpec, D: float, rng: random.Random) -> None:
    c = D / 2
    bw = max(1.0, spec.border_mm / spec.size_mm * D)
    _ring(ink, (bw / 2, bw / 2, D - bw / 2, D - bw / 2), bw)
    inner_edge = bw * 1.5  # 外圈内沿相对章边的偏移
    if spec.inner_ring:
        thin = max(1.0, bw * 0.45)
        off = inner_edge + INNER_RING_GAP * D
        _ring(ink, (off, off, D - off, D - off), thin)
        inner_edge = off + thin

    jitter = spec.aging
    r_text = D / 2 - inner_edge - RING_TEXT_GAP * D
    # 上弧必须 t 递减（左 → 顶 → 右），否则整行字镜像
    _draw_arc_text(
        ink,
        spec.arc_text,
        r_text,
        r_text,
        c,
        c,
        90 + spec.arc_span_deg / 2,
        90 - spec.arc_span_deg / 2,
        spec.font,
        spec.letter_spacing,
        spec.arc_scale,
        False,
        jitter,
        rng,
        fs_max=ARC_FS_MAX * D,
    )

    if spec.star:
        _star(ink, c, c + STAR_CY * D, STAR_R * D)
        center_y = c + CENTER_DY * D
    else:
        center_y = c + CENTER_DY_NO_STAR * D
    _draw_line(
        ink,
        spec.center_text,
        spec.font,
        spec.center_scale,
        spec.letter_spacing,
        (c, center_y),
        CENTER_MAX_W * D,
        CENTER_FS_MAX * D,
        rng,
        jitter,
    )
    _draw_bottom(ink, spec, c, c, D / 2, D / 2, bw, rng, jitter)


def _draw_oval(ink: Image.Image, spec: SealSpec, W: float, H: float, rng: random.Random) -> None:
    c, cy = W / 2, H / 2
    a, b = W / 2, H / 2
    bw = max(1.0, spec.border_mm / spec.size_mm * W)
    _ring(ink, (bw / 2, bw / 2, W - bw / 2, H - bw / 2), bw)
    inner_edge = bw * 1.5
    if spec.inner_ring:
        thin = max(1.0, bw * 0.45)
        off = inner_edge + INNER_RING_GAP * W
        _ring(ink, (off, off, W - off, H - off), thin)
        inner_edge = off + thin

    jitter = spec.aging
    off = inner_edge + RING_TEXT_GAP * W
    _draw_arc_text(
        ink,
        spec.arc_text,
        a - off,
        b - off,
        c,
        cy,
        90 + spec.arc_span_deg / 2,
        90 - spec.arc_span_deg / 2,
        spec.font,
        spec.letter_spacing,
        spec.arc_scale,
        False,
        jitter,
        rng,
        fs_max=ARC_FS_MAX * W,
    )
    if spec.star:
        _star(ink, c, cy + STAR_CY * H, STAR_R * H)
        center_y = cy + CENTER_DY * H
    else:
        center_y = cy + CENTER_DY_NO_STAR * H
    _draw_line(
        ink,
        spec.center_text,
        spec.font,
        spec.center_scale,
        spec.letter_spacing,
        (c, center_y),
        CENTER_MAX_W * W,
        CENTER_FS_MAX * W,
        rng,
        jitter,
    )
    _draw_bottom(ink, spec, c, cy, a, b, bw, rng, jitter)


def _draw_rect(ink: Image.Image, spec: SealSpec, W: float, H: float, rng: random.Random) -> None:
    bw = max(1.0, spec.border_mm / spec.size_mm * W)
    draw = ImageDraw.Draw(ink)
    draw.rectangle((bw / 2, bw / 2, W - bw / 2, H - bw / 2), fill=255)
    inner = (bw * 1.5, bw * 1.5, W - bw * 1.5, H - bw * 1.5)
    if spec.inner_ring and inner[2] > inner[0] and inner[3] > inner[1]:
        thin = max(1.0, bw * 0.4)
        draw.rectangle((inner[0], inner[1], inner[2], inner[3]), fill=255)
        draw.rectangle((inner[0] + thin, inner[1] + thin, inner[2] - thin, inner[3] - thin), fill=0)
    else:
        draw.rectangle(inner, fill=0)

    body = spec.arc_text.strip() or spec.center_text.strip()
    if not body:
        _draw_rect_number(ink, spec, W, H, bw, H * 0.5, rng)
        return
    lines = _wrap(body, spec.columns)
    avail_w = (W - 2 * bw * 1.5) * 0.86
    avail_h = (H - 2 * bw * 1.5) * 0.80
    cols = max(len(ln) for ln in lines)
    rows = len(lines)
    fs = _fit_size("字" * cols, spec.font, lambda _s: avail_w, spec.letter_spacing, int(H * 0.8))
    fs = min(fs, int(avail_h / (rows * 1.18)))
    fs = max(4, int(fs * spec.center_scale))
    font = fonts.load_font(spec.font, fs)
    step = fs * 1.18
    y0 = H / 2 - step * (rows - 1) / 2
    for i, line in enumerate(lines):
        fonts.draw_center(ink, (W / 2, y0 + i * step), line, font, spec.letter_spacing)
    _draw_rect_number(ink, spec, W, H, bw, y0 + (rows - 1) * step + fs * 0.7, rng)


def _draw_rect_number(
    ink, spec: SealSpec, W: float, H: float, bw: float, body_bottom: float, rng: random.Random
) -> None:
    """长方形章的编号：只有填了编号才画，位置压在正文下方、不越框。"""
    if not (spec.bottom_prefix.strip() or spec.bottom_text.strip()):
        return
    text = f"{spec.bottom_prefix.strip()}{spec.bottom_text.strip()}"
    y = min(H - bw * 2.2, body_bottom + H * 0.09)
    _draw_line(
        ink,
        text,
        spec.font,
        spec.bottom_scale,
        spec.letter_spacing,
        (W / 2, y),
        W - bw * 6,
        BOTTOM_FS_MAX * W,
        rng,
        0.0,
    )


def _wrap(text: str, columns: int) -> list[str]:
    """长方形章的分行：columns=0 时自动（≤4 字按方阵，多字按近似正方形）。"""
    text = text.replace("\n", "").replace(" ", "")
    if not text:
        return []
    n = len(text)
    if n == 1:
        cols = 1
    elif columns and columns > 0:
        cols = columns
    elif n <= 4:
        cols = 2
    else:
        cols = int(math.ceil(math.sqrt(n)))
    return [text[i : i + cols] for i in range(0, n, cols)]


def _draw_line(
    ink,
    text,
    font_name,
    scale,
    spacing,
    xy,
    max_w,
    fs_max,
    rng,
    jitter,
) -> None:
    """横排一行字：字号先适配给定宽度，再受上限与用户缩放约束。"""
    text = (text or "").strip()
    if not text:
        return
    fs = _fit_size(text, font_name, lambda _s: max_w, spacing, 10_000)
    fs = max(4, int(min(fs, fs_max) * scale))
    font = fonts.load_font(font_name, fs)
    x, y = xy
    if jitter > 0:
        x += rng.uniform(-1, 1) * jitter * fs * 0.02
        y += rng.uniform(-1, 1) * jitter * fs * 0.02
    fonts.draw_center(ink, (x, y), text, font, spacing)


def _draw_bottom(ink, spec: SealSpec, cx, cy, a, b, bw, rng, jitter) -> None:
    """底部编号：(22)1101011001124 这种，可横排也可沿下弧。

    横排时宽度按「该高度上的章内弦长」算，所以编号再长也不会顶出外圈。
    """
    text = f"{spec.bottom_prefix.strip()}{spec.bottom_text.strip()}"
    if not text:
        return
    W = 2 * a
    off = bw * 1.5 + RING_TEXT_GAP * W
    a_in, b_in = a - off, b - off
    if a_in <= 4 or b_in <= 4:
        return
    fs_max = BOTTOM_FS_MAX * W
    if spec.bottom_arc:
        # 下弧：t 递增 = 左下 → 底 → 右下（字顶朝圆心）
        half = min(BOTTOM_ARC_SPAN / 2, 40.0)
        _draw_arc_text(
            ink,
            text,
            a_in,
            b_in,
            cx,
            cy,
            270 - half,
            270 + half,
            spec.font,
            spec.letter_spacing,
            spec.bottom_scale,
            True,
            0.0,
            rng,
            fs_max=fs_max,
        )
        return
    dy = BOTTOM_DY_NORM * b_in
    ratio = min(0.995, dy / b_in)
    half_w = a_in * math.sqrt(max(0.0, 1 - ratio * ratio)) * 0.94
    _draw_line(
        ink, text, spec.font, spec.bottom_scale, spec.letter_spacing, (cx, cy + dy), 2 * half_w, fs_max, rng, jitter
    )


# --------------------------------------------------------------------------- #
# 做旧与上色
# --------------------------------------------------------------------------- #
def _noise(size: tuple[int, int], small: tuple[int, int], rng: random.Random, resample) -> Image.Image:
    img = Image.frombytes("L", small, bytes(rng.randrange(256) for _ in range(small[0] * small[1])))
    return img.resize(size, resample)


def apply_aging(ink: Image.Image, spec: SealSpec) -> Image.Image:
    """让墨迹不那么"干净"：墨量不均 → 缺墨颗粒 → 边缘飞溅 → 柔化。"""
    a = max(0.0, min(1.0, spec.aging))
    if a <= 0.001 and spec.blur <= 0.001:
        return ink
    W, H = ink.size
    rng = random.Random(spec.seed)

    if a > 0.001:
        # 低频墨量不均（印泥厚薄）
        low = _noise((W, H), (max(2, W // 48), max(2, H // 48)), rng, Image.BICUBIC)
        floor = int(255 * (1 - 0.34 * a))
        ink = ImageChops.multiply(ink, low.point(lambda p: floor + int((255 - floor) * p / 255)))

        # 缺墨颗粒：约 (0.2%~2%) 的像素被吃掉
        holes = _noise((W, H), (max(2, W // 2), max(2, H // 2)), rng, Image.NEAREST)
        thr = max(1, int(255 * 0.020 * a))
        ink = ImageChops.multiply(ink, holes.point(lambda p: 0 if p > 255 - thr else 255))

        # 边缘飞溅：把笔画外侧一圈稀薄墨点提亮
        band = ink.filter(ImageFilter.GaussianBlur(1.6)).point(lambda p: 255 if 12 < p < 190 else 0)
        sprink = _noise((W, H), (max(2, W // 3), max(2, H // 3)), rng, Image.NEAREST)
        dots = ImageChops.multiply(band, sprink.point(lambda p: 255 if p > 255 - int(10 * a) else 0))
        ink = ImageChops.lighter(ink, dots.point(lambda p: int(p * 0.55)))

    if spec.blur > 0.001:
        ink = ink.filter(ImageFilter.GaussianBlur(spec.blur))
    return ink


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    c = color.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        raise ValueError(f"颜色值不合法: {color!r}")
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def colorize(ink: Image.Image, color: str, density: float, opacity: float) -> Image.Image:
    r, g, b = _hex_to_rgb(color)
    k = max(0.0, min(1.0, density)) * max(0.0, min(1.0, opacity))
    alpha = ink.point(lambda p: int(p * k))
    out = Image.new("RGBA", ink.size, (r, g, b, 255))
    out.putalpha(alpha)
    return out


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def render_mask(spec: SealSpec, *, target_px: int | None = None) -> Image.Image:
    """画墨迹蒙版（L 通道）。target_px 用于预览：按最长边缩到该像素内再画。"""
    W, H = pixel_size(spec)
    if target_px and max(W, H) > target_px:
        k = target_px / max(W, H)
        W, H = max(16, int(round(W * k))), max(16, int(round(H * k)))
    ss = SUPERSAMPLE
    ink = Image.new("L", (W * ss, H * ss), 0)
    rng = random.Random(spec.seed)
    if spec.kind == "round":
        _draw_round(ink, spec, W * ss, rng)
    elif spec.kind == "oval":
        _draw_oval(ink, spec, W * ss, H * ss, rng)
    elif spec.kind == "rect":
        _draw_rect(ink, spec, W * ss, H * ss, rng)
    else:
        raise ValueError(f"未知章体类型: {spec.kind!r}")
    ink = ink.resize((W, H), Image.LANCZOS)
    return apply_aging(ink, spec)


def render(spec: SealSpec, *, target_px: int | None = None) -> Image.Image:
    """渲染一枚印章（RGBA，透明底）。"""
    ink = render_mask(spec, target_px=target_px)
    return colorize(ink, spec.color, spec.density, spec.opacity)


def render_png_bytes(spec: SealSpec, *, target_px: int | None = None) -> bytes:
    from io import BytesIO

    buf = BytesIO()
    render(spec, target_px=target_px).save(buf, "PNG")
    return buf.getvalue()
