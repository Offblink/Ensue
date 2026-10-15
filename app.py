"""电子印章水印应用入口（双击 run.bat 走的就是这里）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ensue.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
