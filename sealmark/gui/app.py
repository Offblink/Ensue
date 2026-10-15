"""应用入口：设置任务栏身份、主题，开主窗口。"""

from __future__ import annotations

import contextlib
import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme, setThemeColor

from .main_window import MainWindow

AUMID = "Offblink.SealMark"
ICON = Path(__file__).resolve().parent.parent.parent / "assets" / "icon.ico"


def main() -> int:
    # 任务栏身份：不带这个，任务栏会把窗口算到 python.exe 头上、显示 python 图标
    with contextlib.suppress(AttributeError, OSError):
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("SealMark")  # 标题栏身份；别再设 displayName，否则窗口标题会被追加一遍
    if ICON.exists():
        app.setWindowIcon(QIcon(str(ICON)))
    setTheme(Theme.AUTO)
    setThemeColor("#C42B1C")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
