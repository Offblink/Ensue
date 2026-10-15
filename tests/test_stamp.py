"""加盖水印：页码解析、瓦片几何、图片合成、PDF 叠加（文字层必须还在）。"""

from __future__ import annotations

import pytest
from PIL import Image

from ensue.seal import SealSpec, render
from ensue.stamp import (
    StampOptions,
    compose_overlay,
    output_path,
    parse_pages,
    prepare_tile,
    remove_white_background,
    resolve_mark,
    stamp_file,
    stamp_image,
    stamp_pdf,
    text_mark,
)


@pytest.fixture(scope="module")
def mark() -> Image.Image:
    return render(SealSpec(aging=0.0, blur=0.0), target_px=240)


def make_pdf(path, pages: int = 3) -> str:
    import pymupdf

    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page()
        page.insert_text((72, 120), f"CONTRACT PAGE {index + 1}", fontsize=20)
        page.insert_text((72, 160), "Party A: Example Co., Ltd.", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


# --------------------------------------------------------------------- 页码
@pytest.mark.parametrize(
    ("expr", "npages", "expected"),
    [
        ("", 3, [0, 1, 2]),
        ("全部", 3, [0, 1, 2]),
        ("2", 3, [1]),
        ("1-3,5", 5, [0, 1, 2, 4]),
        ("3-1", 3, [0, 1, 2]),
        ("1、2", 3, [0, 1]),
        ("9", 3, "error"),
        ("1-x", 3, "error"),
    ],
)
def test_parse_pages(expr, npages, expected):
    if expected == "error":
        with pytest.raises(ValueError):
            parse_pages(expr, npages)
    else:
        assert parse_pages(expr, npages) == expected


# --------------------------------------------------------------------- 瓦片
def test_prepare_tile_scales_and_fades(mark):
    tile = prepare_tile(mark, 1000, StampOptions(width_ratio=0.25, angle=0.0, opacity=0.5))
    assert abs(tile.width - 250) <= 2
    assert max(tile.getchannel("A").getdata()) <= 130


def test_prepare_tile_rotates_with_expanded_bounds(mark):
    """旋转会换外接框：30° 时外接框 = 边长 ×(cos30+sin30)。"""
    tile = prepare_tile(mark, 1000, StampOptions(width_ratio=0.25, angle=30.0, opacity=1.0))
    assert abs(tile.width - 250 * (0.866 + 0.5)) <= 3


def test_callable_mark_is_drawn_at_the_final_size():
    """工厂水印按瓦片宽度现画：细笔画不该被缩糊（固定 140px 再缩到 300px 只剩一半浓度）。"""

    def factory(width: int) -> Image.Image:
        return text_mark("仅供存档参考", size_px=max(10, int(width / 7.2)), color="#8C8C8C")

    tile = prepare_tile(factory, 1200, StampOptions(width_ratio=0.25, angle=0.0, opacity=1.0))
    assert abs(tile.width - 300) <= 2
    assert max(tile.getchannel("A").getdata()) > 240


def test_resolve_mark_passes_through_plain_images(mark):
    assert resolve_mark(mark, 300) is mark
    widths = []
    resolve_mark(lambda w: widths.append(w) or mark, 321)
    assert widths == [321]


def test_compose_overlay_single_lands_where_asked(mark):
    opt = StampOptions(layout="single", x=0.7, y=0.78, width_ratio=0.3, opacity=1.0, angle=0)
    overlay = compose_overlay((1000, 1400), prepare_tile(mark, 1000, opt), opt)
    bbox = overlay.getchannel("A").getbbox()
    assert bbox is not None
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    assert abs(cx - 700) <= 12 and abs(cy - 0.78 * 1400) <= 12


def test_compose_overlay_tile_covers_more(mark):
    base = {"width_ratio": 0.2, "opacity": 1.0, "angle": 0.0}
    single = compose_overlay((800, 800), prepare_tile(mark, 800, StampOptions(**base)), StampOptions(**base))
    tiled_opt = StampOptions(**base, layout="tile", gap=1.2, stagger=True)
    tiled = compose_overlay((800, 800), prepare_tile(mark, 800, tiled_opt), tiled_opt)
    assert len(tiled.getchannel("A").getbbox()) == 4
    lit_single = sum(1 for v in single.getchannel("A").getdata() if v > 40)
    lit_tiled = sum(1 for v in tiled.getchannel("A").getdata() if v > 40)
    assert lit_tiled > lit_single * 4


# --------------------------------------------------------------------- 图片
def test_stamp_image_keeps_size_and_only_paints_the_mark(mark):
    photo = Image.new("RGB", (900, 600), (240, 244, 250))
    opt = StampOptions(layout="single", x=0.2, y=0.25, width_ratio=0.25, angle=0, opacity=1.0)
    out = stamp_image(photo, mark, opt)
    assert out.size == photo.size
    assert out.getpixel((880, 560))[:3] == (240, 244, 250), "没盖到的地方不能动"
    tile = prepare_tile(mark, out.width, opt)
    overlay = compose_overlay(out.size, tile, opt)
    bbox = overlay.getchannel("A").getbbox()
    assert bbox is not None
    lit = sum(1 for v in overlay.getchannel("A").getdata() if v > 0)
    before = photo.crop(bbox)
    after = out.crop(bbox)
    changed = sum(1 for a, b in zip(after.getdata(), before.getdata(), strict=False) if a[:3] != b[:3])
    assert changed >= 0.95 * lit, "水印覆盖到的像素应该都变了，且不多不少"


def test_stamp_file_writes_next_to_the_source(tmp_path, mark):
    src = tmp_path / "合同.png"
    Image.new("RGB", (400, 300), "white").save(src)
    dst = stamp_file(src, mark, StampOptions(width_ratio=0.4))
    assert dst == output_path(src)
    assert dst.exists() and dst.name == "合同_已盖章.png"
    assert src.exists(), "原文件必须留着"
    with Image.open(dst) as out:
        assert out.size == (400, 300)


def test_stamp_file_rejects_unknown_type(tmp_path, mark):
    src = tmp_path / "notes.txt"
    src.write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError):
        stamp_file(src, mark, StampOptions())


def test_remove_white_background_keeps_the_red_ink():
    img = Image.new("RGB", (10, 10), "white")
    img.putpixel((5, 5), (200, 30, 30))
    out = remove_white_background(img)
    assert out.getpixel((0, 0))[3] == 0
    assert out.getpixel((5, 5))[3] == 255


def test_text_mark_has_ink():
    img = text_mark("仅供存档参考", size_px=80)
    assert img.mode == "RGBA"
    assert max(img.getchannel("A").getdata()) > 200
    assert img.width > img.height


# ----------------------------------------------------------------------- PDF
def test_stamp_pdf_single_page_keeps_text_layer_and_geometry(tmp_path, mark):
    import pymupdf

    src = make_pdf(tmp_path / "in.pdf", 3)
    dst = tmp_path / "out.pdf"
    opt = StampOptions(layout="single", x=0.7, y=0.78, width_ratio=0.30, angle=0.0, opacity=1.0)
    pages = stamp_pdf(src, dst, mark, opt, pages_expr="2")
    assert pages == [1]

    with pymupdf.open(dst) as doc:
        assert doc.page_count == 3
        assert "CONTRACT PAGE 2" in doc[1].get_text(), "PDF 文字层不能被光栅化掉"
        assert "CONTRACT PAGE 1" in doc[0].get_text()
        assert doc[0].get_images() == [], "没点名的页不能被动过"
        infos = doc[1].get_image_info()
        assert len(infos) == 1
        bbox = infos[0]["bbox"]
        page = doc[1].rect
        width = 0.30 * page.width
        assert abs((bbox[0] + bbox[2]) / 2 - 0.70 * page.width) <= 4
        assert abs((bbox[1] + bbox[3]) / 2 - 0.78 * page.height) <= 4
        assert abs((bbox[2] - bbox[0]) - width) <= 6


def test_stamp_pdf_tile_uses_one_overlay_per_page(tmp_path, mark):
    import pymupdf

    src = make_pdf(tmp_path / "in.pdf", 2)
    dst = tmp_path / "tiled.pdf"
    opt = StampOptions(layout="tile", width_ratio=0.2, angle=30, opacity=0.2, gap=1.8)
    stamp_pdf(src, dst, mark, opt)
    with pymupdf.open(dst) as doc:
        for page in doc:
            infos = page.get_image_info()
            assert len(infos) == 1, "平铺只该插一张覆盖层"
            assert infos[0]["bbox"][2] - infos[0]["bbox"][0] > page.rect.width * 0.8


def test_stamp_pdf_bad_pages_leaves_no_output(tmp_path, mark):
    src = make_pdf(tmp_path / "in.pdf", 2)
    dst = tmp_path / "out.pdf"
    with pytest.raises(ValueError):
        stamp_pdf(src, dst, mark, StampOptions(), pages_expr="7")
