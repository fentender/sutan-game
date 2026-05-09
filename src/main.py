"""
苏丹的游戏 - Mod 合并管理器
入口文件

运行方式: 在项目根目录执行 python -m src.main
"""
import sys

from PySide6.QtWidgets import QApplication

from .core.app_service import AppService
from .gui.app import MainWindow
from .gui.resources import APP_ICON_PATH

def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    if APP_ICON_PATH.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))

    service = AppService()
    window = MainWindow(service)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
