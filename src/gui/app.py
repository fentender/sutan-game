"""
主窗口 - 串联所有 GUI 面板和核心逻辑
"""
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.core.api import (
    ERROR,
    WARNING,
    AppService,
    AsyncTaskHandle,
    FileOverrideInfo,
    InitState,
    MergeMode,
    RemapTable,
    TaskDone,
    TaskError,
    TaskEvent,
    TaskProgress,
    TaskStage,
    diag,
)

from .panels.log import LogPanel, prefix_mod_title
from .panels.mod_detail import ModDetailPanel
from .panels.mod_list import ModListPanel
from .panels.override import OverridePanel


class _TaskBridge(QObject):
    task_event = Signal(object)


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self, service: AppService) -> None:
        super().__init__()
        self.setWindowTitle("苏丹的游戏 - Mod 合并管理器")
        self.setMinimumSize(1000, 700)

        self.service = service
        self._delta_handle: AsyncTaskHandle | None = None
        self._delta_progress: QProgressDialog | None = None
        self._delta_bridge = _TaskBridge(self)
        self._delta_bridge.task_event.connect(self._on_delta_event)
        self._merge_handle: AsyncTaskHandle | None = None
        self._merge_progress: QProgressDialog | None = None
        self._merge_bridge = _TaskBridge(self)
        self._merge_bridge.task_event.connect(self._on_merge_event)
        self._analyze_handle: AsyncTaskHandle | None = None
        self._analyze_bridge = _TaskBridge(self)
        self._analyze_bridge.task_event.connect(self._on_analyze_event)
        self._analyze_gen: int = 0
        self._update_handle: AsyncTaskHandle | None = None
        self._update_bridge = _TaskBridge(self)
        self._update_bridge.task_event.connect(self._on_update_event)
        self._update_silent: bool = True
        self._pending_action: Callable[[], None] | None = None

        self._remap_tables: dict[str, RemapTable] | None = None
        self._mod_name_map: dict[str, str] = {}

        self._analyze_timer = QTimer()
        self._analyze_timer.setSingleShot(True)
        self._analyze_timer.setInterval(300)
        self._analyze_timer.timeout.connect(self._analyze_conflicts)

        self._init_timer = QTimer()
        self._init_timer.setInterval(50)
        self._init_timer.timeout.connect(self._poll_init_state)

        # 路径确认（不通过则弹配置对话框）
        if not self._ensure_paths():
            import sys
            sys.exit(0)

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()

        # AppService 构造时已同步扫描 + 启动异步链
        if service.init_state != InitState.IDLE:
            self._load_mods()

        QTimer.singleShot(3000, self._auto_check_update)

    def _ensure_paths(self) -> bool:
        """确保路径有效，无效则弹配置对话框。返回 True 路径就绪，False 用户取消。"""
        if self.service.paths_valid():
            return True

        from .dialogs.setup_wizard import SetupDialog

        default_game = (
            self.service.game_path
            if self.service.game_path and Path(self.service.game_path).exists()
            else self.service.detect_game_path()
        )
        default_workshop = (
            self.service.workshop_path
            if self.service.workshop_path and Path(self.service.workshop_path).exists()
            else self.service.detect_workshop_path()
        )

        dlg = SetupDialog(default_game, default_workshop)
        if dlg.exec() != SetupDialog.DialogCode.Accepted:
            return False

        self.service.set_game_path(dlg.game_path)
        self.service.set_workshop_path(dlg.workshop_path)
        return True

    def _load_mods(self) -> None:
        """读取 AppService 同步扫描结果，填充面板，启动轮询"""
        mods = self.service.mods
        self.mod_list_panel.set_mods(
            mods,
            order=self.service.mod_order or None,
            enabled=self.service.enabled_mods or None,
            merge_modes=self.service.mod_merge_modes or None,
            game_update_time=self.service.get_game_update_time(self.service.workshop_dir),
            major_update_ts=self.service.get_major_update_ts(),
        )
        self.statusBar().showMessage(f"已加载 {len(mods)} 个 Mod")

        self._mod_name_map = {
            m.mod_id: m.name
            for m in self.mod_list_panel._mods
            if m.name and m.name != m.mod_id
        }

        all_messages = diag.snapshot("scan", "parse")
        self._show_messages([
            (level, prefix_mod_title(msg, self._mod_name_map))
            for level, msg in all_messages
        ])

        self._init_timer.start()

    def _poll_init_state(self) -> None:
        state = self.service.init_state
        match state:
            case InitState.SCANNING:
                self.statusBar().showMessage("正在扫描 Mod...")
            case InitState.GENERATING_SCHEMAS:
                cur, total, name = self.service.init_schema_progress
                if total > 0:
                    self.statusBar().showMessage(
                        f"首次运行: 生成 Schema 规则... {name} ({cur}/{total})")
                else:
                    self.statusBar().showMessage("首次运行: 生成 Schema 规则...")
            case InitState.LOADING_JSON:
                cur, total = self.service.init_progress
                if total > 0:
                    self.statusBar().showMessage(f"正在加载 JSON 资源... ({cur}/{total})")
                else:
                    self.statusBar().showMessage("正在加载 JSON 资源...")
            case InitState.AWAITING_FIX:
                self._init_timer.stop()
                self._handle_parse_failures()
            case InitState.COMPUTING_DELTA:
                cur, total = self.service.init_progress
                if total > 0:
                    self.statusBar().showMessage(f"正在预计算差异... ({cur}/{total})")
                else:
                    self.statusBar().showMessage("正在预计算差异...")
            case InitState.ANALYZING:
                self.statusBar().showMessage("正在分析覆盖情况...")
            case InitState.READY:
                self._init_timer.stop()
                self._on_init_complete()
            case InitState.ERROR:
                self._init_timer.stop()
                self._log_message(ERROR, f"初始化失败: {self.service.init_error}")
                self.statusBar().showMessage("初始化失败")

    def _handle_parse_failures(self) -> None:
        failures = self.service.parse_failures
        from .dialogs.json_fix import JsonFixDialog
        dialog = JsonFixDialog(failures, parent=self)
        dialog.exec()

        fixed_paths = [f.file_path for f in failures
                       if dialog.resolutions.get(str(f.file_path), {}).get('action') == 'fixed']
        ignored = [f for f in failures
                   if dialog.resolutions.get(str(f.file_path), {}).get('action') != 'fixed']

        self.service.continue_after_fix(fixed_paths, ignored)
        self._init_timer.start()

    def _on_init_complete(self) -> None:
        json_msgs = diag.snapshot("json")
        if json_msgs:
            self._show_messages([
                (level, prefix_mod_title(msg, self._mod_name_map))
                for level, msg in json_msgs
            ])

        self.mod_list_panel.update_overlap(self.service.overlap_map)

        enabled = self.mod_list_panel.get_enabled_mods()
        major_update_ts = self.service.get_major_update_ts()
        outdated = {m.name for m in enabled
                    if m.update_time is not None
                    and m.update_time < major_update_ts}
        self.override_panel.set_data(
            self.service.init_overrides,
            self._get_mod_configs(),
            outdated,
        )
        self.statusBar().showMessage("初始化完成")

    def _setup_menu(self) -> None:
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        file_menu.addAction("设置游戏路径...", self._set_game_path)
        file_menu.addAction("设置 Workshop 路径...", self._set_workshop_path)
        file_menu.addAction("设置本地 Mod 路径...", self._set_local_mod_path)
        file_menu.addSeparator()
        file_menu.addAction("退出", self.close)

        help_menu = menubar.addMenu("帮助")
        help_menu.addAction(
            f"检查更新 (当前: v{self.service.app_version})", self._check_update,
        )

        menubar.addAction("使用教程", self._show_manual)

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.mod_list_panel = ModListPanel()
        self.mod_list_panel.setMinimumWidth(300)
        splitter.addWidget(self.mod_list_panel)

        self.mod_detail_panel = ModDetailPanel()
        splitter.addWidget(self.mod_detail_panel)

        splitter.setSizes([350, 450])
        main_layout.addWidget(splitter, 3)

        self.override_panel = OverridePanel(service=self.service)
        main_layout.addWidget(self.override_panel, 2)

        btn_layout = QHBoxLayout()

        self.btn_merge = QPushButton("执行合并")
        self.btn_merge.setStyleSheet("font-weight: bold;")
        self.btn_merge.clicked.connect(self._execute_merge)
        btn_layout.addWidget(self.btn_merge)

        btn_clean = QPushButton("清理合成Mod")
        btn_clean.clicked.connect(self._clean)
        btn_layout.addWidget(btn_clean)

        btn_layout.addStretch()

        btn_layout.addWidget(QLabel("合并模式:"))

        self.cmb_merge_mode = QComboBox()
        self.cmb_merge_mode.addItem("智能合并", MergeMode.SMART.value)
        self.cmb_merge_mode.addItem("自适应合并", MergeMode.ADAPTIVE.value)
        self.cmb_merge_mode.addItem("正常合并", MergeMode.NORMAL.value)
        self.cmb_merge_mode.addItem("简单替换", MergeMode.REPLACE.value)
        self.cmb_merge_mode.setToolTip(
            "智能合并：保守策略，数组元素禁止删除，condition/action/result 内允许删除\n"
            "自适应合并：基于 Mod 更新时间匹配历史游戏版本计算差异，合并行为同智能合并\n"
            "正常合并：全部应用 Mod 的增删改\n"
            "简单替换：直接用 Mod 文件替换，不做字段级合并"
        )
        mode_idx = self.cmb_merge_mode.findData(self.service.merge_mode)
        if mode_idx >= 0:
            self.cmb_merge_mode.setCurrentIndex(mode_idx)
        self.cmb_merge_mode.currentIndexChanged.connect(self._on_merge_mode_changed)
        btn_layout.addWidget(self.cmb_merge_mode)

        self.btn_deletion_report = QPushButton("查看删减报告")
        self.btn_deletion_report.clicked.connect(self._show_deletion_report)
        btn_layout.addWidget(self.btn_deletion_report)

        main_layout.addLayout(btn_layout)

        self.log_panel = LogPanel()
        self.log_panel.file_open_requested.connect(self._open_json_editor)
        main_layout.addWidget(self.log_panel)

        self.mod_list_panel.mod_selected.connect(self.mod_detail_panel.show_mod)
        self.mod_list_panel.order_changed.connect(self._save_config)
        self.mod_list_panel.merge_mode_changed.connect(self._on_mod_merge_mode_changed)
        self.override_panel.diff_requested.connect(self._open_diff)

    def _setup_statusbar(self) -> None:
        self.statusBar().showMessage("就绪")
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(200)
        self.progress_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.progress_bar)

    def _request_delta_refresh(self) -> None:
        if self._delta_handle:
            self._delta_handle.cancel()
        enabled_ids = [
            mid for mid in self.service.mod_order
            if mid in set(self.service.enabled_mods)
        ]
        if not enabled_ids:
            return
        self._delta_progress = QProgressDialog(
            "正在预计算差异数据...", "", 0, 0, self,
        )
        self._delta_progress.setWindowTitle("初始化")
        self._delta_progress.setMinimumDuration(0)
        self._delta_progress.setCancelButton(None)
        self._delta_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._delta_progress.show()
        self._delta_handle = self.service.refresh_delta_async(
            enabled_ids,
            self._get_merge_mode(),
            self._get_mod_merge_modes(),
            cb=self._delta_bridge.task_event.emit,
        )

    def _on_delta_event(self, event: TaskEvent) -> None:
        match event:
            case TaskProgress(completed, total):
                if self._delta_progress:
                    self._delta_progress.setMaximum(total)
                    self._delta_progress.setValue(completed)
            case TaskDone():
                if self._delta_progress:
                    self._delta_progress.close()
                    self._delta_progress = None
                self.statusBar().showMessage("初始化完成")
                self._schedule_analyze()
            case TaskError(message=message):
                if self._delta_progress:
                    self._delta_progress.close()
                    self._delta_progress = None
                self.statusBar().showMessage(f"差异预计算失败: {message}")
                self._log_message(ERROR, f"差异预计算失败: {message}")

    def _on_merge_mode_changed(self, index: int) -> None:
        mode_value = self.cmb_merge_mode.itemData(index)
        if mode_value:
            self.service.update_merge_mode(mode_value)
            self._request_delta_refresh()

    def _get_merge_mode(self) -> MergeMode:
        try:
            return MergeMode(self.service.merge_mode)
        except ValueError:
            return MergeMode.SMART

    def _get_mod_merge_modes(self) -> dict[str, MergeMode]:
        result: dict[str, MergeMode] = {}
        for mod_id, mode_str in self.service.mod_merge_modes.items():
            try:
                result[mod_id] = MergeMode(mode_str)
            except ValueError:
                continue
        return result

    def _get_mod_update_times(self) -> dict[str, int]:
        enabled = self.mod_list_panel.get_enabled_mods()
        return {m.mod_id: m.update_time for m in enabled if m.update_time is not None}

    def _on_mod_merge_mode_changed(self, mod_id: str, mode_value: str) -> None:
        self.service.update_mod_merge_mode(
            mod_id, mode_value if mode_value else None,
        )
        self._request_delta_refresh()

    def _show_deletion_report(self) -> None:
        from .dialogs.deletion import DeletionReportDialog
        dlg = DeletionReportDialog(
            self.override_panel._data,
            mod_configs=self._get_mod_configs(),
            service=self.service,
            parent=self,
        )
        dlg.exec()

    def _save_config(self) -> None:
        new_order = self.mod_list_panel.get_mod_order()
        new_enabled = self.mod_list_panel.get_enabled_ids()
        self.service.update_mod_order(new_order, new_enabled)
        self._schedule_analyze()

    def _schedule_analyze(self) -> None:
        self._analyze_timer.start()

    def _get_mod_configs(self) -> list[tuple[str, str, Path]]:
        enabled = self.mod_list_panel.get_enabled_mods()
        return [
            (m.mod_id, m.name, m.path / "config")
            for m in enabled
        ]

    def _is_analyzing(self) -> bool:
        return self._analyze_handle is not None and not self._analyze_handle.is_done

    def _analyze_conflicts(self) -> None:
        if self._merge_handle and not self._merge_handle.is_done:
            return

        if self._analyze_handle and not self._analyze_handle.is_done:
            self._analyze_handle.cancel()

        mod_configs = self._get_mod_configs()
        if not mod_configs:
            self._cleanup_remap()
            self.override_panel.clear()
            self.statusBar().showMessage("没有启用的 Mod")
            return

        self._cleanup_remap()
        diag.snapshot("remap")
        _remap_msgs, remap_tables = self.service.remap_mod_configs(mod_configs)
        self._remap_tables = remap_tables

        remap_messages = diag.snapshot("remap")
        if remap_messages:
            self._show_messages([
                (level, prefix_mod_title(msg, self._mod_name_map))
                for level, msg in remap_messages
            ])

        enabled_ids = [mod_id for mod_id, _, _ in mod_configs]
        self.service.invalidate_delta()
        self.service.init_delta(enabled_ids,
                                merge_mode=self._get_merge_mode(),
                                mod_merge_modes=self._get_mod_merge_modes())

        self.statusBar().showMessage("正在分析覆盖情况...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self._analyze_gen += 1
        gen = self._analyze_gen

        def _cb(ev: TaskEvent) -> None:
            if self._analyze_gen == gen:
                self._analyze_bridge.task_event.emit(ev)

        self._analyze_handle = self.service.analyze_async(
            self._get_mod_configs(), self.service.schema_dir, cb=_cb,
        )

    def _on_analyze_event(self, event: TaskEvent) -> None:
        match event:
            case TaskDone(result=(overrides, parse_msgs)):
                self._on_analyze_finished(overrides, parse_msgs)
            case TaskError(message=message):
                self._on_analyze_error(message)

    def _on_analyze_finished(self, overrides: list[FileOverrideInfo],
                             parse_msgs: list[tuple[str, str]]) -> None:
        self.progress_bar.setVisible(False)
        major_update_ts = self.service.get_major_update_ts()
        enabled = self.mod_list_panel.get_enabled_mods()
        outdated = {m.name for m in enabled
                    if m.update_time is not None
                    and m.update_time < major_update_ts}
        self.override_panel.set_data(
            overrides,
            self._get_mod_configs(),
            outdated,
        )
        if parse_msgs:
            for level, msg in parse_msgs:
                self._log_message(level, prefix_mod_title(msg, self._mod_name_map))
        conflict_count = sum(1 for o in overrides if o.has_conflict)
        warning_count = sum(1 for o in overrides if o.has_warning and not o.has_conflict)
        status_parts = [f"分析完成: {len(overrides)} 个文件被修改"]
        if conflict_count:
            status_parts.append(f"{conflict_count} 个存在冲突")
        if warning_count:
            status_parts.append(f"{warning_count} 个存在数组合并")
        self.statusBar().showMessage(", ".join(status_parts))

        if self._pending_action:
            action = self._pending_action
            self._pending_action = None
            action()

    def _on_analyze_error(self, error: str) -> None:
        self.progress_bar.setVisible(False)
        self.statusBar().showMessage(f"分析失败: {error}")
        self._log_message(ERROR, f"冲突分析失败: {error}")

    def _open_diff(self, rel_path: str) -> None:
        if self._is_analyzing():
            self.statusBar().showMessage("等待冲突分析完成...")
            self._pending_action = lambda: self._open_diff(rel_path)
            return

        from .dialogs.diff import DiffDialog
        array_warnings: list[str] = []
        for info in self.override_panel._data:
            if info.rel_path == rel_path:
                array_warnings = info.array_warnings
                break
        dlg = DiffDialog(
            rel_path=rel_path,
            mod_configs=self._get_mod_configs(),
            array_warnings=array_warnings,
            service=self.service,
        )
        dlg.exec()

    def _execute_merge(self) -> None:
        if self._is_analyzing():
            self.statusBar().showMessage("等待冲突分析完成...")
            self._pending_action = self._execute_merge
            return

        self._save_config()
        mod_configs = self._get_mod_configs()
        if not mod_configs:
            QMessageBox.information(self, "提示", "没有启用的 Mod")
            return

        folder_name = self._ask_synthetic_mod_name()
        if folder_name is None:
            return

        output_path = self.service.local_mod_dir / folder_name
        if output_path.exists():
            shutil.rmtree(output_path)
        output_path.mkdir(parents=True)

        enabled = self.mod_list_panel.get_enabled_mods()
        mod_paths = [(m.name, m.path) for m in enabled]

        self.service.reset_dir_cache()

        self.btn_merge.setText("取消合并")
        self.btn_merge.clicked.disconnect()
        self.btn_merge.clicked.connect(self._cancel_merge)

        self._merge_progress = QProgressDialog(
            "正在合并 JSON 文件...", "取消", 0, 0, self,
        )
        self._merge_progress.setWindowTitle("执行合并")
        self._merge_progress.setMinimumDuration(0)
        self._merge_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._merge_progress.canceled.connect(self._cancel_merge)
        self._merge_progress.show()

        self._merge_output_path = output_path
        self._merge_handle = self.service.merge_async(
            mod_configs,
            output_path,
            mod_paths,
            remap_tables=self._remap_tables,
            cb=self._merge_bridge.task_event.emit,
        )

    _VALID_NAME_RE = re.compile(r'^[A-Za-z0-9_]+$')

    def _ask_synthetic_mod_name(self) -> str | None:
        while True:
            text, ok = QInputDialog.getText(
                self, "命名合成 Mod",
                "输入合成 Mod 文件夹名（仅限英文、数字、下划线）：",
                text=self.service.synthetic_mod_id,
            )
            if not ok:
                return None
            name = text.strip()
            if not name:
                name = self.service.synthetic_mod_id
            if self._VALID_NAME_RE.match(name):
                return name
            QMessageBox.warning(
                self, "名称无效",
                "文件夹名只能包含英文字母、数字和下划线。",
            )

    def _cancel_merge(self) -> None:
        if self._merge_handle:
            self._merge_handle.cancel()
        self.statusBar().showMessage("正在取消...")

    def _restore_merge_btn(self) -> None:
        self.btn_merge.setText("执行合并")
        self.btn_merge.setEnabled(True)
        self.btn_merge.clicked.disconnect()
        self.btn_merge.clicked.connect(self._execute_merge)
        if hasattr(self, '_merge_progress') and self._merge_progress is not None:
            self._merge_progress.close()
            self._merge_progress = None

    def _on_merge_event(self, event: TaskEvent) -> None:
        match event:
            case TaskStage(message=message):
                self._on_merge_stage(message)
            case TaskProgress(completed=completed, total=total):
                self._on_merge_progress(completed, total)
            case TaskDone(result=(results, warnings)):
                self._on_merge_finished(results, warnings)
            case TaskError(message=message):
                self._on_merge_error(message)

    def _on_merge_progress(self, completed: int, total: int) -> None:
        if hasattr(self, '_merge_progress') and self._merge_progress is not None:
            self._merge_progress.setMaximum(total)
            self._merge_progress.setValue(completed)

    def _on_merge_stage(self, msg: str) -> None:
        if hasattr(self, '_merge_progress') and self._merge_progress is not None:
            self._merge_progress.setLabelText(msg)
            self._merge_progress.setMaximum(0)
            self._merge_progress.setValue(0)
        self.statusBar().showMessage(msg)

    def _on_merge_finished(self, results: dict[str, object], warnings: list[str]) -> None:
        self._restore_merge_btn()
        enabled = self.mod_list_panel.get_enabled_mods()
        mod_names = [m.name for m in enabled]
        self.service.generate_info_json(mod_names, self._merge_output_path)

        output = self._merge_output_path
        msg = f"合并完成: {len(results)} 个文件已合并到 {output}"
        self.statusBar().showMessage(msg)
        for w in warnings:
            self._log_message(WARNING, prefix_mod_title(w, self._mod_name_map))
        QMessageBox.information(self, "合并完成", f"已合并 {len(results)} 个文件到:\n{output}")

    def _on_merge_error(self, error: str) -> None:
        self._restore_merge_btn()
        if self._merge_output_path and self._merge_output_path.exists():
            shutil.rmtree(self._merge_output_path, ignore_errors=True)
        self.statusBar().showMessage(f"合并失败: {error}")
        self._log_message(ERROR, f"合并失败: {error}")
        QMessageBox.critical(self, "合并失败", error)

    def _clean(self) -> None:
        synthetic_mods = self.service.scan_synthetic_mods(self.service.local_mod_dir)
        if not synthetic_mods:
            QMessageBox.information(self, "提示", "没有找到合成 Mod")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("清理合成 Mod")
        dlg.setMinimumWidth(400)
        layout = QVBoxLayout(dlg)

        list_widget = QListWidget()
        for folder_name, display_name, _ in synthetic_mods:
            item = QListWidgetItem(f"{display_name}  [{folder_name}]")
            item.setData(Qt.ItemDataRole.UserRole, folder_name)
            item.setCheckState(Qt.CheckState.Unchecked)
            list_widget.addItem(item)
        layout.addWidget(list_widget)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        deleted: list[str] = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item and item.checkState() == Qt.CheckState.Checked:
                folder_name = item.data(Qt.ItemDataRole.UserRole)
                target = self.service.local_mod_dir / folder_name
                if target.exists():
                    shutil.rmtree(target)
                    deleted.append(folder_name)

        if deleted:
            self.statusBar().showMessage(f"已清理 {len(deleted)} 个合成 Mod")
            QMessageBox.information(
                self, "清理完成",
                f"已删除 {len(deleted)} 个合成 Mod：\n" + "\n".join(deleted),
            )
        else:
            QMessageBox.information(self, "提示", "未选择任何合成 Mod")

    def _set_game_path(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择游戏安装目录", self.service.game_path,
        )
        if path:
            self.service.set_game_path(path)
            self.statusBar().showMessage(f"游戏路径已更新: {path}")

    def _set_workshop_path(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择 Workshop 目录", self.service.workshop_path,
        )
        if path:
            self.service.set_workshop_path(path)
            self._load_mods()

    def _set_local_mod_path(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "选择本地 Mod 目录", self.service.local_mod_path,
        )
        if path:
            self.service.set_local_mod_path(path)
            self._load_mods()

    def _show_messages(self, messages: list[tuple[str, str]]) -> None:
        self.log_panel.show_messages(messages)

    def _log_message(self, level: str, msg: str) -> None:
        self.log_panel.log_message(level, msg)

    def _open_json_editor(self, file_path: str, search_key: str = "") -> None:
        path = Path(file_path)
        if not path.exists():
            QMessageBox.warning(self, "提示", f"文件不存在:\n{file_path}")
            return
        from .widgets.code_editor import JsonEditorDialog
        dlg = JsonEditorDialog(path, parent=self, search_key=search_key)
        dlg.exec()

    def _cleanup_remap(self) -> None:
        if self._remap_tables:
            self.service.cleanup_remap(self._remap_tables)
        self._remap_tables = None

    # ── 检查更新 ──

    def _auto_check_update(self) -> None:
        self._do_check_update(silent=True)

    def _show_manual(self) -> None:
        from src.gui.dialogs.manual import ManualDialog
        dlg = ManualDialog(self)
        dlg.exec()

    def _check_update(self) -> None:
        self._do_check_update(silent=False)

    def _do_check_update(self, silent: bool) -> None:
        if self._update_handle and not self._update_handle.is_done:
            return
        self._update_silent = silent
        self._update_handle = self.service.check_update_async(
            cb=self._update_bridge.task_event.emit,
        )

    def _on_update_event(self, event: TaskEvent) -> None:
        match event:
            case TaskDone(result=result):
                self._on_update_checked(
                    result,  # type: ignore[arg-type]
                    self._update_silent,
                )
            case TaskError():
                pass

    def _on_update_checked(self, result: dict[str, str] | None, silent: bool) -> None:
        if result:
            self._show_update_dialog(result)
        elif not silent:
            QMessageBox.information(
                self, "检查更新",
                f"当前版本 v{self.service.app_version} 已是最新版本。",
            )

    def _show_update_dialog(self, info: dict[str, str]) -> None:
        tag = info["tag_name"]
        name = info.get("name") or tag
        body = info.get("body") or ""
        if len(body) > 500:
            body = body[:500] + "..."

        msg = QMessageBox(self)
        msg.setWindowTitle("发现新版本")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            f"新版本 {name} 可用！\n\n"
            f"当前版本: v{self.service.app_version}\n最新版本: {tag}"
        )
        if body:
            msg.setDetailedText(body)
        btn_download = msg.addButton("前往下载", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("稍后再说", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() == btn_download:
            QDesktopServices.openUrl(QUrl(info.get("download_url", "")))

    def closeEvent(self, event: QCloseEvent) -> None:
        self._analyze_timer.stop()
        self._init_timer.stop()
        if self._analyze_handle and not self._analyze_handle.is_done:
            self._analyze_handle.cancel()
            self._analyze_handle.wait(5.0)
        if self._merge_handle and not self._merge_handle.is_done:
            self._merge_handle.cancel()
            self._merge_handle.wait(5.0)
        if self._update_handle and not self._update_handle.is_done:
            self._update_handle.wait(2.0)
        self._cleanup_remap()
        event.accept()
