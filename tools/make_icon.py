"""生成 assets/icon.ico —— 直接拿印章渲染器画一枚小章当图标（改参数重跑即可）。

用法：python tools/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sealmark.seal import SealSpec, render

SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> int:
    spec = SealSpec(
        kind="round",
        arc_text="",
        center_text="",
        bottom_prefix="",
        bottom_text="",
        star=True,
        inner_ring=True,
        border_mm=2.8,  # 小尺寸下描边粗一点才看得清
        aging=0.0,
        blur=0.18,
        dpi=600,
    )
    big = render(spec, target_px=1024)
    icon = ROOT / "assets" / "icon.ico"
    icon.parent.mkdir(parents=True, exist_ok=True)
    big.save(icon, sizes=SIZES)
    print(f"{icon} {icon.stat().st_size} B，{len(SIZES)} 档尺寸")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
