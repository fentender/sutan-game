"""
苏丹的游戏 - Mod 合并管理器
入口文件
"""
import sys
from pathlib import Path

# 将 src 目录加入 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

from PySide6.QtWidgets import QApplication
from gui.app import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
