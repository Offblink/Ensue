"""SVG 导出：必须是合法 XML，且几何跟光栅版共用同一批数字。"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from sealmark import seal
from sealmark.seal import SealSpec, pixel_size, star_points
from sealmark.svg import render_svg

SVG_NS = "{http://www.w3.org/2000/svg}"


@pytest.mark.parametrize(
    "spec",
    [
        SealSpec(),
        SealSpec(kind="oval", size_mm=45, center_text="发票专用章", star=False),
        SealSpec(kind="rect", arc_text="张三印", star=False, inner_ring=True),
        SealSpec(bottom_prefix="", bottom_text="1101011001124", bottom_arc=True),
        SealSpec(arc_text='带"引号"与<尖括号>的公司', opacity=0.5),
    ],
)
def test_svg_is_well_formed(spec):
    root = ET.fromstring(render_svg(spec))
    assert root.tag.endswith("svg")
    assert root.attrib["viewBox"].startswith("0 0 ")


def test_svg_ellipse_matches_raster_geometry():
    spec = SealSpec(size_mm=40)
    root = ET.fromstring(render_svg(spec))
    ellipse = root.find(f"{SVG_NS}ellipse")
    assert ellipse is not None
    width = pixel_size(spec)[0]
    bw = spec.border_mm / spec.size_mm * width
    assert float(ellipse.attrib["rx"]) == pytest.approx(width / 2 - bw / 2, abs=0.02)
    assert float(ellipse.attrib["stroke-width"]) == pytest.approx(bw, abs=0.02)
    assert float(ellipse.attrib["cx"]) == pytest.approx(width / 2, abs=0.02)


def test_svg_star_matches_raster_star():
    spec = SealSpec(size_mm=40)
    root = ET.fromstring(render_svg(spec))
    polygon = root.find(f"{SVG_NS}polygon")
    assert polygon is not None
    width, height = pixel_size(spec)
    expected = star_points(width / 2, height / 2 + seal.STAR_CY * height, seal.STAR_R * height)
    got = [tuple(float(v) for v in pair.split(",")) for pair in polygon.attrib["points"].split()]
    assert len(got) == len(expected) == 10
    for (gx, gy), (ex, ey) in zip(got, expected, strict=False):
        assert gx == pytest.approx(ex, abs=0.02)
        assert gy == pytest.approx(ey, abs=0.02)


def test_svg_arc_text_uses_textpath_centered():
    root = ET.fromstring(render_svg(SealSpec(arc_text="示例科技有限公司")))
    path = root.find(f"{SVG_NS}path")
    text_path = root.find(f".//{SVG_NS}textPath")
    assert path is not None and text_path is not None
    assert text_path.attrib["startOffset"] == "50%"
    assert text_path.attrib["text-anchor"] == "middle"
    assert text_path.text == "示例科技有限公司"
    tokens = path.attrib["d"].split()
    assert "A" in tokens, "上弧要用椭圆弧命令"
    arc = tokens.index("A")
    assert tokens[arc + 4] == "1", "跨度超过 180° 时 large-arc-flag 必须为 1"
    assert tokens[arc + 5] == "1", "sweep 保持顺时针"


def test_svg_bottom_arc_emits_one_group_per_char():
    text = "1101011001124"
    root = ET.fromstring(render_svg(SealSpec(bottom_prefix="", bottom_text=text, bottom_arc=True)))
    groups = [g for g in root.iter(f"{SVG_NS}g") if g.attrib.get("transform", "").startswith("translate")]
    assert len(groups) == len(text)
    chars = "".join(g.find(f"{SVG_NS}text").text for g in groups)
    assert chars == text


def test_svg_escapes_user_text():
    raw = render_svg(SealSpec(arc_text="A&B<C>", center_text="合同", bottom_prefix="", bottom_text=""))
    ET.fromstring(raw)
    assert "A&amp;B&lt;C&gt;" in raw
    assert "<C>" not in raw


def test_svg_units_mm_scales_canvas():
    raw = render_svg(SealSpec(size_mm=40), units="mm")
    root = ET.fromstring(raw)
    assert root.attrib["width"].endswith("mm")
    assert float(root.attrib["width"].removesuffix("mm")) == pytest.approx(40, abs=0.01)
    view_box = root.attrib["viewBox"].split()
    assert float(view_box[2]) == pytest.approx(40, abs=0.01)
    assert float(view_box[3]) == pytest.approx(40, abs=0.01)
