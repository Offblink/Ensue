"""GUI 通用零件：滑块行、颜色行、卡片、纸张预览、PIL→QPixmap。"""

from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QEvent, QObject, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QColorDialog, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, CardWidget, Slider, StrongBodyLabel

PAPER = QColor("#FBFAF5")  # 「纸」底色：浅暖白，两种主题下都像纸


class _WheelBlocker(QObject):
    """吞掉滚轮事件的过滤器。

    两个细节都要：``ignore()`` 让事件继续冒泡给外层滚动区（页面照常滚），
    ``return True`` 才是「这个控件别处理」——只 ignore 不 return True 的话
    控件本体照样会把值滚掉。
    """

    def eventFilter(self, obj, event):  # Qt 命名
        if event.type() == QEvent.Type.Wheel:
            event.ignore()
            return True
        return super().eventFilter(obj, event)


_WHEEL_BLOCKER: _WheelBlocker | None = None


def block_wheel(*widgets: QWidget) -> None:
    """让这些控件（连同内部子控件，如微调框里的输入框）不再响应滚轮。

    用户常年被"滚页面时把数值滚变了"咬；数值类控件一律装这个护栏。
    """
    global _WHEEL_BLOCKER
    if _WHEEL_BLOCKER is None:
        _WHEEL_BLOCKER = _WheelBlocker()
    for widget in widgets:
        for target in (widget, *widget.findChildren(QWidget)):
            target.installEventFilter(_WHEEL_BLOCKER)


def pil_to_pixmap(img: Image.Image) -> QPixmap:
    """PIL → QPixmap。QImage 不持有 bytes 缓冲，必须 copy 一份。"""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    data = img.tobytes("raw", "RGBA")
    qimg = QImage(data, img.width, img.height, img.width * 4, QImage.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


def hint(text: str) -> CaptionLabel:
    label = CaptionLabel(text)
    label.setWordWrap(True)
    return label


class SliderRow(QWidget):
    """一行设置：说明 + 滑块 + 当前值。``step`` 决定取值粒度（1.0 就是整数档）。"""

    valueChanged = Signal(float)

    def __init__(
        self,
        text: str,
        minimum: float,
        maximum: float,
        value: float,
        step: float = 1.0,
        suffix: str = "",
        decimals: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self._min = float(minimum)
        self._max = float(maximum)
        self._step = float(step)
        self._suffix = suffix
        self._decimals = decimals
        self._name = BodyLabel(text, self)
        self._name.setMinimumWidth(76)
        self._slider = Slider(Qt.Horizontal, self)
        self._slider.setRange(0, max(1, int(round((self._max - self._min) / self._step))))
        block_wheel(self._slider)  # 滑块不吃滚轮：滚页面时别把参数滚歪
        self._slider.setMinimumWidth(120)
        self._value = BodyLabel(self)
        self._value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._value.setMinimumWidth(62)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(self._name)
        lay.addWidget(self._slider, 1)
        lay.addWidget(self._value)
        self._slider.setValue(self._ticks(value))
        self._value.setText(self._fmt(self.value()))
        self._slider.valueChanged.connect(self._emit)

    def _ticks(self, value: float) -> int:
        clamped = max(self._min, min(self._max, float(value)))
        return int(round((clamped - self._min) / self._step))

    def _fmt(self, value: float) -> str:
        text = f"{value:.{self._decimals}f}" if self._decimals else f"{int(round(value))}"
        return text + self._suffix

    def _emit(self, _tick: int) -> None:
        self._value.setText(self._fmt(self.value()))
        self.valueChanged.emit(self.value())

    def value(self) -> float:
        return self._min + self._slider.value() * self._step

    def setValue(self, value: float) -> None:
        self._slider.setValue(self._ticks(value))

    def set_label(self, text: str) -> None:
        self._name.setText(text)


class LabeledRow(QWidget):
    """一行设置：说明 + 任意控件。"""

    def __init__(self, text: str, widget: QWidget, label_width: int = 76, parent=None):
        super().__init__(parent)
        self.label = BodyLabel(text, self)
        self.label.setMinimumWidth(label_width)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(self.label)
        lay.addWidget(widget, 1)


class SwatchButton(QWidget):
    """一个圆形色块按钮（不用 QSS，直接画，免得跟 Fluent 主题样式打架）。"""

    clicked = Signal()

    def __init__(self, color: str, name: str, parent=None):
        super().__init__(parent)
        self.color = color
        self.active = False
        self.setFixedSize(30, 30)
        self.setToolTip(f"{name} {color}")
        self.setCursor(Qt.PointingHandCursor)

    def set_active(self, active: bool) -> None:
        if self.active != active:
            self.active = active
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(4, 4, -4, -4)
        painter.setBrush(QColor(self.color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(r)
        if self.active:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(self.palette().color(self.foregroundRole()), 2))
            painter.drawEllipse(r.adjusted(-3, -3, 3, 3))
        painter.end()


class ColorRow(QWidget):
    """预设色块 + 自定义颜色。"""

    colorChanged = Signal(str)

    def __init__(self, presets, current: str, parent=None):
        super().__init__(parent)
        self._current = current.lower()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self._swatches: list[SwatchButton] = []
        for name, color in presets:
            btn = SwatchButton(color, name, self)
            btn.clicked.connect(lambda c=color: self._pick(c))
            self._swatches.append(btn)
            lay.addWidget(btn)
        self._custom = SwatchButton("#888888", "自定义", self)
        self._custom.clicked.connect(self._pick_custom)
        lay.addWidget(self._custom)
        lay.addStretch(1)
        self._sync_active()

    def _pick(self, color: str) -> None:
        self._current = color.lower()
        self._sync_active()
        self.colorChanged.emit(self._current)

    def _pick_custom(self) -> None:
        color = QColorDialog.getColor(QColor(self._current), self, "选择印章颜色")
        if color.isValid():
            self._custom.color = color.name()
            self._pick(color.name())

    def _sync_active(self) -> None:
        for btn in self._swatches:
            btn.set_active(btn.color.lower() == self._current)
        self._custom.set_active(not any(b.active for b in self._swatches))

    def set_color(self, color: str) -> None:
        self._current = (color or "").lower()
        if not any(b.color.lower() == self._current for b in self._swatches):
            self._custom.color = self._current or "#888888"
        self._sync_active()

    def color(self) -> str:
        return self._current


def card(title: str, *children: QWidget, spacing: int = 10) -> CardWidget:
    """带小标题的卡片容器。"""
    box = CardWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(16, 12, 16, 14)
    lay.setSpacing(spacing)
    if title:
        lay.addWidget(StrongBodyLabel(title))
    for child in children:
        lay.addWidget(child)
    return box


class PaperPreview(QWidget):
    """把印章画在一张「纸」上：透明背景看得见，纸面衬底也不刺眼。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix: QPixmap | None = None
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_image(self, img: Image.Image | None) -> None:
        self._pix = pil_to_pixmap(img) if img is not None else None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        body = self.rect().adjusted(4, 4, -4, -4)
        path = QPainterPath()
        path.addRoundedRect(QRect(body.x(), body.y() + 2, body.width(), body.height()), 12, 12)
        painter.fillPath(path, QColor(0, 0, 0, 20))  # 淡淡一层投影
        painter.setBrush(PAPER)
        painter.setPen(QPen(QColor(0, 0, 0, 30), 1))
        painter.drawRoundedRect(body, 12, 12)
        if self._pix is not None and not self._pix.isNull():
            pad = 14
            avail_w = max(10, body.width() - 2 * pad)
            avail_h = max(10, body.height() - 2 * pad)
            k = min(avail_w / self._pix.width(), avail_h / self._pix.height())
            w = max(1, int(self._pix.width() * k))
            h = max(1, int(self._pix.height() * k))
            painter.drawPixmap(
                body.x() + (body.width() - w) // 2,
                body.y() + (body.height() - h) // 2,
                w,
                h,
                self._pix.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation),
            )
        painter.end()


__all__ = [
    "ColorRow",
    "LabeledRow",
    "PaperPreview",
    "SliderRow",
    "SwatchButton",
    "block_wheel",
    "card",
    "hint",
    "pil_to_pixmap",
]
