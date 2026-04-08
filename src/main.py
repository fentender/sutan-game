"""
苏丹的游戏 - Mod 合并管理器
入口文件

运行方式: 在项目根目录执行 python -m src.main
"""
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication, QProgressDialog
from PySide6.QtCore import Qt

from .config import UserConfig, SCHEMA_DIR, APP_ICON_PATH


def _ensure_schemas_with_ui(config_dir, schema_dir):
    """检查 schemas/ 是否已初始化，若为空则弹出进度框并生成"""
    if schema_dir.exists() and any(schema_dir.glob("*.schema.json")):
        return

    dlg = QProgressDialog("首次运行: 生成 Schema 规则...", "取消", 0, 100)
    dlg.setWindowTitle("Schema 初始化")
    dlg.setWindowModality(Qt.WindowModality.ApplicationModal)
    dlg.setMinimumDuration(0)
    dlg.setValue(0)

    from .gui.workers import SchemaWorker
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


def _ensure_paths(config: UserConfig) -> bool:
    """确保游戏路径和创意工坊路径有效，无效时弹出配置对话框。
    返回 True 表示路径已就绪，False 表示用户取消/关闭了对话框。
    """
    game_ok = config.game_path and Path(config.game_path).exists()
    workshop_ok = config.workshop_path and Path(config.workshop_path).exists()
    if game_ok and workshop_ok:
        return True

    from .gui.setup_dialog import SetupDialog
    from .config import detect_game_path, detect_workshop_path

    # 预填：优先用配置中的值，无效则用自动检测结果
    default_game = config.game_path if game_ok else detect_game_path()
    default_workshop = config.workshop_path if workshop_ok else detect_workshop_path()

    dlg = SetupDialog(default_game, default_workshop)
    if dlg.exec() != SetupDialog.DialogCode.Accepted:
        return False

    config.game_path = dlg.game_path
    config.workshop_path = dlg.workshop_path
    config.save()
    return True


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    if APP_ICON_PATH.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))

    config = UserConfig.load()

    # 按配置启用性能评估
    if config.enable_profiler:
        from .core import profiler
        profiler.enable()

    # 确保路径有效，无效则引导用户配置；用户关闭则退出
    if not _ensure_paths(config):
        sys.exit(0)

    # Schema 生成（依赖游戏路径，路径已确认有效）
    config_dir = config.game_config_path
    if config_dir.exists():
        _ensure_schemas_with_ui(config_dir, SCHEMA_DIR)

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
