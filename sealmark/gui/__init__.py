"""Qt 界面层：只有这一层依赖 PySide6，seal / stamp / svg / fonts 都不依赖。"""

from __future__ import annotations

__all__ = ["MainWindow"]


def __getattr__(name: str):
    """延迟导入 MainWindow：暴露 ``sealmark.gui.MainWindow`` 但不逼调用方装 Qt。"""
    if name == "MainWindow":
        from .main_window import MainWindow

        return MainWindow
    raise AttributeError(name)
