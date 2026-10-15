"""印章制作页：左边调参数，右边纸上预览，可导出 PNG / SVG / 复制到剪贴板。"""

from __future__ import annotations

import random
import re
import time
from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    DoubleSpinBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SingleDirectionScrollArea,
    SpinBox,
    SubtitleLabel,
)

from .. import fonts, specs
from ..seal import CENTER_PRESETS, COLOR_PRESETS, SealSpec, pixel_size, render, render_png_bytes
from ..svg import render_svg
from .widgets import ColorRow, LabeledRow, PaperPreview, SliderRow, block_wheel, card, hint, pil_to_pixmap

PREVIEW_PX = 360
SEAL_TYPES: tuple[tuple[str, str], ...] = (
    ("圆形公章", "round"),
    ("椭圆形章", "oval"),
    ("长方形章", "rect"),
)


def _safe_name(text: str, fallback: str = "印章") -> str:
    name = re.sub(r'[\\/:*?"<>|\s]+', "", (text or "").strip())
    return (name or fallback)[:24]


class SealPage(QWidget):
    """印章参数页。``spec()`` 给加盖页取当前印章。"""

    specChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sealPage")
        self._spec = SealSpec()
        self._loading = False
        self._toast = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(90)
        self._preview_timer.timeout.connect(self._refresh)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(2500)
        self._save_timer.timeout.connect(self._remember)
        self._build()
        self._bind()
        remembered = specs.load_settings().get("seal")
        if isinstance(remembered, dict):
            self._apply_spec(specs.spec_from_dict(remembered))
        else:
            self._apply_spec(self._spec)
        self._refresh()

    # ------------------------------------------------------------------ 界面
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(20)
        root.addWidget(self._build_controls(), 0)
        root.addWidget(self._build_preview(), 1)

    def _build_controls(self) -> QWidget:
        holder = QWidget(self)
        panel = QVBoxLayout(holder)
        panel.setContentsMargins(0, 0, 12, 0)
        panel.setSpacing(12)

        self.type_box = ComboBox()
        self.type_box.addItems([label for label, _k in SEAL_TYPES])
        self.arc_edit = LineEdit()
        self.arc_edit.setPlaceholderText("沿上弧排列，例如：示例科技有限公司")
        self.arc_edit.setClearButtonEnabled(True)
        self.center_edit = LineEdit()
        self.center_edit.setPlaceholderText("星下那一行，例如：合同专用章")
        self.center_edit.setClearButtonEnabled(True)
        self.center_preset = ComboBox()
        self.center_preset.addItems(("常用…", *CENTER_PRESETS))
        self.star_check = CheckBox("章心加五角星")

        box1 = card(
            "印章文字",
            LabeledRow("类型", self.type_box),
            LabeledRow("外圈文字", self.arc_edit),
            LabeledRow("中心文字", self.center_edit),
            LabeledRow("常用中心文字", self.center_preset),
            self.star_check,
            hint("外圈文字沿上弧排；中心文字就是实拍章里星下那一行，可以留空。"),
        )

        self.prefix_edit = LineEdit()
        self.prefix_edit.setPlaceholderText("如 (22)")
        self.prefix_edit.setMaximumWidth(120)
        self.number_edit = LineEdit()
        self.number_edit.setPlaceholderText("如 1101011001124，可留空")
        self.number_edit.setClearButtonEnabled(True)
        self.number_arc = CheckBox("编号沿下弧排")
        self.clear_number = PushButton("清空编号")
        number_row = QWidget()
        nlay = QHBoxLayout(number_row)
        nlay.setContentsMargins(0, 0, 0, 0)
        nlay.setSpacing(8)
        nlay.addWidget(BodyLabel("前缀"))
        nlay.addWidget(self.prefix_edit)
        nlay.addWidget(self.number_edit, 1)
        box2 = card(
            "底部编号",
            number_row,
            self.number_arc,
            self.clear_number,
            hint("公章的编号一般形如 (22)1101011001124；不想要编号就整行留空。"),
        )

        self.size_spin = DoubleSpinBox()
        self.size_spin.setRange(10, 90)
        self.size_spin.setSingleStep(1)
        self.size_spin.setSuffix(" mm")
        self.aspect_spin = DoubleSpinBox()
        self.aspect_spin.setRange(0.40, 1.60)
        self.aspect_spin.setSingleStep(0.02)
        self.aspect_spin.setDecimals(2)
        self.aspect_spin.setSuffix(" 高/宽")
        self.border_spin = DoubleSpinBox()
        self.border_spin.setRange(0.2, 4.0)
        self.border_spin.setSingleStep(0.1)
        self.border_spin.setDecimals(2)
        self.border_spin.setSuffix(" mm")
        self.inner_check = CheckBox("加一圈内细线（双圈章）")
        self.font_box = ComboBox()
        self.font_box.addItems(fonts.font_labels())
        self.span_slider = SliderRow("文字跨度", 120, 320, 200, 5, "°")
        self.arange = SliderRow("外圈字号", 40, 140, 100, 5, "%")
        self.centerrange = SliderRow("中心字号", 40, 140, 100, 5, "%")
        self.numberrange = SliderRow("编号字号", 40, 160, 100, 5, "%")
        self.spacing_slider = SliderRow("字距", 0, 30, 8, 1, "%")
        self.columns_spin = SpinBox()
        self.columns_spin.setRange(0, 6)
        self.columns_spin.setSuffix(" 字/行（0=自动）")
        box3 = card(
            "版式",
            LabeledRow("外径/宽度", self.size_spin),
            LabeledRow("高宽比", self.aspect_spin),
            LabeledRow("边框粗细", self.border_spin),
            LabeledRow("长方形排布", self.columns_spin),
            LabeledRow("字体", self.font_box),
            self.inner_check,
            self.span_slider,
            self.arange,
            self.centerrange,
            self.numberrange,
            self.spacing_slider,
        )

        self.color_row = ColorRow(COLOR_PRESETS, self._spec.color)
        self.density_slider = SliderRow("墨色浓度", 30, 100, 100, 5, "%")
        self.aging_slider = SliderRow("做旧强度", 0, 100, 20, 5, "%")
        self.blur_slider = SliderRow("边缘柔化", 0, 150, 30, 5)
        self.reroll_btn = PushButton("换一种做旧")
        box4 = card(
            "颜色与质感",
            LabeledRow("印泥颜色", self.color_row, label_width=76),
            self.density_slider,
            self.aging_slider,
            self.blur_slider,
            self.reroll_btn,
            hint("做旧会随机吃掉一点墨、加些飞溅，像真盖上去的；要干净的矢量感就拖到 0。"),
        )

        panel.addWidget(box1)
        panel.addWidget(box2)
        panel.addWidget(box3)
        panel.addWidget(box4)
        panel.addStretch(1)

        # 数值/下拉一律不吃滚轮（页面滚动时最容易误触的就是它们）
        block_wheel(
            self.type_box,
            self.center_preset,
            self.font_box,
            self.size_spin,
            self.aspect_spin,
            self.border_spin,
            self.columns_spin,
        )
        scroll = SingleDirectionScrollArea(orient=Qt.Vertical)
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.enableTransparentBackground()
        scroll.setFixedWidth(430)
        scroll.setFrameShape(QFrame.NoFrame)
        return scroll

    def _build_preview(self) -> QWidget:
        holder = QWidget(self)
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(SubtitleLabel("预览"))
        head.addStretch(1)
        self.size_caption = CaptionLabel()
        head.addWidget(self.size_caption)
        lay.addLayout(head)
        self.preview = PaperPreview()
        lay.addWidget(self.preview, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.export_png_btn = PrimaryPushButton(FluentIcon.SAVE, "导出 PNG")
        self.export_svg_btn = PushButton(FluentIcon.SHARE, "导出 SVG")
        self.copy_btn = PushButton(FluentIcon.COPY, "复制图片")
        self.save_btn = PushButton("保存方案")
        self.load_btn = PushButton("打开方案")
        for btn in (self.export_png_btn, self.export_svg_btn, self.copy_btn, self.save_btn, self.load_btn):
            buttons.addWidget(btn)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        lay.addWidget(hint("PNG 是透明底，可直接叠到文件上。复制到剪贴板后能粘进 Word / WPS。"))
        return holder

    def _bind(self) -> None:
        self.type_box.currentIndexChanged.connect(self._on_change)
        for edit in (self.arc_edit, self.center_edit, self.prefix_edit, self.number_edit):
            edit.textChanged.connect(self._on_change)
        self.number_edit.editingFinished.connect(self._on_change)
        self.center_preset.currentIndexChanged.connect(self._on_center_preset)
        self.star_check.toggled.connect(self._on_change)
        self.number_arc.toggled.connect(self._on_change)
        self.clear_number.clicked.connect(self._clear_number)
        self.size_spin.valueChanged.connect(self._on_change)
        self.aspect_spin.valueChanged.connect(self._on_change)
        self.border_spin.valueChanged.connect(self._on_change)
        self.inner_check.toggled.connect(self._on_change)
        self.font_box.currentIndexChanged.connect(self._on_change)
        self.columns_spin.valueChanged.connect(self._on_change)
        for slider in (
            self.span_slider,
            self.arange,
            self.centerrange,
            self.numberrange,
            self.spacing_slider,
            self.density_slider,
            self.aging_slider,
            self.blur_slider,
        ):
            slider.valueChanged.connect(self._on_change)
        self.color_row.colorChanged.connect(self._on_change)
        self.reroll_btn.clicked.connect(self._reroll)
        self.export_png_btn.clicked.connect(self._export_png)
        self.export_svg_btn.clicked.connect(self._export_svg)
        self.copy_btn.clicked.connect(self._copy)
        self.save_btn.clicked.connect(self._save_preset)
        self.load_btn.clicked.connect(self._load_preset)

    # ------------------------------------------------------------- 状态同步
    def spec(self) -> SealSpec:
        return self._spec

    def _on_center_preset(self, index: int) -> None:
        if index > 0:
            self.center_edit.setText(self.center_preset.itemText(index))
            self.center_preset.setCurrentIndex(0)

    def _clear_number(self) -> None:
        self.prefix_edit.setText("")
        self.number_edit.setText("")

    def _reroll(self) -> None:
        self._spec.seed = random.randrange(1, 1 << 30)
        self._refresh()

    def _on_change(self, *_args) -> None:
        if self._loading:
            return
        kind = SEAL_TYPES[max(0, self.type_box.currentIndex())][1]
        self._spec = self._spec.clone(
            kind=kind,
            arc_text=self.arc_edit.text(),
            center_text=self.center_edit.text(),
            bottom_prefix=self.prefix_edit.text(),
            bottom_text=self.number_edit.text(),
            bottom_arc=self.number_arc.isChecked(),
            star=self.star_check.isChecked(),
            size_mm=self.size_spin.value(),
            aspect=self.aspect_spin.value(),
            columns=self.columns_spin.value(),
            color=self.color_row.color(),
            density=self.density_slider.value() / 100.0,
            font=self.font_box.currentText(),
            border_mm=self.border_spin.value(),
            inner_ring=self.inner_check.isChecked(),
            arc_span_deg=self.span_slider.value(),
            arc_scale=self.arange.value() / 100.0,
            center_scale=self.centerrange.value() / 100.0,
            bottom_scale=self.numberrange.value() / 100.0,
            letter_spacing=self.spacing_slider.value() / 100.0,
            aging=self.aging_slider.value() / 100.0,
            blur=self.blur_slider.value() / 100.0,
        )
        self._sync_enabled()
        self._preview_timer.start()
        self._save_timer.start()

    def _sync_enabled(self) -> None:
        shaped = self._spec.kind != "round"
        self.aspect_spin.setEnabled(shaped)
        self.columns_spin.setEnabled(self._spec.kind == "rect")
        self.number_arc.setEnabled(self._spec.kind == "round")
        if self._spec.kind != "round" and self.number_arc.isChecked():
            self.number_arc.setChecked(False)

    def _apply_spec(self, spec: SealSpec) -> None:
        """把一份参数灌回界面（不再触发 _on_change 的连环刷新）。"""
        self._loading = True
        try:
            self._spec = spec
            labels = [k for _l, k in SEAL_TYPES]
            self.type_box.setCurrentIndex(labels.index(spec.kind) if spec.kind in labels else 0)
            self.arc_edit.setText(spec.arc_text)
            self.center_edit.setText(spec.center_text)
            self.prefix_edit.setText(spec.bottom_prefix)
            self.number_edit.setText(spec.bottom_text)
            self.number_arc.setChecked(spec.bottom_arc)
            self.star_check.setChecked(spec.star)
            self.size_spin.setValue(spec.size_mm)
            self.aspect_spin.setValue(spec.aspect)
            self.columns_spin.setValue(spec.columns)
            self.border_spin.setValue(spec.border_mm)
            self.inner_check.setChecked(spec.inner_ring)
            self.font_box.setCurrentText(fonts.resolve_font_name(spec.font))
            self.span_slider.setValue(spec.arc_span_deg)
            self.arange.setValue(spec.arc_scale * 100)
            self.centerrange.setValue(spec.center_scale * 100)
            self.numberrange.setValue(spec.bottom_scale * 100)
            self.spacing_slider.setValue(spec.letter_spacing * 100)
            self.density_slider.setValue(spec.density * 100)
            self.aging_slider.setValue(spec.aging * 100)
            self.blur_slider.setValue(spec.blur * 100)
            self.color_row.set_color(spec.color)
        finally:
            self._loading = False
        self._sync_enabled()

    def _refresh(self) -> None:
        started = time.perf_counter()
        img = render(self._spec, target_px=PREVIEW_PX)
        self.preview.set_image(img)
        full_w, full_h = pixel_size(self._spec)
        cost = (time.perf_counter() - started) * 1000
        self.size_caption.setText(
            f"{full_w} × {full_h} px（{self._spec.size_mm:g}mm @{self._spec.dpi}dpi）"
            f" · 预览 {img.width}px · {cost:.0f}ms"
        )
        self.specChanged.emit()

    def _remember(self) -> None:
        specs.save_settings(seal=specs.spec_to_dict(self._spec))

    # ------------------------------------------------------------------ 导出
    def _default_stem(self) -> str:
        return _safe_name(self._spec.center_text or self._spec.arc_text)

    def _pick_path(self, suffix: str, filter_text: str) -> str:
        start = Path.home() / "Desktop" / f"{self._default_stem()}{suffix}"
        path, _sel = QFileDialog.getSaveFileName(self, f"导出{suffix.lstrip('.').upper()}", str(start), filter_text)
        return path

    def _export_png(self) -> None:
        path = self._pick_path(".png", "PNG 图片 (*.png)")
        if not path:
            return
        render(self._spec).save(path)
        self._notify("已导出 PNG", path)

    def _export_svg(self) -> None:
        path = self._pick_path(".svg", "SVG 矢量图 (*.svg)")
        if not path:
            return
        box = self._spec
        try:
            Path(path).write_text(render_svg(box, units="mm"), encoding="utf-8")
        except OSError as exc:
            self._notify("导出失败", str(exc), ok=False)
            return
        self._notify("已导出 SVG", path)

    def _copy(self) -> None:
        png = render_png_bytes(self._spec)
        mime = QMimeData()
        mime.setImageData(pil_to_pixmap(render(self._spec)).toImage())
        mime.setData("PNG", png)
        QGuiApplication.clipboard().setMimeData(mime)
        self._notify("已复制图片", "粘贴到 Word / WPS 即可")

    def _save_preset(self) -> None:
        start = Path.home() / "Desktop" / f"{self._default_stem()}.seal.json"
        path, _sel = QFileDialog.getSaveFileName(self, "保存方案", str(start), "印章方案 (*.json)")
        if not path:
            return
        specs.save_spec(self._spec, path)
        self._notify("方案已保存", path)

    def _load_preset(self) -> None:
        path, _sel = QFileDialog.getOpenFileName(self, "打开方案", str(Path.home() / "Desktop"), "印章方案 (*.json)")
        if not path:
            return
        try:
            spec = specs.load_spec(path)
        except (OSError, ValueError) as exc:
            self._notify("方案读不了", str(exc), ok=False)
            return
        self._apply_spec(spec)
        self._refresh()
        self._notify("方案已载入", path)

    def _notify(self, title: str, content: str, ok: bool = True) -> None:
        maker = InfoBar.success if ok else InfoBar.error
        self._toast = maker(title, content, duration=3000, position=InfoBarPosition.TOP_RIGHT, parent=self)


__all__ = ["SealPage"]
