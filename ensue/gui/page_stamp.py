"""加盖水印页：三种水印来源（当前印章 / 印章图片 / 文字）盖到图片或 PDF 上。

预览区放的是**同一套合成代码**在缩小尺寸下的结果（相对坐标一致），所以所见即所得；
单处模式下可以直接在预览上拖动印章。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageOps
from PySide6.QtCore import QRect, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SegmentedWidget,
    SingleDirectionScrollArea,
    SubtitleLabel,
    ToolButton,
)

from .. import fonts
from ..seal import SealSpec, render
from ..stamp import (
    IMAGE_SUFFIXES,
    StampOptions,
    compose_overlay,
    parse_pages,
    prepare_tile,
    remove_white_background,
    resolve_mark,
    stamp_file,
    text_mark,
)
from .widgets import ColorRow, LabeledRow, SliderRow, block_wheel, card, hint, pil_to_pixmap

PREVIEW_PX = 520
PICK_FILTER = "图片与 PDF (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff *.pdf);;所有文件 (*)"
TEXT_COLORS = (("灰", "#8C8C8C"), ("红", "#D23B3B"), ("蓝", "#3B6ED2"), ("黑", "#404040"))
GRID_POSITIONS = (
    ("↖", 0.18, 0.16),
    ("↑", 0.50, 0.16),
    ("↗", 0.82, 0.16),
    ("←", 0.18, 0.50),
    ("·", 0.50, 0.50),
    ("→", 0.82, 0.50),
    ("↙", 0.18, 0.84),
    ("↓", 0.50, 0.84),
    ("↘", 0.82, 0.84),
)
# 预设：(名称, 布局, x, y, 大小, 角度, 不透明度, 间距)
PRESETS = (
    ("合同盖章", "single", 0.72, 0.78, 0.30, -12.0, 0.95, 1.6),
    ("背景水印", "single", 0.50, 0.50, 0.65, -30.0, 0.18, 1.6),
    ("平铺防伪", "tile", 0.50, 0.50, 0.26, 30.0, 0.20, 1.8),
)


class StampPreview(QWidget):
    """目标 + 水印的所见即所得预览；单处模式可拖动。"""

    moved = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._target: Image.Image | None = None
        self._mark: Image.Image | Callable[[int], Image.Image] | None = None
        self._tile_cache: tuple[int, Image.Image] | None = None
        self._opt = StampOptions()
        self._base_cache: tuple[tuple[int, int], Image.Image] | None = None
        self._rect = QRect()
        self._dragging = False
        self.setMinimumSize(360, 300)
        self.setAcceptDrops(False)

    def set_target(self, img: Image.Image | None) -> None:
        self._target = img
        self._base_cache = None
        self.update()

    def set_mark(self, mark: Image.Image | Callable[[int], Image.Image] | None) -> None:
        self._mark = mark
        self._tile_cache = None
        self.update()

    def set_options(self, opt: StampOptions) -> None:
        self._opt = opt
        self._tile_cache = None
        self.update()

    def _tile(self, width: int) -> Image.Image | None:
        """按显示宽度拿瓦片；宽度没变就复用，免得拖动时反复重画印章。"""
        if self._mark is None:
            return None
        if self._tile_cache is not None and self._tile_cache[0] == width:
            return self._tile_cache[1]
        tile = prepare_tile(self._mark, width, self._opt)
        self._tile_cache = (width, tile)
        return tile

    def _base(self, size: tuple[int, int]) -> Image.Image:
        assert self._target is not None
        if self._base_cache and self._base_cache[0] == size:
            return self._base_cache[1]
        base = self._target.resize(size, Image.LANCZOS).convert("RGBA")
        self._base_cache = (size, base)
        return base

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        body = self.rect().adjusted(6, 6, -6, -6)
        if self._target is None:
            painter.setPen(QColor(150, 150, 150))
            painter.drawText(body, Qt.AlignCenter, "拖入图片 / PDF，或在左侧添加文件")
            painter.end()
            self._rect = QRect()
            return
        tw, th = self._target.size
        k = min(body.width() / tw, body.height() / th)
        w, h = max(1, int(tw * k)), max(1, int(th * k))
        self._rect = QRect(body.x() + (body.width() - w) // 2, body.y() + (body.height() - h) // 2, w, h)
        frame = self._base((w, h))
        tile = self._tile(w)
        composed = Image.alpha_composite(frame, compose_overlay((w, h), tile, self._opt)) if tile is not None else frame
        painter.drawPixmap(self._rect.topLeft(), pil_to_pixmap(composed))
        painter.setPen(QPen(QColor(0, 0, 0, 60), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self._rect.adjusted(0, 0, -1, -1))
        painter.end()

    def _rel(self, pos) -> tuple[float, float]:
        if self._rect.isEmpty():
            return 0.5, 0.5
        x = (pos.x() - self._rect.x()) / max(1, self._rect.width())
        y = (pos.y() - self._rect.y()) / max(1, self._rect.height())
        return max(0.0, min(1.0, x)), max(0.0, min(1.0, y))

    def mousePressEvent(self, event):
        if self._target is not None and self._opt.layout == "single" and event.button() == Qt.LeftButton:
            self._dragging = True
            self.moved.emit(*self._rel(event.position().toPoint()))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.moved.emit(*self._rel(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        super().mouseReleaseEvent(event)


class StampWorker(QThread):
    """批量加盖：一个文件失败不影响其余文件。"""

    progressed = Signal(int, int, str)
    finished_with = Signal(list, list)

    def __init__(
        self, files: list[str], mark: Image.Image, opt: StampOptions, out_dir: str, pages_expr: str, parent=None
    ):
        super().__init__(parent)
        self._files = list(files)
        self._mark = mark
        self._opt = opt
        self._out_dir = out_dir
        self._pages = pages_expr
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        total = len(self._files)
        done, errors = [], []
        for index, path in enumerate(self._files, 1):
            if self._cancel:
                errors.append("已手动停止")
                break
            name = Path(path).name
            self.progressed.emit(index - 1, total, name)
            try:
                out = stamp_file(path, self._mark, self._opt, out_dir=self._out_dir or None, pages_expr=self._pages)
                done.append(str(out))
            except Exception as exc:  # 单个文件失败要报出来，但别中断整批
                errors.append(f"{name}：{exc}")
            self.progressed.emit(index, total, name)
        self.finished_with.emit(done, errors)


class StampPage(QWidget):
    """加盖水印页。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("stampPage")
        self._toast = None
        self._worker: StampWorker | None = None
        self._seal_spec = SealSpec()
        self._files: list[str] = []
        self._mark_source: tuple[tuple, Image.Image] | None = None
        self._preview_target: tuple[str, Image.Image] | None = None
        self.setAcceptDrops(True)
        self._build()
        self._bind()
        self._apply_mode()
        self._on_layout_changed()
        self._refresh_mark()
        self._refresh_preview()

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

        self.source_seg = SegmentedWidget()
        self.source_seg.addItem("seal", "当前印章")
        self.source_seg.addItem("image", "印章图片")
        self.source_seg.addItem("text", "文字水印")
        self.source_seg.setCurrentItem("seal")

        self.seal_thumb = SealThumb()  # 当前印章缩略图
        self.pick_mark_btn = PushButton(FluentIcon.PHOTO, "选择印章图片")
        self.mark_name = CaptionLabel("（未选择）")
        self.mark_name.setWordWrap(True)
        self.strip_white = CheckBox("自动去掉白底（扫描/拍照的章）")
        self.strip_white.setChecked(True)
        self.text_edit = LineEdit()
        self.text_edit.setPlaceholderText("例如：仅供办理医保报销使用")
        self.text_edit.setText("仅供存档参考")
        self.text_edit.setClearButtonEnabled(True)
        self.text_font = ComboBox()
        self.text_font.addItems(fonts.font_labels())
        self.text_color = ColorRow(TEXT_COLORS, "#8C8C8C")

        self.source_stack = QWidget()
        stack_lay = QVBoxLayout(self.source_stack)
        stack_lay.setContentsMargins(0, 0, 0, 0)
        stack_lay.setSpacing(8)
        self.seal_panel = QWidget()
        seal_lay = QVBoxLayout(self.seal_panel)
        seal_lay.setContentsMargins(0, 0, 0, 0)
        seal_lay.setSpacing(6)
        seal_lay.addWidget(self.seal_thumb, 0, Qt.AlignLeft)
        seal_lay.addWidget(hint("跟随『印章制作』页的参数；改了那边，这里会同步。"))
        self.image_panel = QWidget()
        image_lay = QVBoxLayout(self.image_panel)
        image_lay.setContentsMargins(0, 0, 0, 0)
        image_lay.setSpacing(6)
        image_lay.addWidget(self.pick_mark_btn)
        image_lay.addWidget(self.mark_name)
        image_lay.addWidget(self.strip_white)
        self.text_panel = QWidget()
        text_lay = QVBoxLayout(self.text_panel)
        text_lay.setContentsMargins(0, 0, 0, 0)
        text_lay.setSpacing(6)
        text_lay.addWidget(self.text_edit)
        text_lay.addWidget(LabeledRow("字体", self.text_font))
        text_lay.addWidget(LabeledRow("颜色", self.text_color))
        self.source_panels = (self.seal_panel, self.image_panel, self.text_panel)
        for w in self.source_panels:
            stack_lay.addWidget(w)
        box1 = card("水印来源", self.source_seg, self.source_stack)

        self.file_list = QListWidget()
        self.file_list.setMinimumHeight(96)
        self.file_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.add_btn = PushButton(FluentIcon.ADD, "添加文件")
        self.remove_btn = PushButton("移除所选")
        self.clear_btn = PushButton("清空")
        file_buttons = QWidget()
        flay = QHBoxLayout(file_buttons)
        flay.setContentsMargins(0, 0, 0, 0)
        flay.setSpacing(8)
        flay.addWidget(self.add_btn)
        flay.addWidget(self.remove_btn)
        flay.addWidget(self.clear_btn)
        flay.addStretch(1)
        self.pages_edit = LineEdit()
        self.pages_edit.setPlaceholderText("留空 = 全部页")
        box2 = card(
            "目标文件",
            self.file_list,
            file_buttons,
            LabeledRow("PDF 页码", self.pages_edit),
            hint("拖文件到窗口任意位置也能添加；页码写法：1-3,5（只对 PDF 生效）。"),
        )

        self.layout_seg = SegmentedWidget()
        self.layout_seg.addItem("single", "单处盖章")
        self.layout_seg.addItem("tile", "平铺铺满")
        self.layout_seg.setCurrentItem("single")
        self.grid_widget = QWidget()
        grid = QGridLayout(self.grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        self.grid_buttons = []
        for index, (glyph, gx, gy) in enumerate(GRID_POSITIONS):
            btn = PushButton(glyph)
            btn.setFixedSize(40, 30)
            btn.clicked.connect(lambda _c=False, x=gx, y=gy: self._set_pos(x, y))
            grid.addWidget(btn, index // 3, index % 3)
            self.grid_buttons.append(btn)
        self.pos_x = SliderRow("左右位置", 0, 100, 72, 1, "%")
        self.pos_y = SliderRow("上下位置", 0, 100, 78, 1, "%")
        self.gap_slider = SliderRow("平铺间距", 105, 400, 180, 5, "%")
        self.stagger_check = CheckBox("错开半格（交错排列）")
        self.stagger_check.setChecked(True)
        self.single_box = QWidget()
        single_lay = QVBoxLayout(self.single_box)
        single_lay.setContentsMargins(0, 0, 0, 0)
        single_lay.setSpacing(6)
        single_lay.addWidget(self.grid_widget, 0, Qt.AlignLeft)
        single_lay.addWidget(self.pos_x)
        single_lay.addWidget(self.pos_y)
        self.tile_box = QWidget()
        tile_lay = QVBoxLayout(self.tile_box)
        tile_lay.setContentsMargins(0, 0, 0, 0)
        tile_lay.setSpacing(6)
        tile_lay.addWidget(self.gap_slider)
        tile_lay.addWidget(self.stagger_check)
        self.size_slider = SliderRow("印章大小", 5, 100, 30, 1, "%")
        self.angle_slider = SliderRow("旋转角度", -180, 180, -12, 1, "°")
        self.opacity_slider = SliderRow("不透明度", 5, 100, 95, 1, "%")
        preset_row = QWidget()
        playout = QHBoxLayout(preset_row)
        playout.setContentsMargins(0, 0, 0, 0)
        playout.setSpacing(8)
        for name, *_rest in PRESETS:
            btn = PushButton(name)
            btn.clicked.connect(lambda _c=False, n=name: self._apply_preset(n))
            playout.addWidget(btn)
        playout.addStretch(1)
        box3 = card(
            "加盖方式",
            self.layout_seg,
            self.single_box,
            self.tile_box,
            self.size_slider,
            self.angle_slider,
            self.opacity_slider,
            preset_row,
        )

        self.out_edit = LineEdit()
        self.out_edit.setPlaceholderText("留空 = 与源文件同目录")
        self.out_btn = ToolButton(FluentIcon.FOLDER)
        self.out_btn.setToolTip("选择输出目录")
        out_row = QWidget()
        olay = QHBoxLayout(out_row)
        olay.setContentsMargins(0, 0, 0, 0)
        olay.setSpacing(8)
        olay.addWidget(self.out_edit, 1)
        olay.addWidget(self.out_btn)
        self.start_btn = PrimaryPushButton(FluentIcon.SAVE, "开始处理")
        self.progress = ProgressBar()
        self.progress.setValue(0)
        self.open_out_btn = PushButton(FluentIcon.FOLDER, "打开输出目录")
        self.open_out_btn.setEnabled(False)
        box4 = card(
            "输出",
            out_row,
            self.start_btn,
            self.progress,
            self.open_out_btn,
            hint("输出文件名 = 原文件名 +「_已盖章」，原文件不动；重复处理会覆盖上次的输出。"),
        )

        block_wheel(self.text_font)  # 数值/下拉不吃滚轮
        panel.addWidget(box1)
        panel.addWidget(box2)
        panel.addWidget(box3)
        panel.addWidget(box4)
        panel.addStretch(1)
        scroll = SingleDirectionScrollArea(orient=Qt.Vertical)
        scroll.setWidget(holder)
        scroll.setWidgetResizable(True)
        scroll.enableTransparentBackground()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setFixedWidth(430)
        return scroll

    def _build_preview(self) -> QWidget:
        holder = QWidget(self)
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(SubtitleLabel("预览"))
        head.addStretch(1)
        self.preview_caption = CaptionLabel("未选择文件")
        head.addWidget(self.preview_caption)
        lay.addLayout(head)
        self.preview = StampPreview()
        lay.addWidget(self.preview, 1)
        lay.addWidget(hint("预览就是实际合成流程缩到窗口大小跑一遍；单处模式下可直接拖动印章。"))
        return holder

    # ------------------------------------------------------------------ 绑定
    def _bind(self) -> None:
        self.source_seg.currentItemChanged.connect(lambda _k: self._on_source_changed())
        self.layout_seg.currentItemChanged.connect(lambda _k: self._on_layout_changed())
        self.pick_mark_btn.clicked.connect(self._pick_mark_image)
        self.strip_white.toggled.connect(lambda _v: self._refresh_mark())
        self.text_edit.textChanged.connect(lambda _t: self._refresh_mark())
        self.text_font.currentIndexChanged.connect(lambda _i: self._refresh_mark())
        self.text_color.colorChanged.connect(lambda _c: self._refresh_mark())
        self.add_btn.clicked.connect(self._pick_files)
        self.remove_btn.clicked.connect(self._remove_selected)
        self.clear_btn.clicked.connect(self._clear_files)
        self.pos_x.valueChanged.connect(lambda _v: self._refresh_preview())
        self.pos_y.valueChanged.connect(lambda _v: self._refresh_preview())
        self.gap_slider.valueChanged.connect(lambda _v: self._refresh_preview())
        self.stagger_check.toggled.connect(lambda _v: self._refresh_preview())
        self.size_slider.valueChanged.connect(lambda _v: self._refresh_preview())
        self.angle_slider.valueChanged.connect(lambda _v: self._refresh_preview())
        self.opacity_slider.valueChanged.connect(lambda _v: self._refresh_preview())
        self.preview.moved.connect(self._on_dragged)
        self.out_btn.clicked.connect(self._pick_out_dir)
        self.start_btn.clicked.connect(self._start)
        self.open_out_btn.clicked.connect(self._open_out_dir)

    # ------------------------------------------------------------------ 状态
    def set_seal_spec(self, spec: SealSpec) -> None:
        """印章页改了参数会喊这里；页面不可见时先记着，等切过来再渲染。"""
        self._seal_spec = spec
        if getattr(self, "preview", None) is not None and self.isVisible():
            self._refresh_mark()

    def showEvent(self, event):
        super().showEvent(event)
        if getattr(self, "preview", None) is not None:
            self._refresh_mark()

    def _source(self) -> str:
        return self.source_seg.currentRouteKey() or "seal"

    def options(self) -> StampOptions:
        return StampOptions(
            layout="tile" if self.layout_seg.currentRouteKey() == "tile" else "single",
            x=self.pos_x.value() / 100.0,
            y=self.pos_y.value() / 100.0,
            width_ratio=self.size_slider.value() / 100.0,
            angle=self.angle_slider.value(),
            opacity=self.opacity_slider.value() / 100.0,
            gap=self.gap_slider.value() / 100.0,
            stagger=self.stagger_check.isChecked(),
        )

    def _mark_factory(self) -> Callable[[int], Image.Image] | None:
        """按「目标宽度」现画水印的工厂；当前来源没内容时返回 None。"""
        source = self._source()
        if source == "seal":
            spec = self._seal_spec
            return lambda width: render(spec, target_px=width)
        if source == "image":
            path = getattr(self, "_mark_path", None)
            if not path:
                return None
            key = (path, self.strip_white.isChecked())
            if self._mark_source is None or self._mark_source[0] != key:
                with Image.open(path) as im:
                    img = ImageOps.exif_transpose(im).convert("RGBA")
                if self.strip_white.isChecked():
                    img = remove_white_background(img)
                self._mark_source = (key, img)
            origin = self._mark_source[1]

            def from_image(width: int) -> Image.Image:
                height = max(2, int(round(origin.height * width / origin.width)))
                return origin.resize((max(4, width), height), Image.LANCZOS)

            return from_image
        text = self.text_edit.text().strip()
        if not text:
            return None
        font_name = self.text_font.currentText()
        color = self.text_color.color()

        def from_text(width: int, text: str = text) -> Image.Image:
            # 由目标宽度反推字号（text_mark 会在两侧各留一个字宽，故 +1.2 余量）
            size = max(10, int(round(width / (len(text) + 1.2))))
            return text_mark(text, size_px=size, color=color, font_name=font_name)

        return from_text

    def _refresh_mark(self) -> None:
        factory = self._mark_factory()
        self.seal_thumb.set_image(resolve_mark(factory, 160) if factory else None)
        self.preview.set_mark(factory)
        self._refresh_preview(keep_target=True)

    def _apply_mode(self) -> None:
        source = self._source()
        for index, panel in enumerate(self.source_panels):
            panel.setVisible(("seal", "image", "text")[index] == source)
        if source == "text":
            self.size_slider.set_label("文字大小")
        else:
            self.size_slider.set_label("印章大小")

    def _on_source_changed(self) -> None:
        self._apply_mode()
        if self._source() == "image" and not getattr(self, "_mark_path", None):
            self._pick_mark_image()
        self._mark_source = None
        self._refresh_mark()

    def _on_layout_changed(self) -> None:
        tiled = self.layout_seg.currentRouteKey() == "tile"
        self.single_box.setVisible(not tiled)
        self.tile_box.setVisible(tiled)
        self.preview.set_options(self.options())

    def _set_pos(self, x: float, y: float) -> None:
        self.pos_x.setValue(round(x * 100))
        self.pos_y.setValue(round(y * 100))

    def _on_dragged(self, x: float, y: float) -> None:
        self.pos_x.setValue(round(x * 100))
        self.pos_y.setValue(round(y * 100))

    def _apply_preset(self, name: str) -> None:
        for preset, layout, x, y, size, angle, opacity, gap in PRESETS:
            if preset != name:
                continue
            self.layout_seg.setCurrentItem(layout)
            self._on_layout_changed()
            self._set_pos(x, y)
            self.size_slider.setValue(size * 100)
            self.angle_slider.setValue(angle)
            self.opacity_slider.setValue(opacity * 100)
            self.gap_slider.setValue(gap * 100)
            break
        self._refresh_preview(keep_target=True)

    # ------------------------------------------------------------ 文件与预览
    def _pick_files(self) -> None:
        paths, _sel = QFileDialog.getOpenFileNames(self, "选择要盖章的文件", str(Path.home() / "Desktop"), PICK_FILTER)
        self._add_files(paths)

    def _add_files(self, paths) -> None:
        added = 0
        for raw in paths:
            path = str(raw)
            if not path or path in self._files:
                continue
            suffix = Path(path).suffix.lower()
            if suffix not in IMAGE_SUFFIXES and suffix != ".pdf":
                continue
            self._files.append(path)
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(path)
            self.file_list.addItem(item)
            added += 1
        if added:
            self._refresh_preview(force=True)
        self._sync_start_enabled()

    def _remove_selected(self) -> None:
        for item in self.file_list.selectedItems():
            row = self.file_list.row(item)
            self.file_list.takeItem(row)
            self._files.pop(row)
        self._sync_start_enabled()
        self._refresh_preview(force=True)

    def _clear_files(self) -> None:
        self.file_list.clear()
        self._files.clear()
        self._preview_target = None
        self.preview.set_target(None)
        self.preview_caption.setText("未选择文件")
        self._sync_start_enabled()

    def _sync_start_enabled(self) -> None:
        self.start_btn.setEnabled(bool(self._files) and self._worker is None)

    def _load_preview_target(self, force: bool = False) -> None:
        if not self._files:
            return
        path = self._files[0]
        if not force and self._preview_target and self._preview_target[0] == path:
            return
        try:
            if Path(path).suffix.lower() == ".pdf":
                import pymupdf

                with pymupdf.open(path) as doc:
                    pages = parse_pages(self.pages_edit.text(), doc.page_count)
                    pix = doc[pages[0]].get_pixmap(dpi=110)
                    npages = doc.page_count
                    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                caption = f"{Path(path).name} · 第 {pages[0] + 1} 页 / 共 {npages} 页"
            else:
                with Image.open(path) as im:
                    img = ImageOps.exif_transpose(im).convert("RGBA")
                caption = f"{Path(path).name} · {img.width}×{img.height}"
        except Exception as exc:  # 预览失败不该挡住主流程
            self.preview_caption.setText(f"预览失败：{exc}")
            return
        img.thumbnail((1400, 1400), Image.LANCZOS)
        self._preview_target = (path, img)
        self.preview.set_target(img)
        self.preview_caption.setText(caption)

    def _refresh_preview(self, force: bool = False, keep_target: bool = False) -> None:
        if not keep_target:
            self._load_preview_target(force=force)
        self.preview.set_options(self.options())

    # ------------------------------------------------------------------ 输出
    def _pick_mark_image(self) -> None:
        path, _sel = QFileDialog.getOpenFileName(
            self, "选择印章图片", str(Path.home() / "Desktop"), "图片 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*)"
        )
        if path:
            self._mark_path = path
            self.mark_name.setText(Path(path).name)
            self._mark_cache = None
            self._refresh_mark()

    def _pick_out_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self.out_edit.text() or str(Path.home()))
        if folder:
            self.out_edit.setText(folder)
            self.open_out_btn.setEnabled(True)

    def _open_out_dir(self) -> None:
        folder = self.out_edit.text().strip()
        if folder and Path(folder).exists():
            os.startfile(folder)

    def _start(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.start_btn.setText("正在停止…")
            return
        if not self._files:
            self._notify("还没有选择文件", "先添加图片或 PDF", ok=False)
            return
        mark = self._mark_factory()
        if mark is None:
            self._notify("水印是空的", "当前水印来源没有内容", ok=False)
            return
        try:
            if any(Path(f).suffix.lower() == ".pdf" for f in self._files):
                import pymupdf

                for path in self._files:
                    if Path(path).suffix.lower() == ".pdf":
                        with pymupdf.open(path) as doc:
                            parse_pages(self.pages_edit.text(), doc.page_count)
        except ValueError as exc:
            self._notify("页码不对", str(exc), ok=False)
            return
        self._worker = StampWorker(
            self._files, mark, self.options(), self.out_edit.text().strip(), self.pages_edit.text().strip(), self
        )
        self._worker.progressed.connect(self._on_progress)
        self._worker.finished_with.connect(self._on_finished)
        self.progress.setValue(0)
        self.start_btn.setText("停止处理")
        self._sync_start_enabled()
        self._worker.start()

    def _on_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setValue(int(done / max(1, total) * 100))
        self.preview_caption.setText(f"处理中 {done}/{total} · {name}")

    def _on_finished(self, done: list, errors: list) -> None:
        self._worker = None
        self.start_btn.setText("开始处理")
        self.start_btn.setEnabled(True)
        self.progress.setValue(100 if done else 0)
        if done:
            self.open_out_btn.setEnabled(True)
            self._notify(f"处理完成 {len(done)} 个文件", Path(done[0]).parent.as_posix())
        if errors:
            self._toast = InfoBar.warning(
                "有问题", "；".join(errors[:3]), duration=6000, position=InfoBarPosition.TOP_RIGHT, parent=self
            )
        elif not done:
            self._notify("没有输出", "没有可处理的文件", ok=False)

    def _notify(self, title: str, content: str, ok: bool = True) -> None:
        maker = InfoBar.success if ok else InfoBar.error
        self._toast = maker(title, content, duration=3000, position=InfoBarPosition.TOP_RIGHT, parent=self)

    # ---------------------------------------------------------------- 拖放
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self._add_files(paths)
            event.acceptProposedAction()


class SealThumb(QWidget):
    """当前印章的小缩略图（跟纸张预览同一套画法，尺寸小一号）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix = None
        self.setFixedSize(120, 120)

    def set_image(self, img: Image.Image | None) -> None:
        self._pix = pil_to_pixmap(img) if img is not None else None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        body = self.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(QColor("#FBFAF5"))
        painter.setPen(QPen(QColor(0, 0, 0, 30), 1))
        painter.drawRoundedRect(body, 8, 8)
        if self._pix is not None and not self._pix.isNull():
            k = min((body.width() - 10) / self._pix.width(), (body.height() - 10) / self._pix.height())
            w, h = max(1, int(self._pix.width() * k)), max(1, int(self._pix.height() * k))
            painter.drawPixmap(
                body.x() + (body.width() - w) // 2,
                body.y() + (body.height() - h) // 2,
                w,
                h,
                self._pix.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation),
            )
        painter.end()


__all__ = ["StampPage"]
