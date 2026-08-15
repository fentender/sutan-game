"""
苏丹的游戏 - Mod 合并管理器
入口文件

运行方式: 在项目根目录执行 python -m src.main
"""
import sys

from PySide6.QtCore import QEvent, QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from .core.app_service import AppService
from .core.infra.error_reporter import (
    ExceptionReport,
    install_exception_hooks,
    report_unhandled_exception,
    set_report_callback,
)
from .gui.app import MainWindow
from .gui.resources import APP_ICON_PATH


class ErrorApplication(QApplication):
    """捕获 Qt 事件异常，并在主线程显示统一错误窗口。"""

    exception_ready = Signal(object)

    def __init__(self, argv: list[str]) -> None:
        super().__init__(argv)
        self._showing_exception = False
        self.exception_ready.connect(self._show_exception)
        set_report_callback(self.exception_ready.emit)

    def notify(self, receiver: QObject, event: QEvent) -> bool:
        try:
            return super().notify(receiver, event)
        except Exception as exc:
            report_unhandled_exception(exc, exc.__traceback__)
            return False

    def _show_exception(self, report: ExceptionReport) -> None:
        if self._showing_exception:
            return
        self._showing_exception = True
        try:
            message = QMessageBox(self.activeWindow())
            message.setIcon(QMessageBox.Icon.Critical)
            message.setWindowTitle("程序错误")
            message.setText("程序执行过程中发生错误，本次操作可能未完成。")
            message.setInformativeText(
                f"{report.summary}\n\n异常日志：{report.log_path}",
            )
            message.setDetailedText(report.details)
            message.exec()
        finally:
            self._showing_exception = False


def main() -> None:
    install_exception_hooks()
    app = ErrorApplication(sys.argv)
    app.setStyle("Fusion")

    if APP_ICON_PATH.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))

    service = AppService()
    window = MainWindow(service)
    window.show()

    exit_code = app.exec()
    set_report_callback(None)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
