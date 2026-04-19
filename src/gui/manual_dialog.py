"""使用教程对话框 - 在应用内展示用户使用手册"""
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout, QWidget


def _get_manual_path() -> Path:
    """获取手册路径，兼容 PyInstaller 打包环境"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
    return base / "docs" / "用户使用手册.md"


class ManualDialog(QDialog):
    """显示 Markdown 格式使用手册的对话框"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("使用教程")
        self.resize(800, 600)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint
        )

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setMarkdown(self._load_manual())

        layout = QVBoxLayout(self)
        layout.addWidget(browser)

    @staticmethod
    def _load_manual() -> str:
        """读取手册文件内容"""
        path = _get_manual_path()
        if not path.is_file():
            return f"找不到使用手册文件：{path}"
        return path.read_text(encoding="utf-8")
