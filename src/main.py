"""
苏丹的游戏 - Mod 合并管理器
入口文件

运行方式: 在项目根目录执行 python -m src.main
"""
import sys

from PySide6.QtWidgets import QApplication
from .gui.app import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
