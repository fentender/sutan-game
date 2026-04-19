"""
主窗口 - 串联所有 GUI 面板和核心逻辑
"""
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
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

from ..config import APP_VERSION, MOD_OVERRIDES_DIR, SCHEMA_DIR, SYNTHETIC_MOD_ID, UserConfig
from ..core.conflict import FileOverrideInfo
from ..core.deployer import generate_info_json, scan_synthetic_mods
from ..core.diagnostics import ERROR, INFO, WARNING, diag
from ..core.id_remapper import RemapTable, remap_mod_configs
from ..core.json_store import JsonStore
from ..core.mod_scanner import scan_all_mods
from ..core.types import MergeMode
from .log_panel import LogPanel, prefix_mod_title
from .mod_detail import ModDetailPanel
from .mod_list import ModListPanel
from .override_panel import OverridePanel
from .workers import AnalyzeWorker, DeltaInitWorker, MergeWorker, StoreInitWorker, UpdateCheckWorker


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("苏丹的游戏 - Mod 合并管理器")
        self.setMinimumSize(1000, 700)

        self.config = UserConfig.load()
        self._worker: MergeWorker | None = None
        self._analyze_worker: AnalyzeWorker | None = None
        self._store_worker: StoreInitWorker | None = None
        self._delta_worker: DeltaInitWorker | None = None
        self._delta_progress: QProgressDialog | None = None
        self._update_worker: UpdateCheckWorker | None = None
        self._pending_action: Callable[[], None] | None = None

        # ID 重分配缓存：remap 只在 _analyze_conflicts 中做一次，
        # 结果直接写入 store 内存，供 AnalyzeWorker / DiffDialog / MergeWorker 共用
        self._remap_tables: dict[str, RemapTable] | None = None

        # 防抖定时器：快速连续操作只触发一次分析
        self._analyze_timer = QTimer()
        self._analyze_timer.setSingleShot(True)
        self._analyze_timer.setInterval(300)
        self._analyze_timer.timeout.connect(self._analyze_conflicts)

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()
        self._load_mods()

        # 启动 3 秒后静默检查更新
        QTimer.singleShot(3000, self._auto_check_update)

    def _setup_menu(self) -> None:
        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        file_menu.addAction("设置游戏路径...", self._set_game_path)
        file_menu.addAction("设置 Workshop 路径...", self._set_workshop_path)
        file_menu.addAction("设置本地 Mod 路径...", self._set_local_mod_path)
        file_menu.addSeparator()
        file_menu.addAction("退出", self.close)

        help_menu = menubar.addMenu("帮助")
        help_menu.addAction(f"检查更新 (当前: v{APP_VERSION})", self._check_update)

    def _setup_ui(self) -> None:
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

        btn_layout.addWidget(QLabel("合并模式:"))

        self.cmb_merge_mode = QComboBox()
        self.cmb_merge_mode.addItem("智能合并", MergeMode.SMART.value)
        self.cmb_merge_mode.addItem("正常合并", MergeMode.NORMAL.value)
        self.cmb_merge_mode.addItem("简单替换", MergeMode.REPLACE.value)
        self.cmb_merge_mode.setToolTip(
            "智能合并：保守策略，数组元素禁止删除，condition/action/result 内允许删除\n"
            "正常合并：全部应用 Mod 的增删改\n"
            "简单替换：直接用 Mod 文件替换，不做字段级合并"
        )
        # 从配置恢复选中项
        mode_idx = self.cmb_merge_mode.findData(self.config.merge_mode)
        if mode_idx >= 0:
            self.cmb_merge_mode.setCurrentIndex(mode_idx)
        self.cmb_merge_mode.currentIndexChanged.connect(self._on_merge_mode_changed)
        btn_layout.addWidget(self.cmb_merge_mode)

        self.btn_deletion_report = QPushButton("查看删减报告")
        self.btn_deletion_report.clicked.connect(self._show_deletion_report)
        btn_layout.addWidget(self.btn_deletion_report)

        main_layout.addLayout(btn_layout)

        # 日志面板
        self.log_panel = LogPanel()
        self.log_panel.file_open_requested.connect(self._open_json_editor)
        main_layout.addWidget(self.log_panel)

        # 信号连接
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

    def _load_mods(self) -> None:
        """加载所有 mod（workshop + 本地目录），然后后台初始化 JsonStore"""
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

        # 读取游戏更新时间（用于过时检测）
        from ..core.steam_time import get_game_update_time, get_steamapps_from_workshop
        steamapps = get_steamapps_from_workshop(self.config.workshop_dir)
        game_update_time = get_game_update_time(steamapps)

        self.mod_list_panel.set_mods(
            mods,
            order=self.config.mod_order or None,
            enabled=self.config.enabled_mods or None,
            merge_modes=self.config.mod_merge_modes or None,
            game_update_time=game_update_time,
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

        # 后台初始化 JsonStore（加载所有 mod 的 config JSON）
        all_mod_configs: list[tuple[str, str, Path]] = [
            (m.mod_id, m.name, m.path / "config")
            for m in self.mod_list_panel._mods
        ]
        self._start_store_init(all_mod_configs)

    def _start_store_init(self, mod_configs: list[tuple[str, str, Path]]) -> None:
        """启动后台 store 初始化"""
        # 取消正在进行的 store 初始化
        if self._store_worker and self._store_worker.isRunning():
            self._store_worker.wait()

        self.statusBar().showMessage("正在加载 JSON 资源...")
        self._store_worker = StoreInitWorker(
            self.config.game_config_path, mod_configs,
        )
        self._store_worker.finished.connect(self._on_store_ready)
        self._store_worker.error.connect(self._on_store_error)
        self._store_worker.start()

    def _on_store_ready(self) -> None:
        """JsonStore 初始化完成，处理错误后触发冲突分析"""
        store = JsonStore.instance()
        failures = store.take_failures()

        if failures:
            # 弹窗让用户处理解析错误
            from .json_fix_dialog import JsonFixDialog
            dialog = JsonFixDialog(failures, parent=self)
            dialog.exec()

            # 收集用户修复的文件路径
            fixed_paths = [
                f.file_path for f in failures
                if dialog.resolutions.get(str(f.file_path), {}).get('action') == 'fixed'
            ]
            if fixed_paths:
                remaining = store.reload(fixed_paths)
                if remaining:
                    # 仍然失败的文件记录到日志
                    for f in remaining:
                        self._log_message(
                            ERROR,
                            prefix_mod_title(
                                f"{f.file_path}: JSON 解析失败 ({f.error_msg})",
                                self._mod_name_map,
                            ),
                        )

        # 展示 JSON 加载阶段的诊断消息
        json_msgs = diag.snapshot("json")
        if json_msgs:
            self._show_messages([
                (level, prefix_mod_title(msg, self._mod_name_map))
                for level, msg in json_msgs
            ])

        self.statusBar().showMessage("JSON 资源加载完成")

        # 计算 Mod 与本体的重叠状态（用于过时风险分级）
        from ..core.overlap import compute_all_overlaps
        all_mod_ids = [m.mod_id for m in self.mod_list_panel._mods]
        overlap_map = compute_all_overlaps(store, all_mod_ids)
        self.mod_list_panel.update_overlap(overlap_map)

        # 加载用户 override 文件
        enabled_ids = [
            mid for mid in self.config.mod_order
            if mid in set(self.config.enabled_mods)
        ]
        store.load_overrides(MOD_OVERRIDES_DIR, enabled_ids)

        # 启动 delta 预计算
        self._start_delta_init(enabled_ids)

    def _refresh_delta(self) -> None:
        """模式变更后重新计算 delta 并刷新覆盖分析"""
        from ..core.merge_cache import MergeCache
        MergeCache.instance().invalidate_all()

        # 合并模式变更，清理所有 override
        store = JsonStore.instance()
        all_mod_ids = set(self.config.mod_order)
        deleted_ids = store.invalidate_overrides(all_mod_ids)
        if deleted_ids:
            names = "、".join(self._mod_name_map.get(mid, mid) for mid in deleted_ids)
            self._log_message(INFO, f"合并模式变更，已清理覆盖编辑: {names}")

        enabled_ids = [
            mid for mid in self.config.mod_order
            if mid in set(self.config.enabled_mods)
        ]
        if not enabled_ids:
            return
        self._start_delta_init(enabled_ids)

    def _start_delta_init(self, mod_ids: list[str]) -> None:
        """启动后台 delta 预计算，并显示进度对话框"""
        if self._delta_worker and self._delta_worker.isRunning():
            self._delta_worker.wait()
        self._delta_worker = DeltaInitWorker(
            mod_ids, schema_dir=SCHEMA_DIR,
            merge_mode=self._get_merge_mode(),
            mod_merge_modes=self._get_mod_merge_modes(),
        )

        self._delta_progress = QProgressDialog(
            "正在预计算差异数据...", "", 0, 0, self,
        )
        self._delta_progress.setWindowTitle("初始化")
        self._delta_progress.setMinimumDuration(0)
        self._delta_progress.setCancelButton(None)
        self._delta_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._delta_progress.show()

        self._delta_worker.progress.connect(self._on_delta_progress)
        self._delta_worker.finished.connect(self._on_delta_ready)
        self._delta_worker.error.connect(self._on_delta_error)
        self._delta_worker.start()

    def _on_delta_progress(self, completed: int, total: int) -> None:
        """更新 delta 预计算进度"""
        if hasattr(self, '_delta_progress') and self._delta_progress is not None:
            self._delta_progress.setMaximum(total)
            self._delta_progress.setValue(completed)

    def _on_delta_ready(self) -> None:
        """Delta 预计算完成"""
        if hasattr(self, '_delta_progress') and self._delta_progress is not None:
            self._delta_progress.close()
            self._delta_progress = None
        self.statusBar().showMessage("初始化完成")
        self._schedule_analyze()

    def _on_delta_error(self, error: str) -> None:
        """Delta 预计算失败"""
        if hasattr(self, '_delta_progress') and self._delta_progress is not None:
            self._delta_progress.close()
            self._delta_progress = None
        self.statusBar().showMessage(f"差异预计算失败: {error}")
        self._log_message(ERROR, f"差异预计算失败: {error}")

    def _on_store_error(self, error: str) -> None:
        """JsonStore 初始化失败"""
        self.statusBar().showMessage(f"JSON 资源加载失败: {error}")
        self._log_message(ERROR, f"JSON 资源加载失败: {error}")

    def _on_merge_mode_changed(self, index: int) -> None:
        mode_value = self.cmb_merge_mode.itemData(index)
        if mode_value:
            self.config.merge_mode = mode_value
            self.config.save()
            self._refresh_delta()

    def _get_merge_mode(self) -> MergeMode:
        """从配置获取当前全局合并模式"""
        try:
            return MergeMode(self.config.merge_mode)
        except ValueError:
            return MergeMode.SMART

    def _get_mod_merge_modes(self) -> dict[str, MergeMode]:
        """从配置获取 per-mod 合并模式覆盖"""
        result: dict[str, MergeMode] = {}
        for mod_id, mode_str in self.config.mod_merge_modes.items():
            try:
                result[mod_id] = MergeMode(mode_str)
            except ValueError:
                continue
        return result

    def _on_mod_merge_mode_changed(self, mod_id: str, mode_value: str) -> None:
        if mode_value:
            self.config.mod_merge_modes[mod_id] = mode_value
        else:
            self.config.mod_merge_modes.pop(mod_id, None)
        self.config.save()
        self._refresh_delta()

    def _show_deletion_report(self) -> None:
        """打开删减报告对话框（懒加载）"""
        from .deletion_report import DeletionReportDialog
        dlg = DeletionReportDialog(
            self.override_panel._data,
            mod_configs=self._get_mod_configs(),
            parent=self,
        )
        dlg.exec()

    def _save_config(self) -> None:
        from ..core.merge_cache import MergeCache
        MergeCache.instance().invalidate_all()
        new_order = self.mod_list_panel.get_mod_order()
        new_enabled = self.mod_list_panel.get_enabled_ids()

        # 计算变化前后的启用有序列表，清理失效的 override
        old_enabled_set = set(self.config.enabled_mods)
        old_enabled_ordered = [mid for mid in self.config.mod_order
                               if mid in old_enabled_set]
        new_enabled_set = set(new_enabled)
        new_enabled_ordered = [mid for mid in new_order
                               if mid in new_enabled_set]

        # 找到第一个差异位置，之后的 mod 全部失效
        min_len = min(len(old_enabled_ordered), len(new_enabled_ordered))
        diverge = min_len
        for i in range(min_len):
            if old_enabled_ordered[i] != new_enabled_ordered[i]:
                diverge = i
                break
        stale_ids = set(old_enabled_ordered[diverge:]) | set(new_enabled_ordered[diverge:])

        store = JsonStore.instance()
        deleted_ids = store.invalidate_overrides(stale_ids)
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

    def _schedule_analyze(self) -> None:
        """防抖触发冲突分析（重置 300ms 定时器）"""
        self._analyze_timer.start()

    def _get_mod_configs(self) -> list[tuple[str, str, Path]]:
        """获取启用的 mod 配置路径列表"""
        enabled = self.mod_list_panel.get_enabled_mods()
        return [
            (m.mod_id, m.name, m.path / "config")
            for m in enabled
        ]

    def _analyze_conflicts(self) -> None:
        """异步分析冲突（由防抖定时器触发）"""
        # 合并期间不重新分析
        if self._worker and self._worker.isRunning():
            return

        # 取消正在进行的分析，等待线程退出（避免 QThread 被销毁时仍在运行）
        if self._analyze_worker and self._analyze_worker.isRunning():
            self._analyze_worker.done.disconnect()
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
        _remap_msgs, remap_tables = remap_mod_configs(mod_configs)
        self._remap_tables = remap_tables

        # 展示 remap 日志
        remap_messages = diag.snapshot("remap")
        if remap_messages:
            self._show_messages([
                (level, prefix_mod_title(msg, self._mod_name_map))
                for level, msg in remap_messages
            ])

        # 重新预计算 delta（remap 可能修改了 store 数据）
        from ..core.delta_store import ModDelta
        enabled_ids = [mod_id for mod_id, _, _ in mod_configs]
        ModDelta.invalidate()
        ModDelta.init(enabled_ids, schema_dir=SCHEMA_DIR,
                      merge_mode=self._get_merge_mode(),
                      mod_merge_modes=self._get_mod_merge_modes())

        self.statusBar().showMessage("正在分析覆盖情况...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        self._analyze_worker = AnalyzeWorker(
            self._get_mod_configs(), SCHEMA_DIR,
        )
        self._analyze_worker.done.connect(self._on_analyze_finished)
        self._analyze_worker.error.connect(self._on_analyze_error)
        self._analyze_worker.start()

    def _on_analyze_finished(self, overrides: list[FileOverrideInfo],
                             parse_msgs: list[tuple[str, str]]) -> None:
        self.progress_bar.setVisible(False)
        self.override_panel.set_data(
            overrides,
            self._get_mod_configs(),
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

        # 执行挂起的操作
        if self._pending_action:
            action = self._pending_action
            self._pending_action = None
            action()

    def _on_analyze_error(self, error: str) -> None:
        self.progress_bar.setVisible(False)
        self.statusBar().showMessage(f"分析失败: {error}")
        self._log_message(ERROR, f"冲突分析失败: {error}")

    def _open_diff(self, rel_path: str) -> None:
        """打开 Diff 对比窗口（分析进行中则排队等待）"""
        if self._analyze_worker and self._analyze_worker.isRunning():
            self.statusBar().showMessage("等待冲突分析完成...")
            self._pending_action = lambda: self._open_diff(rel_path)
            return

        from .diff_dialog import DiffDialog
        # 查找该文件的数组合并警告
        array_warnings: list[str] = []
        for info in self.override_panel._data:
            if info.rel_path == rel_path:
                array_warnings = info.array_warnings
                break
        dlg = DiffDialog(
            rel_path=rel_path,
            mod_configs=self._get_mod_configs(),
            array_warnings=array_warnings,
        )
        dlg.exec()

    def _execute_merge(self) -> None:
        """执行合并"""
        if self._analyze_worker and self._analyze_worker.isRunning():
            self.statusBar().showMessage("等待冲突分析完成...")
            self._pending_action = self._execute_merge
            return

        self._save_config()
        mod_configs = self._get_mod_configs()
        if not mod_configs:
            QMessageBox.information(self, "提示", "没有启用的 Mod")
            return

        # 弹出命名输入框
        folder_name = self._ask_synthetic_mod_name()
        if folder_name is None:
            return  # 用户取消

        # 输出到本地 Mod 目录
        output_path = self.config.local_mod_dir / folder_name
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
            mod_configs,
            output_path,
            mod_paths,
            remap_tables=self._remap_tables,
        )
        self._worker.progress.connect(lambda msg: self.statusBar().showMessage(msg))
        self._worker.done.connect(self._on_merge_finished)
        self._worker.error.connect(self._on_merge_error)
        self._worker.start()

    _VALID_NAME_RE = re.compile(r'^[A-Za-z0-9_]+$')

    def _ask_synthetic_mod_name(self) -> str | None:
        """弹出输入框让用户命名合成 Mod 文件夹。返回 None 表示取消。"""
        while True:
            text, ok = QInputDialog.getText(
                self, "命名合成 Mod",
                "输入合成 Mod 文件夹名（仅限英文、数字、下划线）：",
                text=SYNTHETIC_MOD_ID,
            )
            if not ok:
                return None
            name = text.strip()
            if not name:
                name = SYNTHETIC_MOD_ID
            if self._VALID_NAME_RE.match(name):
                return name
            QMessageBox.warning(
                self, "名称无效",
                "文件夹名只能包含英文字母、数字和下划线。",
            )

    def _cancel_merge(self) -> None:
        """取消正在进行的合并"""
        if self._worker:
            self._worker.cancel()
        self.statusBar().showMessage("正在取消...")

    def _restore_merge_btn(self) -> None:
        """恢复合并按钮到初始状态"""
        self.btn_merge.setText("执行合并")
        self.btn_merge.setEnabled(True)
        self.btn_merge.clicked.disconnect()
        self.btn_merge.clicked.connect(self._execute_merge)
        self.progress_bar.setVisible(False)

    def _on_merge_finished(self, results: dict[str, object], warnings: list[str]) -> None:
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

    def _on_merge_error(self, error: str) -> None:
        self._restore_merge_btn()
        # 清理合并失败后残留的半成品输出目录
        if self._merge_output_path and self._merge_output_path.exists():
            shutil.rmtree(self._merge_output_path, ignore_errors=True)
        self.statusBar().showMessage(f"合并失败: {error}")
        self._log_message(ERROR, f"合并失败: {error}")
        QMessageBox.critical(self, "合并失败", error)

    def _clean(self) -> None:
        """清理合成 Mod：扫描所有带标记的合成 Mod 并让用户选择删除"""
        synthetic_mods = scan_synthetic_mods(self.config.local_mod_dir)
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
                target = self.config.local_mod_dir / folder_name
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
        path = QFileDialog.getExistingDirectory(self, "选择游戏安装目录", self.config.game_path)
        if path:
            self.config.game_path = path
            self.config.save()
            self.statusBar().showMessage(f"游戏路径已更新: {path}")

    def _set_workshop_path(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 Workshop 目录", self.config.workshop_path)
        if path:
            self.config.workshop_path = path
            self.config.save()
            self._load_mods()

    def _set_local_mod_path(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择本地 Mod 目录", self.config.local_mod_path)
        if path:
            self.config.local_mod_path = path
            self.config.save()
            self._load_mods()

    def _show_messages(self, messages: list[tuple[str, str]]) -> None:
        """显示或隐藏日志面板，messages 为 [(level, msg), ...]"""
        self.log_panel.show_messages(messages)

    def _log_message(self, level: str, msg: str) -> None:
        """追加一条日志到面板"""
        self.log_panel.log_message(level, msg)

    def _open_json_editor(self, file_path: str, search_key: str = "") -> None:
        """打开 JSON 编辑器（由日志面板双击触发）"""
        path = Path(file_path)
        if not path.exists():
            QMessageBox.warning(self, "提示", f"文件不存在:\n{file_path}")
            return
        from .json_editor import JsonEditorDialog
        dlg = JsonEditorDialog(path, parent=self, search_key=search_key)
        dlg.exec()

    def _cleanup_remap(self) -> None:
        """清理 remap 状态"""
        self._remap_tables = None

    # ── 检查更新 ──

    def _auto_check_update(self) -> None:
        """启动时自动检查更新（静默模式）"""
        self._do_check_update(silent=True)

    def _check_update(self) -> None:
        """用户手动触发检查更新"""
        self._do_check_update(silent=False)

    def _do_check_update(self, silent: bool) -> None:
        if self._update_worker and self._update_worker.isRunning():
            return
        self._update_worker = UpdateCheckWorker()
        self._update_worker.done.connect(
            lambda result: self._on_update_checked(result, silent)
        )
        self._update_worker.start()

    def _on_update_checked(self, result: dict[str, str] | None, silent: bool) -> None:
        if result:
            self._show_update_dialog(result)
        elif not silent:
            QMessageBox.information(self, "检查更新",
                                    f"当前版本 v{APP_VERSION} 已是最新版本。")

    def _show_update_dialog(self, info: dict[str, str]) -> None:
        tag = info["tag_name"]
        name = info.get("name") or tag
        body = info.get("body") or ""
        if len(body) > 500:
            body = body[:500] + "..."

        msg = QMessageBox(self)
        msg.setWindowTitle("发现新版本")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(f"新版本 {name} 可用！\n\n当前版本: v{APP_VERSION}\n最新版本: {tag}")
        if body:
            msg.setDetailedText(body)
        btn_download = msg.addButton("前往下载", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("稍后再说", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() == btn_download:
            QDesktopServices.openUrl(QUrl(info.get("download_url", "")))

    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭窗口时协作式等待工作线程结束"""
        self._analyze_timer.stop()
        if self._store_worker is not None and self._store_worker.isRunning():
            self._store_worker.wait(5000)
        if self._analyze_worker is not None and self._analyze_worker.isRunning():
            self._analyze_worker.cancel()
            self._analyze_worker.wait(5000)
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(5000)
        if self._update_worker is not None and self._update_worker.isRunning():
            self._update_worker.wait(2000)
        self._cleanup_remap()
        event.accept()
