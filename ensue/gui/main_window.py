"""主窗口：印章制作 / 加盖水印 两页。"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon, FluentWindow, NavigationItemPosition

from .page_seal import SealPage
from .page_stamp import StampPage

APP_TITLE = "Ensue · 电子印章水印"


class MainWindow(FluentWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sealMarkWindow")
        self.setWindowTitle(APP_TITLE)
        self.resize(1200, 820)
        self.setMinimumSize(1000, 700)

        self.seal_page = SealPage(self)
        self.stamp_page = StampPage(self)
        self.addSubInterface(self.seal_page, FluentIcon.EDIT, "印章制作")
        self.addSubInterface(self.stamp_page, FluentIcon.SAVE, "加盖水印", NavigationItemPosition.TOP)
        self.seal_page.specChanged.connect(self._on_spec_changed)
        self.stamp_page.set_seal_spec(self.seal_page.spec())
        self._center()

    def _on_spec_changed(self) -> None:
        self.stamp_page.set_seal_spec(self.seal_page.spec())

    def _center(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2, max(area.y(), area.center().y() - self.height() // 2 - 30))


__all__ = ["MainWindow"]
