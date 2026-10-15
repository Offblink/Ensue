"""导出矢量印章（SVG）：文字仍是文字，能在 Illustrator / Inkscape / 浏览器里继续改。

结构对齐用户既有的 ``Desktop\\印章水印\\印章-矢量源文件.svg``：
外圈（圆/椭圆/矩形）+ 五角星 polygon + 上弧文字 textPath + 中心行 / 编号 text。

比例与公式跟 ``seal.py`` 共用同一批常量，**改一处要同步改两处**；
``tests/test_svg.py`` 拿五角星顶点与弧端点做交叉校验，防止两边跑偏。
"""

from __future__ import annotations

import math
from html import escape

from .seal import (
    ARC_FS_MAX,
    BOTTOM_ARC_SPAN,
    BOTTOM_DY_NORM,
    BOTTOM_FS_MAX,
    CENTER_DY,
    CENTER_DY_NO_STAR,
    CENTER_FS_MAX,
    CENTER_MAX_W,
    INNER_RING_GAP,
    RING_TEXT_GAP,
    STAR_CY,
    STAR_R,
    SealSpec,
    _fit_size,
    _point,
    _wrap,
    arc_placements,
    pixel_size,
    star_points,
)

# textPath 把字形基线压在路径上，所以路径半径要比字心半径多让半个字
BASELINE_SHIFT = 0.36


def _fmt(v: float) -> str:
    out = f"{v:.2f}".rstrip("0").rstrip(".")
    return out or "0"


def _ring_offset(spec: SealSpec, W: float) -> tuple[float, float]:
    """(外圈线宽, 外圈内沿偏移)；开了内细圈时偏移含细圈厚度。"""
    bw = max(1.0, spec.border_mm / spec.size_mm * W)
    inner_edge = bw * 1.5
    if spec.inner_ring:
        inner_edge += INNER_RING_GAP * W + max(1.0, bw * 0.45)
    return bw, inner_edge


def _arc_path(cx: float, cy: float, a: float, b: float, t_start: float, t_end: float) -> str:
    """椭圆弧路径。

    跨度超过 180° 时必须给 ``large-arc-flag=1``，否则浏览器会挑另一侧的短弧
    （实测：200° 的上弧被画成 160°，弧长 964 而不是 1205，字被挤掉一半）。
    """
    x1, y1, _nx, _ny = _point(a, b, cx, cy, t_start)
    x2, y2, _nx2, _ny2 = _point(a, b, cx, cy, t_end)
    large = 1 if abs(t_end - t_start) > 180.0 else 0
    return f"M {_fmt(x1)} {_fmt(y1)} A {_fmt(a)} {_fmt(b)} 0 {large} 1 {_fmt(x2)} {_fmt(y2)}"


def _text_attrs(spec: SealSpec, fs: int) -> str:
    spacing = spec.letter_spacing * fs
    return (
        f'font-family="{escape(spec.font)}" font-size="{_fmt(fs)}" letter-spacing="{_fmt(spacing)}" fill="{spec.color}"'
    )


def _center_line(x: float, y: float, text: str, spec: SealSpec, fs: int, max_w: float) -> str:
    """横排一行字：y 落在字的垂直中心（dominant-baseline=central）。"""
    text = (text or "").strip()
    if not text:
        return ""
    fitted = _fit_size(text, spec.font, lambda _s: max_w, spec.letter_spacing, 10_000)
    fs = max(4, int(min(fitted, fs)))
    return (
        f'  <text x="{_fmt(x)}" y="{_fmt(y)}" text-anchor="middle" '
        f'dominant-baseline="central" {_text_attrs(spec, fs)}>{escape(text)}</text>\n'
    )


def _arc_line(
    spec: SealSpec,
    text: str,
    a: float,
    b: float,
    cx: float,
    cy: float,
    t0: float,
    t1: float,
    fs_max: float,
    scale: float,
) -> tuple[str, int]:
    """弧排一行字：上弧（t 递减）用 textPath 排；下弧（t 递增）textPath 摆不出「字顶朝圆心」，逐字 rotate。

    两种情况都用 ``seal.arc_placements`` 算出来的同一批位置/字号，免得两个渲染器各写一套。
    """
    text = text.strip()
    if not text:
        return "", 0
    inward = t1 > t0
    placements, fs = arc_placements(
        text, a, b, cx, cy, t0, t1, spec.font, spec.letter_spacing, scale, fs_max, inward=inward
    )
    if not placements:
        return "", 0
    if not inward:
        r_path = a - fs * 0.62 + BASELINE_SHIFT * fs
        b_path = b - fs * 0.62 + BASELINE_SHIFT * fs
        pid = "arcTop"
        return (
            f'  <path id="{pid}" d="{_arc_path(cx, cy, r_path, b_path, t0, t1)}" fill="none"/>\n'
            f"  <text {_text_attrs(spec, fs)}>"
            f'<textPath href="#{pid}" xlink:href="#{pid}" startOffset="50%" '
            f'text-anchor="middle">{escape(text)}</textPath></text>\n'
        ), fs
    out = []
    for item in placements:  # SVG 的 rotate 是顺时针为正，PIL 的是逆时针为正
        out.append(
            f'  <g transform="translate({_fmt(item.x)} {_fmt(item.y)}) rotate({_fmt(-item.rot)})">\n'
            f'    <text text-anchor="middle" dominant-baseline="central" '
            f"{_text_attrs(spec, fs)}>{escape(item.char)}</text>\n  </g>\n"
        )
    return "".join(out), fs


def _number_text(spec: SealSpec) -> str:
    if not (spec.bottom_prefix.strip() or spec.bottom_text.strip()):
        return ""
    return f"{spec.bottom_prefix.strip()}{spec.bottom_text.strip()}"


def _rect_body(spec: SealSpec, W: float, H: float, bw: float) -> str:
    body = spec.arc_text.strip() or spec.center_text.strip()
    inner_w = W - bw * 3
    inner_h = H - bw * 3
    out: list[str] = []
    if body:
        lines = _wrap(body, spec.columns)
        cols = max(len(ln) for ln in lines)
        rows = len(lines)
        fs = _fit_size("字" * cols, spec.font, lambda _s: inner_w * 0.86, spec.letter_spacing, int(H * 0.8))
        fs = min(fs, int(inner_h * 0.80 / (rows * 1.18)))
        fs = max(4, int(fs * spec.center_scale))
        step = fs * 1.18
        y0 = H / 2 - step * (rows - 1) / 2
        for i, line in enumerate(lines):
            out.append(_center_line(W / 2, y0 + i * step, line, spec, fs, inner_w))
        number_y = min(H - bw * 2.2, y0 + (rows - 1) * step + fs * 0.7 + H * 0.09)
    else:
        number_y = H * 0.5
    out.append(_center_line(W / 2, number_y, _number_text(spec), spec, int(BOTTOM_FS_MAX * W), inner_w * 0.9))
    return "".join(out)


def _bottom_svg(spec: SealSpec, W: float, H: float, bw: float, cx: float, cy: float) -> str:
    text = _number_text(spec)
    if not text:
        return ""
    off = bw * 1.5 + RING_TEXT_GAP * W
    a_in, b_in = W / 2 - off, H / 2 - off
    fs_max = BOTTOM_FS_MAX * W
    if spec.bottom_arc:
        half = min(BOTTOM_ARC_SPAN / 2, 40.0)
        out, _fs = _arc_line(spec, text, a_in, b_in, cx, cy, 270 - half, 270 + half, fs_max, spec.bottom_scale)
        return out
    dy = BOTTOM_DY_NORM * b_in
    ratio = min(0.995, dy / b_in)
    half_w = a_in * math.sqrt(max(0.0, 1 - ratio * ratio)) * 0.94
    return _center_line(cx, cy + dy, text, spec, int(fs_max), 2 * half_w)


def render_svg(spec: SealSpec, *, units: str = "px") -> str:
    """渲染成 SVG 字符串；``units='mm'`` 时画布按毫米出图（可直接印）。"""
    if units == "mm":
        vw = float(spec.size_mm)
        vh = vw if spec.kind == "round" else vw * spec.aspect
    else:
        vw, vh = (float(v) for v in pixel_size(spec))
    cx, cy = vw / 2, vh / 2
    bw, inner_edge = _ring_offset(spec, vw)
    body: list[str] = []

    if spec.kind == "rect":
        body.append(
            f'  <rect x="{_fmt(bw / 2)}" y="{_fmt(bw / 2)}" '
            f'width="{_fmt(vw - bw)}" height="{_fmt(vh - bw)}" fill="none" '
            f'stroke="{spec.color}" stroke-width="{_fmt(bw)}"/>\n'
        )
        if spec.inner_ring:
            ix, iy = bw * 1.5, bw * 1.5
            body.append(
                f'  <rect x="{_fmt(ix)}" y="{_fmt(iy)}" width="{_fmt(vw - 2 * ix)}" '
                f'height="{_fmt(vh - 2 * iy)}" fill="none" stroke="{spec.color}" '
                f'stroke-width="{_fmt(max(1.0, bw * 0.4))}"/>\n'
            )
        body.append(_rect_body(spec, vw, vh, bw))
    else:
        rx, ry = vw / 2 - bw / 2, vh / 2 - bw / 2
        body.append(
            f'  <ellipse cx="{_fmt(cx)}" cy="{_fmt(cy)}" rx="{_fmt(rx)}" ry="{_fmt(ry)}" '
            f'fill="none" stroke="{spec.color}" stroke-width="{_fmt(bw)}"/>\n'
        )
        if spec.inner_ring:
            body.append(
                f'  <ellipse cx="{_fmt(cx)}" cy="{_fmt(cy)}" rx="{_fmt(rx - inner_edge)}" '
                f'ry="{_fmt(ry - inner_edge)}" fill="none" stroke="{spec.color}" '
                f'stroke-width="{_fmt(max(1.0, bw * 0.45))}"/>\n'
            )
        a_in = rx - inner_edge - RING_TEXT_GAP * vw
        b_in = ry - inner_edge - RING_TEXT_GAP * vw
        arc, _fs = _arc_line(
            spec,
            spec.arc_text,
            a_in,
            b_in,
            cx,
            cy,
            90 + spec.arc_span_deg / 2,
            90 - spec.arc_span_deg / 2,
            ARC_FS_MAX * vw,
            spec.arc_scale,
        )
        body.append(arc)
        if spec.star:
            pts = star_points(cx, cy + STAR_CY * vh, STAR_R * vh)
            body.append(
                '  <polygon points="' + " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in pts) + f'" fill="{spec.color}"/>\n'
            )
            center_y = cy + CENTER_DY * vh
        else:
            center_y = cy + CENTER_DY_NO_STAR * vh
        body.append(_center_line(cx, center_y, spec.center_text, spec, int(CENTER_FS_MAX * vw), CENTER_MAX_W * vw))
        body.append(_bottom_svg(spec, vw, vh, bw, cx, cy))

    if spec.opacity < 0.999:
        body.insert(0, f'  <g opacity="{_fmt(spec.opacity)}">\n')
        body.append("  </g>\n")
    head = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {_fmt(vw)} {_fmt(vh)}" width="{_fmt(vw)}{units}" '
        f'height="{_fmt(vh)}{units}">\n'
        f"  <!-- 印章矢量源文件：{_fmt(spec.size_mm)}mm；文字可直接编辑"
        f"（外圈弧排字 / 中心行 / 底部编号） -->\n"
    )
    return head + "".join(body) + "</svg>\n"


__all__ = ["render_svg"]
