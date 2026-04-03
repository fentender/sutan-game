"""
主窗口 - 串联所有 GUI 面板和核心逻辑
"""
import re
import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSplitter, QMessageBox, QProgressBar,
    QFileDialog, QListWidget, QListWidgetItem
)
from PySide6.QtCore import Qt, QThread, Signal

from ..config import UserConfig, MERGED_OUTPUT_PATH, SYNTHETIC_MOD_ID
from ..core.mod_scanner import scan_all_mods, scan_errors
from ..core.json_parser import parse_warnings
from ..core.merger import merge_all_files, merge_warnings
from ..core.conflict import analyze_all_overrides
from ..core.deployer import deploy_to_workshop, clean_synthetic_mod, copy_resources
from .mod_list import ModListPanel
from .mod_detail import ModDetailPanel
from .override_panel import OverridePanel


class MergeWorker(QThread):
    """后台合并线程"""
    finished = Signal(dict)  # 合并结果
    error = Signal(str)
    progress = Signal(str)

    def __init__(self, game_config_path, mod_configs, output_path, mod_paths, merge_modes=None):
        super().__init__()
        self.game_config_path = game_config_path
        self.mod_configs = mod_configs
        self.output_path = output_path
        self.mod_paths = mod_paths
        self.merge_modes = merge_modes or {}

    def run(self):
        try:
            self.progress.emit("正在合并 JSON 文件...")
            results = merge_all_files(
                self.game_config_path,
                self.mod_configs,
                self.output_path / "config",
                merge_modes=self.merge_modes
            )
            self.progress.emit("正在复制资源文件...")
            copy_resources(self.mod_paths, self.output_path)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("苏丹的游戏 - Mod 合并管理器")
        self.setMinimumSize(1000, 700)

        self.config = UserConfig.load()
        self._worker: MergeWorker | None = None

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self._load_mods()

    def _setup_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        file_menu.addAction("设置游戏路径...", self._set_game_path)
        file_menu.addAction("设置 Workshop 路径...", self._set_workshop_path)
        file_menu.addAction("设置本地 Mod 路径...", self._set_local_mod_path)
        file_menu.addSeparator()
        file_menu.addAction("退出", self.close)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # 上半部分：mod 列表 + 详情
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.mod_list_panel = ModListPanel()
        self.mod_list_panel.setMinimumWidth(300)
        splitter.addWidget(self.mod_list_panel)

        self.mod_detail_panel = ModDetailPanel()
        splitter.addWidget(self.mod_detail_panel)

        splitter.setSizes([350, 450])
        main_layout.addWidget(splitter, 3)

        # 覆盖详情面板
        self.override_panel = OverridePanel()
        main_layout.addWidget(self.override_panel, 2)

        # 操作按钮
        btn_layout = QHBoxLayout()

        btn_analyze = QPushButton("分析冲突")
        btn_analyze.clicked.connect(self._analyze_conflicts)
        btn_layout.addWidget(btn_analyze)

        btn_merge = QPushButton("执行合并")
        btn_merge.setStyleSheet("font-weight: bold;")
        btn_merge.clicked.connect(self._execute_merge)
        btn_layout.addWidget(btn_merge)

        btn_deploy = QPushButton("部署到游戏")
        btn_deploy.clicked.connect(self._deploy)
        btn_layout.addWidget(btn_deploy)

        btn_clean = QPushButton("清理合成Mod")
        btn_clean.clicked.connect(self._clean)
        btn_layout.addWidget(btn_clean)

        main_layout.addLayout(btn_layout)

        # 错误日志面板
        self.error_log = QListWidget()
        self.error_log.setMaximumHeight(120)
        self.error_log.setStyleSheet(
            "QListWidget { font-family: Consolas, monospace; font-size: 12px; }"
            "QListWidget::item { color: #e88; padding: 3px 4px;"
            "  border-bottom: 1px solid #444; }"
        )
        self.error_log.setVisible(False)
        self.error_log.itemDoubleClicked.connect(self._on_error_double_clicked)
        self._error_count = 0
        main_layout.addWidget(self.error_log)

        # 信号连接
        self.mod_list_panel.mod_selected.connect(self.mod_detail_panel.show_mod)
        self.mod_list_panel.order_changed.connect(self._save_config)

    def _setup_statusbar(self):
        self.statusBar().showMessage("就绪")
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress_bar)

    def _load_mods(self):
        """加载所有 mod（workshop + 本地目录）"""
        scan_errors.clear()
        parse_warnings.clear()

        # 扫描 workshop 目录
        mods = scan_all_mods(
            self.config.workshop_dir,
            exclude_ids={SYNTHETIC_MOD_ID}
        )

        # 扫描本地 mod 目录
        local_dir = self.config.local_mod_dir
        if local_dir.exists():
            local_mods = scan_all_mods(local_dir)
            # 用 mod_id 去重（workshop 优先）
            existing_ids = {m.mod_id for m in mods}
            for lm in local_mods:
                if lm.mod_id not in existing_ids:
                    mods.append(lm)

        self.mod_list_panel.set_mods(
            mods,
            order=self.config.mod_order or None,
            enabled=self.config.enabled_mods or None,
            merge_modes=self.config.merge_modes or None
        )
        self.statusBar().showMessage(f"已加载 {len(mods)} 个 Mod")
        # 汇总所有错误和警告
        all_messages = list(scan_errors) + list(parse_warnings)
        self._show_errors(all_messages)

    def _save_config(self):
        self.config.mod_order = self.mod_list_panel.get_mod_order()
        self.config.enabled_mods = self.mod_list_panel.get_enabled_ids()
        self.config.merge_modes = self.mod_list_panel.get_merge_modes()
        self.config.save()

    def _get_mod_configs(self) -> list[tuple[str, str, Path]]:
        """获取启用的 mod 配置路径列表"""
        enabled = self.mod_list_panel.get_enabled_mods()
        return [
            (m.mod_id, m.name, m.path / "config")
            for m in enabled
        ]

    def _analyze_conflicts(self):
        """分析冲突"""
        self._save_config()
        mod_configs = self._get_mod_configs()
        if not mod_configs:
            QMessageBox.information(self, "提示", "没有启用的 Mod")
            return

        self.statusBar().showMessage("正在分析覆盖情况...")
        parse_warnings.clear()
        overrides = analyze_all_overrides(
            self.config.game_config_path,
            mod_configs
        )
        self.override_panel.set_data(overrides, self.config.game_config_path, mod_configs)

        for w in parse_warnings:
            self._log_error(w)

        conflict_count = sum(1 for o in overrides if o.has_conflict)
        self.statusBar().showMessage(
            f"分析完成: {len(overrides)} 个文件被修改, {conflict_count} 个存在冲突"
        )

    def _execute_merge(self):
        """执行合并"""
        self._save_config()
        mod_configs = self._get_mod_configs()
        if not mod_configs:
            QMessageBox.information(self, "提示", "没有启用的 Mod")
            return

        # 清理输出目录
        if MERGED_OUTPUT_PATH.exists():
            shutil.rmtree(MERGED_OUTPUT_PATH)
        MERGED_OUTPUT_PATH.mkdir(parents=True)

        enabled = self.mod_list_panel.get_enabled_mods()
        mod_paths = [(m.name, m.path) for m in enabled]

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # 不确定进度

        merge_modes = self.mod_list_panel.get_merge_modes()
        self._worker = MergeWorker(
            self.config.game_config_path,
            mod_configs,
            MERGED_OUTPUT_PATH,
            mod_paths,
            merge_modes=merge_modes
        )
        self._worker.progress.connect(lambda msg: self.statusBar().showMessage(msg))
        self._worker.finished.connect(self._on_merge_finished)
        self._worker.error.connect(self._on_merge_error)
        self._worker.start()

    def _on_merge_finished(self, results: dict):
        self.progress_bar.setVisible(False)
        msg = f"合并完成: {len(results)} 个文件已合并到 merged_output/"
        self.statusBar().showMessage(msg)
        # 展示合并过程中的警告
        if merge_warnings:
            for w in merge_warnings:
                self._log_error(w)
        QMessageBox.information(self, "合并完成", f"已合并 {len(results)} 个文件到:\n{MERGED_OUTPUT_PATH}")

    def _on_merge_error(self, error: str):
        self.progress_bar.setVisible(False)
        self.statusBar().showMessage(f"合并失败: {error}")
        self._log_error(f"合并失败: {error}")
        QMessageBox.critical(self, "合并失败", error)

    def _deploy(self):
        """部署合成 Mod"""
        if not MERGED_OUTPUT_PATH.exists() or not any(MERGED_OUTPUT_PATH.iterdir()):
            QMessageBox.warning(self, "提示", "请先执行合并")
            return

        enabled = self.mod_list_panel.get_enabled_mods()
        mod_names = [m.name for m in enabled]

        target = deploy_to_workshop(
            MERGED_OUTPUT_PATH,
            self.config.workshop_dir,
            mod_names
        )
        self.statusBar().showMessage(f"已部署到: {target}")
        QMessageBox.information(
            self, "部署完成",
            f"合成 Mod 已部署到:\n{target}\n\n"
            "请在游戏中禁用所有原始 Mod，只启用合成 Mod。"
        )

    def _clean(self):
        """清理合成 Mod"""
        if clean_synthetic_mod(self.config.workshop_dir):
            self.statusBar().showMessage("已清理合成 Mod")
            QMessageBox.information(self, "清理完成", "合成 Mod 已删除")
        else:
            QMessageBox.information(self, "提示", "没有找到合成 Mod")

    def _set_game_path(self):
        path = QFileDialog.getExistingDirectory(self, "选择游戏安装目录", self.config.game_path)
        if path:
            self.config.game_path = path
            self.config.save()
            self.statusBar().showMessage(f"游戏路径已更新: {path}")

    def _set_workshop_path(self):
        path = QFileDialog.getExistingDirectory(self, "选择 Workshop 目录", self.config.workshop_path)
        if path:
            self.config.workshop_path = path
            self.config.save()
            self._load_mods()

    def _set_local_mod_path(self):
        path = QFileDialog.getExistingDirectory(self, "选择本地 Mod 目录", self.config.local_mod_path)
        if path:
            self.config.local_mod_path = path
            self.config.save()
            self._load_mods()

    def _show_errors(self, errors: list[str]):
        """显示或隐藏错误日志"""
        self.error_log.clear()
        self._error_count = 0
        if errors:
            for msg in errors:
                self._log_error(msg)
        else:
            self.error_log.setVisible(False)

    def _log_error(self, msg: str):
        """追加一条错误到日志面板"""
        self._error_count += 1
        item = QListWidgetItem(f"[{self._error_count}] {msg}")
        # 从消息中提取文件路径
        match = re.match(r'([A-Za-z]:\\[^:]+\.json|/[^:]+\.json)', msg)
        if match:
            item.setData(Qt.ItemDataRole.UserRole, match.group(1))
        self.error_log.addItem(item)
        self.error_log.setVisible(True)

    def _on_error_double_clicked(self, item: QListWidgetItem):
        """双击错误日志条目，打开文件编辑器"""
        file_path = item.data(Qt.ItemDataRole.UserRole)
        if not file_path:
            return
        path = Path(file_path)
        if not path.exists():
            QMessageBox.warning(self, "提示", f"文件不存在:\n{file_path}")
            return

        from .json_editor import JsonEditorDialog
        dlg = JsonEditorDialog(path, parent=self)
        dlg.exec()
