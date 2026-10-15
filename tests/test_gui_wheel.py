"""滚轮护栏：数值控件不该被"滚页面"顺手改掉（用户实测误触过）。

每条断言都配一个**正对照**：同一类控件不装护栏时必须真的会被滚轮改值，
否则说明这条测试根本测不出回归（例如事件构造错了）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import qfluentwidgets

_flavor = Path(qfluentwidgets.__file__).parent / "common" / "config.py"
if "PySide6" not in _flavor.read_text(encoding="utf-8"):
    pytest.skip(
        f"当前解释器里的 qfluentwidgets 不是 PySide6 版（{qfluentwidgets.__file__}）："
        "GUI 测试请用 .venv 跑（scripts/check.ps1 已经指到 .venv）",
        allow_module_level=True,
    )

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication
from qfluentwidgets import ComboBox, DoubleSpinBox

from ensue.gui.widgets import SliderRow, block_wheel


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def wheel(widget, delta: int = 120, times: int = 3) -> None:
    """朝控件发一个真正的 QWheelEvent（和用户滚轮走同一条路）。"""
    for _ in range(times):
        event = QWheelEvent(
            QPointF(widget.rect().center()),
            QPointF(widget.mapToGlobal(widget.rect().center())),
            QPoint(0, 0),
            QPoint(0, delta),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        QApplication.sendEvent(widget, event)


def test_slider_row_ignores_wheel(qapp):
    row = SliderRow("测试", 0, 100, 50, 1, "%")
    wheel(row._slider)
    assert row.value() == 50


def test_slider_without_guard_is_wheel_sensitive(qapp):
    """正对照：证明上面那条不是"事件没送到"。"""
    from qfluentwidgets import Slider

    bare = Slider(Qt.Orientation.Horizontal)
    bare.setRange(0, 100)
    bare.setValue(50)
    wheel(bare)
    assert bare.value() != 50, "裸滑块本来就该被滚轮改值"


def test_spinbox_ignores_wheel_including_inner_line_edit(qapp):
    box = DoubleSpinBox()
    box.setRange(0, 100)
    box.setValue(50)
    block_wheel(box)
    wheel(box)
    assert box.value() == 50
    edit = box.lineEdit()
    if edit is not None:
        wheel(edit)
        assert box.value() == 50, "光标停在微调框的输入框上也不能被滚轮改值"


def test_bare_spinbox_is_wheel_sensitive(qapp):
    """正对照。"""
    box = DoubleSpinBox()
    box.setRange(0, 100)
    box.setValue(50)
    wheel(box)
    assert box.value() != 50


def test_combobox_ignores_wheel(qapp):
    box = ComboBox()
    box.addItems(["甲", "乙", "丙"])
    box.setCurrentIndex(1)
    block_wheel(box)
    wheel(box)
    assert box.currentIndex() == 1


def test_seal_page_blocks_every_numeric_widget(qapp):
    from ensue.gui.page_seal import SealPage

    page = SealPage()
    page.size_spin.setValue(40)
    wheel(page.size_spin)
    assert page.size_spin.value() == 40
    page.font_box.setCurrentIndex(1)
    wheel(page.font_box)
    assert page.font_box.currentIndex() == 1
    page.border_spin.setValue(1.5)
    wheel(page.border_spin)
    assert page.border_spin.value() == pytest.approx(1.5)
    page.deleteLater()


def test_stamp_page_blocks_font_combo(qapp):
    from ensue.gui.page_stamp import StampPage

    page = StampPage()
    page.text_font.setCurrentIndex(1)
    wheel(page.text_font)
    assert page.text_font.currentIndex() == 1
    page.deleteLater()
