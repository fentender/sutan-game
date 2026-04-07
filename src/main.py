"""
苏丹的游戏 - Mod 合并管理器
入口文件

运行方式: 在项目根目录执行 python -m src.main
"""
import sys

from PySide6.QtWidgets import QApplication, QProgressDialog
from PySide6.QtCore import Qt

from .config import UserConfig, SCHEMA_DIR
from .gui.workers import SchemaWorker


def _ensure_schemas_with_ui(config_dir, schema_dir):
    """检查 schemas/ 是否已初始化，若为空则弹出进度框并生成"""
    if schema_dir.exists() and any(schema_dir.glob("*.schema.json")):
        return

    dlg = QProgressDialog("首次运行: 生成 Schema 规则...", "取消", 0, 100)
    dlg.setWindowTitle("Schema 初始化")
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.setMinimumDuration(0)
    dlg.setValue(0)

    worker = SchemaWorker(config_dir, schema_dir)

    def _on_progress(current, total, name):
        if total > 0:
            dlg.setMaximum(total)
            dlg.setValue(current)
            dlg.setLabelText(f"生成 Schema 规则: {name} ({current}/{total})")

    def _on_error(msg):
        from PySide6.QtWidgets import QMessageBox
        dlg.close()
        QMessageBox.critical(None, "Schema 生成失败", msg)

    worker.progress.connect(_on_progress)
    worker.finished.connect(dlg.close)
    worker.error.connect(_on_error)

    worker.start()
    dlg.exec()
    worker.wait()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    config = UserConfig.load()

    # 按配置启用性能评估
    if config.enable_profiler:
        from .core import profiler
        profiler.enable()

    _ensure_schemas_with_ui(config.game_config_path, SCHEMA_DIR)

    from .gui.app import MainWindow
    window = MainWindow()
    window.show()

    exit_code = app.exec()

    # 程序退出时输出性能报告
    if config.enable_profiler:
        from .core import profiler
        profiler.log_report()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
