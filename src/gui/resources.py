"""
GUI 资源常量 — 图标路径、版本号等 GUI 专属资源。

与业务逻辑无关，不属于 UserConfig。
"""
import sys
from pathlib import Path


if getattr(sys, 'frozen', False):
    _MEIPASS = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    APP_ICON_PATH = _MEIPASS / "app.ico"
else:
    _PROJECT_ROOT = Path(__file__).parent.parent.parent
    APP_ICON_PATH = _PROJECT_ROOT / "app.ico"
