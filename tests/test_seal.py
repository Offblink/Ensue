"""印章渲染的核心不变量：尺寸、透明底、确定性、弧排方向、不溢出。"""

from __future__ import annotations

import pytest
from PIL import Image

from sealmark import seal
from sealmark.seal import SealSpec, arc_placements, render


def ink_ratio(img: Image.Image, threshold: int = 40) -> float:
    alpha = img.getchannel("A")
    lit = sum(1 for v in alpha.getdata() if v > threshold)
    return lit / (img.width * img.height)


def alpha_stats(img: Image.Image) -> tuple[int, float]:
    hist = img.getchannel("A").histogram()
    total = sum(hist)
    max_alpha = max(i for i, count in enumerate(hist) if count)
    mean = sum(i * count for i, count in enumerate(hist)) / total
    return max_alpha, mean


def test_pixel_size_follows_mm_and_dpi():
    assert seal.pixel_size(SealSpec(size_mm=40, dpi=600)) == (945, 945)
    oval = seal.pixel_size(SealSpec(kind="oval", size_mm=45, aspect=0.68, dpi=600))
    assert oval[0] == 1063
    assert abs(oval[1] - oval[0] * 0.68) <= 1


def test_render_is_transparent_and_has_ink():
    img = render(SealSpec())
    assert img.mode == "RGBA"
    assert img.size == seal.pixel_size(SealSpec())
    for corner in ((0, 0), (img.width - 1, 0), (0, img.height - 1), (img.width - 1, img.height - 1)):
        assert img.getpixel(corner)[3] == 0, "四角必须完全透明"
    assert 0.10 < ink_ratio(img) < 0.60


def test_target_px_limits_preview_size():
    img = render(SealSpec(kind="oval", size_mm=45, aspect=0.68), target_px=200)
    assert max(img.size) == 200


def test_same_seed_is_deterministic_and_seed_changes_texture():
    spec = SealSpec(aging=0.6)
    first = render(spec).tobytes()
    assert render(spec).tobytes() == first
    other = render(spec.clone(seed=spec.seed + 1)).tobytes()
    assert other != first


def test_density_scales_alpha_and_color_is_used():
    spec = SealSpec(color="#1F4E9C", density=0.5, aging=0.0, blur=0.0)
    img = render(spec)
    max_alpha, _mean = alpha_stats(img)
    assert 120 <= max_alpha <= 135, "浓度 50% 时最实的墨应该在 127 上下"
    solid = max(
        (img.getpixel((x, y)) for y in range(0, img.height, 7) for x in range(0, img.width, 7)),
        key=lambda px: px[3],
    )
    assert solid[:3] == (0x1F, 0x4E, 0x9C)


def test_aging_erodes_ink():
    clean = render(SealSpec(aging=0.0, blur=0.0))
    worn = render(SealSpec(aging=1.0, blur=0.0))
    assert alpha_stats(worn)[1] < alpha_stats(clean)[1], "做旧只会让墨更淡/更碎"


def test_empty_texts_still_draw_a_ring():
    spec = SealSpec(arc_text="", center_text="", bottom_prefix="", bottom_text="", star=True)
    img = render(spec)
    assert 0.02 < ink_ratio(img) < 0.20


def test_long_text_never_leaves_the_canvas():
    """弧排字与底部编号都曾经顶出外圈/画布，这里钉住「不外溢」。"""
    spec = SealSpec(
        arc_text="某某省某某市某某区某某街道办事处综合服务中心",
        center_text="合同专用章",
        bottom_prefix="(22)",
        bottom_text="1101011001124999",
    )
    img = render(spec)
    bbox = img.getchannel("A").point(lambda v: 255 if v > 40 else 0).getbbox()
    assert bbox is not None
    left, top, right, bottom = bbox
    assert left >= 0 and top >= 0 and right <= img.width and bottom <= img.height
    assert left <= img.width * 0.06 and right >= img.width * 0.94, "外圈要完整"


def test_top_arc_reads_left_to_right():
    """上弧 t 递减才不是镜像：字心 x 必须随字数单调递增，中间那个字要立正。"""
    d = 945.0
    inner = d / 2 - 35.44 * 1.5 - seal.RING_TEXT_GAP * d
    placements, size = arc_placements(
        "示例科技有限公司",
        inner,
        inner,
        d / 2,
        d / 2,
        90 + 100,
        90 - 100,
        "宋体",
        0.08,
        1.0,
        seal.ARC_FS_MAX * d,
    )
    assert size > 40
    xs = [p.x for p in placements]
    assert xs == sorted(xs), "字心 x 单调递增 = 阅读方向正确"
    middle = placements[len(placements) // 2]
    assert abs(middle.rot) < 15, "中间那个字应该基本立正"
    assert placements[0].rot > 30 and placements[-1].rot < -30, "两端朝外张开"


def test_bottom_arc_tilts_the_other_way():
    """下弧编号字顶要朝圆心，旋转方向跟上弧相反。"""
    d = 945.0
    radius = d / 2 - 35.44 * 1.5 - seal.RING_TEXT_GAP * d
    placements, _size = arc_placements(
        "1101011001124",
        radius,
        radius,
        d / 2,
        d / 2,
        270 - 40,
        270 + 40,
        "宋体",
        0.08,
        1.0,
        seal.BOTTOM_FS_MAX * d,
        inward=True,
    )
    xs = [p.x for p in placements]
    assert xs == sorted(xs)
    assert placements[0].rot < -5 and placements[-1].rot > 5


@pytest.mark.parametrize(
    ("text", "columns", "expected"),
    [
        ("张三", 0, ["张三"]),
        ("张三印", 0, ["张三", "印"]),
        ("张三印信", 0, ["张三", "印信"]),
        ("甲乙丙丁戊", 0, ["甲乙丙", "丁戊"]),
        ("张三印", 1, ["张", "三", "印"]),
    ],
)
def test_rect_wrapping(text, columns, expected):
    assert seal._wrap(text, columns) == expected


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        render(SealSpec(kind="triangle"))
