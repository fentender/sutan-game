"""
主窗口 - 串联所有 GUI 面板和核心逻辑
"""
import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSplitter, QMessageBox, QProgressBar,
    QFileDialog, QCheckBox
)
from PySide6.QtCore import Qt, QTimer

from ..config import UserConfig, SYNTHETIC_MOD_ID, SCHEMA_DIR, MOD_OVERRIDES_DIR
from ..core.mod_scanner import scan_all_mods
from ..core.diagnostics import diag, INFO, WARNING, ERROR
from ..core.deployer import generate_info_json
from ..core.override_utils import invalidate_stale_overrides
from ..core.id_remapper import remap_mod_configs
from .mod_list import ModListPanel
from .mod_detail import ModDetailPanel
from .override_panel import OverridePanel
from .log_panel import LogPanel, prefix_mod_title
from .workers import MergeWorker, AnalyzeWorker


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("苏丹的游戏 - Mod 合并管理器")
        self.setMinimumSize(1000, 700)

        self.config = UserConfig.load()
        self._worker: MergeWorker | None = None
        self._analyze_worker: AnalyzeWorker | None = None
        self._pending_action = None  # 等待分析完成后执行的操作

        # ID 重分配缓存：remap 只在 _analyze_conflicts 中做一次，
        # 结果供 AnalyzeWorker / DiffDialog / MergeWorker 共用
        self._remapped_configs: list[tuple[str, str, Path]] | None = None
        self._remap_temp_dir: Path | None = None

        # 防抖定时器：快速连续操作只触发一次分析
        self._analyze_timer = QTimer()
        self._analyze_timer.setSingleShot(True)
        self._analyze_timer.setInterval(300)
        self._analyze_timer.timeout.connect(self._analyze_conflicts)

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self._load_mods()
        self._schedule_analyze()

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

        self.btn_merge = QPushButton("执行合并")
        self.btn_merge.setStyleSheet("font-weight: bold;")
        self.btn_merge.clicked.connect(self._execute_merge)
        btn_layout.addWidget(self.btn_merge)

        btn_clean = QPushButton("清理合成Mod")
        btn_clean.clicked.connect(self._clean)
        btn_layout.addWidget(btn_clean)

        btn_layout.addStretch()

        self.chk_allow_deletions = QCheckBox("允许删减")
        self.chk_allow_deletions.setChecked(self.config.allow_deletions)
        self.chk_allow_deletions.setToolTip(
            "勾选后，Mod 中缺少的条目会从合并结果中删除。\n"
            "默认关闭，兼容未及时跟上游戏版本的 Mod。"
        )
        self.chk_allow_deletions.toggled.connect(self._on_allow_deletions_changed)
        btn_layout.addWidget(self.chk_allow_deletions)

        main_layout.addLayout(btn_layout)

        # 日志面板
        self.log_panel = LogPanel()
        self.log_panel.file_open_requested.connect(self._open_json_editor)
        main_layout.addWidget(self.log_panel)

        # 信号连接
        self.mod_list_panel.mod_selected.connect(self.mod_detail_panel.show_mod)
        self.mod_list_panel.order_changed.connect(self._save_config)
        self.override_panel.diff_requested.connect(self._open_diff)

    def _setup_statusbar(self):
        self.statusBar().showMessage("就绪")
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress_bar)

    def _load_mods(self):
        """加载所有 mod（workshop + 本地目录）"""
        diag.snapshot("scan", "parse")  # 清空上次的扫描/解析消息

        # 路径有效性检查
        if not self.config.workshop_dir.exists():
            diag.warn("scan",
                      "Workshop 路径不存在，请通过 文件 → 设置 Workshop 路径 进行配置: "
                      + str(self.config.workshop_dir))
        if not self.config.game_config_path.exists():
            diag.warn("scan",
                      "游戏配置目录不存在，请通过 文件 → 设置游戏路径 进行配置: "
                      + str(self.config.game_config_path))

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
        )
        self.statusBar().showMessage(f"已加载 {len(mods)} 个 Mod")

        # 缓存 mod_id -> mod_name 映射（仅含成功读取名称的 mod）
        self._mod_name_map = {
            m.mod_id: m.name
            for m in self.mod_list_panel._mods
            if m.name and m.name != m.mod_id
        }

        # 汇总所有错误和警告，添加 mod 名称前缀
        all_messages = diag.snapshot("scan", "parse")
        self._show_messages([
            (level, prefix_mod_title(msg, self._mod_name_map))
            for level, msg in all_messages
        ])

    def _on_allow_deletions_changed(self, checked: bool):
        self.config.allow_deletions = checked
        self.config.save()

    def _save_config(self):
        new_order = self.mod_list_panel.get_mod_order()
        new_enabled = self.mod_list_panel.get_enabled_ids()

        # 计算变化前后的启用有序列表，清理失效的 override
        old_enabled_set = set(self.config.enabled_mods)
        old_enabled_ordered = [mid for mid in self.config.mod_order
                               if mid in old_enabled_set]
        new_enabled_set = set(new_enabled)
        new_enabled_ordered = [mid for mid in new_order
                               if mid in new_enabled_set]
        deleted_ids = invalidate_stale_overrides(
            MOD_OVERRIDES_DIR, old_enabled_ordered, new_enabled_ordered
        )
        if deleted_ids:
            names = "、".join(self._mod_name_map.get(mid, mid) for mid in deleted_ids)
            self._log_message(
                INFO,
                f"Mod 排序/启用变化，已清理失效的覆盖编辑: {names}"
            )

        self.config.mod_order = new_order
        self.config.enabled_mods = new_enabled
        self.config.save()
        self._schedule_analyze()

    def _schedule_analyze(self):
        """防抖触发冲突分析（重置 300ms 定时器）"""
        self._analyze_timer.start()

    def _get_mod_configs(self) -> list[tuple[str, str, Path]]:
        """获取启用的 mod 配置路径列表"""
        enabled = self.mod_list_panel.get_enabled_mods()
        return [
            (m.mod_id, m.name, m.path / "config")
            for m in enabled
        ]

    def _analyze_conflicts(self):
        """异步分析冲突（由防抖定时器触发）"""
        # 合并期间不重新分析（避免清理正在使用的 remap 临时目录）
        if self._worker and self._worker.isRunning():
            return

        # 取消正在进行的分析，等待线程退出（避免 QThread 被销毁时仍在运行）
        if self._analyze_worker and self._analyze_worker.isRunning():
            self._analyze_worker.finished.disconnect()
            self._analyze_worker.error.disconnect()
            self._analyze_worker.cancel()
            self._analyze_worker.wait()

        mod_configs = self._get_mod_configs()
        if not mod_configs:
            self._cleanup_remap()
            self.override_panel.clear()
            self.statusBar().showMessage("没有启用的 Mod")
            return

        # ID 冲突检测与重分配（同步执行，通常很快）
        self._cleanup_remap()
        diag.snapshot("remap")
        remap_temp = MOD_OVERRIDES_DIR.parent / "_remap_temp"
        remapped, remap_msgs = remap_mod_configs(
            self.config.game_config_path, mod_configs, remap_temp,
        )
        self._remapped_configs = remapped
        self._remap_temp_dir = remap_temp

        # 展示 remap 日志
        remap_messages = diag.snapshot("remap")
        if remap_messages:
            self._show_messages([
                (level, prefix_mod_title(msg, self._mod_name_map))
                for level, msg in remap_messages
            ])

        self.statusBar().showMessage("正在分析覆盖情况...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self._analyze_worker = AnalyzeWorker(
            self.config.game_config_path, self._remapped_configs, SCHEMA_DIR,
        )
        self._analyze_worker.finished.connect(self._on_analyze_finished)
        self._analyze_worker.error.connect(self._on_analyze_error)
        self._analyze_worker.start()

    def _on_analyze_finished(self, overrides, parse_msgs):
        self.progress_bar.setVisible(False)
        self.override_panel.set_data(
            overrides, self.config.game_config_path,
            self._remapped_configs or self._get_mod_configs(),
            allow_deletions=self.config.allow_deletions
        )
        if parse_msgs:
            self._show_messages([
                (level, prefix_mod_title(msg, self._mod_name_map))
                for level, msg in parse_msgs
            ])
        conflict_count = sum(1 for o in overrides if o.has_conflict)
        self.statusBar().showMessage(
            f"分析完成: {len(overrides)} 个文件被修改, "
            f"{conflict_count} 个存在冲突"
        )

        # 执行挂起的操作
        if self._pending_action:
            action = self._pending_action
            self._pending_action = None
            action()

    def _on_analyze_error(self, error: str):
        self.progress_bar.setVisible(False)
        self.statusBar().showMessage(f"分析失败: {error}")
        self._log_message(ERROR, f"冲突分析失败: {error}")

    def _open_diff(self, rel_path: str):
        """打开 Diff 对比窗口（分析进行中则排队等待）"""
        if self._analyze_worker and self._analyze_worker.isRunning():
            self.statusBar().showMessage("等待冲突分析完成...")
            self._pending_action = lambda: self._open_diff(rel_path)
            return

        from .diff_dialog import DiffDialog
        dlg = DiffDialog(
            rel_path=rel_path,
            game_config_path=self.config.game_config_path,
            mod_configs=self._remapped_configs or self._get_mod_configs(),
            allow_deletions=self.config.allow_deletions,
            parent=self,
        )
        dlg.exec()

    def _execute_merge(self):
        """执行合并"""
        if self._analyze_worker and self._analyze_worker.isRunning():
            self.statusBar().showMessage("等待冲突分析完成...")
            self._pending_action = self._execute_merge
            return

        self._save_config()
        mod_configs = self._remapped_configs or self._get_mod_configs()
        if not mod_configs:
            QMessageBox.information(self, "提示", "没有启用的 Mod")
            return

        # 输出到本地 Mod 目录
        output_path = self.config.local_mod_dir / SYNTHETIC_MOD_ID
        if output_path.exists():
            shutil.rmtree(output_path)
        output_path.mkdir(parents=True)

        enabled = self.mod_list_panel.get_enabled_mods()
        mod_paths = [(m.name, m.path) for m in enabled]

        # 切换按钮为「取消合并」
        self.btn_merge.setText("取消合并")
        self.btn_merge.clicked.disconnect()
        self.btn_merge.clicked.connect(self._cancel_merge)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # 不确定进度

        self._merge_output_path = output_path
        self._worker = MergeWorker(
            self.config.game_config_path,
            mod_configs,
            output_path,
            mod_paths,
            allow_deletions=self.config.allow_deletions,
        )
        self._worker.progress.connect(lambda msg: self.statusBar().showMessage(msg))
        self._worker.finished.connect(self._on_merge_finished)
        self._worker.error.connect(self._on_merge_error)
        self._worker.start()

    def _cancel_merge(self):
        """取消正在进行的合并"""
        if self._worker:
            self._worker.cancel()
        self.statusBar().showMessage("正在取消...")

    def _restore_merge_btn(self):
        """恢复合并按钮到初始状态"""
        self.btn_merge.setText("执行合并")
        self.btn_merge.setEnabled(True)
        self.btn_merge.clicked.disconnect()
        self.btn_merge.clicked.connect(self._execute_merge)
        self.progress_bar.setVisible(False)

    def _on_merge_finished(self, results: dict, warnings: list[str]):
        self._restore_merge_btn()
        # 生成合成 Mod 的 Info.json
        enabled = self.mod_list_panel.get_enabled_mods()
        mod_names = [m.name for m in enabled]
        generate_info_json(mod_names, self._merge_output_path)

        output = self._merge_output_path
        msg = f"合并完成: {len(results)} 个文件已合并到 {output}"
        self.statusBar().showMessage(msg)
        # 展示合并过程中的警告（通过信号从工作线程传递，避免竞态）
        for w in warnings:
            self._log_message(WARNING, prefix_mod_title(w, self._mod_name_map))
        QMessageBox.information(self, "合并完成", f"已合并 {len(results)} 个文件到:\n{output}")

    def _on_merge_error(self, error: str):
        self._restore_merge_btn()
        # 清理合并失败后残留的半成品输出目录
        if self._merge_output_path and self._merge_output_path.exists():
            shutil.rmtree(self._merge_output_path, ignore_errors=True)
        self.statusBar().showMessage(f"合并失败: {error}")
        self._log_message(ERROR, f"合并失败: {error}")
        QMessageBox.critical(self, "合并失败", error)

    def _clean(self):
        """清理合成 Mod"""
        target = self.config.local_mod_dir / SYNTHETIC_MOD_ID
        if target.exists():
            shutil.rmtree(target)
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

    def _show_messages(self, messages: list[tuple[str, str]]):
        """显示或隐藏日志面板，messages 为 [(level, msg), ...]"""
        self.log_panel.show_messages(messages)

    def _log_message(self, level: str, msg: str):
        """追加一条日志到面板"""
        self.log_panel.log_message(level, msg)

    def _open_json_editor(self, file_path: str):
        """打开 JSON 编辑器（由日志面板双击触发）"""
        path = Path(file_path)
        if not path.exists():
            QMessageBox.warning(self, "提示", f"文件不存在:\n{file_path}")
            return
        from .json_editor import JsonEditorDialog
        dlg = JsonEditorDialog(path, parent=self)
        dlg.exec()

    def _cleanup_remap(self):
        """清理上一次 remap 产生的临时目录"""
        if self._remap_temp_dir and self._remap_temp_dir.exists():
            shutil.rmtree(self._remap_temp_dir, ignore_errors=True)
        self._remap_temp_dir = None
        self._remapped_configs = None

    def closeEvent(self, event):
        """关闭窗口时协作式等待工作线程结束"""
        self._analyze_timer.stop()
        if self._analyze_worker is not None and self._analyze_worker.isRunning():
            self._analyze_worker.cancel()
            self._analyze_worker.wait(5000)
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)
        self._cleanup_remap()
        event.accept()
