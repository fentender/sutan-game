"""
后台工作线程 - Store 初始化、合并、冲突分析、Schema 生成
"""
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from src.core.api import AppService, MergeMode, RemapTable, diag


class _MergeCancelled(Exception):
    pass


class BaseWorker(QThread):
    """统一异常处理的工作线程基类"""
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def _check_cancel(self) -> None:
        if self._cancelled.is_set():
            raise _MergeCancelled()

    def run(self) -> None:
        try:
            self._run()
        except _MergeCancelled:
            pass
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")

    def _run(self) -> None:
        raise NotImplementedError


class StoreInitWorker(BaseWorker):
    """后台初始化 DataManager"""
    progress = Signal(int, int)

    def __init__(self, game_config_path: Path,
                 mod_configs: list[tuple[str, str, Path]],
                 service: AppService,
                 history_dir: Path | None = None,
                 mod_update_times: dict[str, int] | None = None,
                 overrides_dir: Path | None = None,
                 enabled_mod_ids: list[str] | None = None) -> None:
        super().__init__()
        self.game_config_path = game_config_path
        self.mod_configs = mod_configs
        self.history_dir = history_dir
        self.mod_update_times = mod_update_times
        self.overrides_dir = overrides_dir
        self.enabled_mod_ids = enabled_mod_ids
        self._service = service

    def _run(self) -> None:
        self._service.init_store(
            self.game_config_path, self.mod_configs,
            history_dir=self.history_dir,
            mod_update_times=self.mod_update_times,
            overrides_dir=self.overrides_dir,
            enabled_mod_ids=self.enabled_mod_ids,
            on_progress=self.progress.emit,
        )


class DeltaInitWorker(BaseWorker):
    """后台预计算所有 mod 的 delta"""
    progress = Signal(int, int)

    def __init__(self, mod_ids: list[str],
                 merge_mode: MergeMode = MergeMode.SMART,
                 mod_merge_modes: dict[str, MergeMode] | None = None,
                 service: AppService | None = None) -> None:
        super().__init__()
        self.mod_ids = mod_ids
        self.merge_mode = merge_mode
        self.mod_merge_modes = mod_merge_modes
        self._service = service

    def _run(self) -> None:
        assert self._service is not None
        self._service.init_delta(
            self.mod_ids,
            progress_cb=self.progress.emit,
            merge_mode=self.merge_mode,
            mod_merge_modes=self.mod_merge_modes,
        )


class MergeWorker(BaseWorker):
    """后台合并线程"""
    done = Signal(dict, list)
    progress = Signal(int, int)
    stage = Signal(str)

    def __init__(self, mod_configs: list[tuple[str, str, Path]],
                 output_path: Path, mod_paths: list[tuple[str, Path]],
                 remap_tables: dict[str, RemapTable] | None = None,
                 service: AppService | None = None) -> None:
        super().__init__()
        self.mod_configs = mod_configs
        self.output_path = output_path
        self.mod_paths = mod_paths
        self.remap_tables = remap_tables
        self._service = service

    def _run(self) -> None:
        assert self._service is not None
        self.stage.emit("正在合并 JSON 文件...")
        results = self._service.merge_all_files(
            self.mod_configs,
            self.output_path / "config",
            cancel_check=self._check_cancel,
            progress_cb=lambda c, t: self.progress.emit(c, t),
        )
        self._check_cancel()

        self._service.copy_failed_files(self.mod_configs, self.output_path / "config")

        warnings_snapshot = [msg for _, msg in diag.snapshot("merge")]
        self.stage.emit("正在复制资源文件...")
        self._service.copy_resources(self.mod_paths, self.output_path,
                                     cancel_check=self._check_cancel,
                                     remap_tables=self.remap_tables)
        self.done.emit(results, warnings_snapshot)


class AnalyzeWorker(BaseWorker):
    """后台冲突分析线程"""
    done = Signal(list, list)

    def __init__(self, mod_configs: list[tuple[str, str, Path]],
                 schema_dir: Path,
                 service: AppService) -> None:
        super().__init__()
        self.mod_configs = mod_configs
        self.schema_dir = schema_dir
        self._service = service

    def _run(self) -> None:
        diag.snapshot("parse")
        overrides = self._service.analyze_all_overrides(
            self.mod_configs,
            schema_dir=self.schema_dir,
            cancel_check=self._check_cancel,
        )
        self._check_cancel()

        parse_msgs = diag.snapshot("parse")
        self.done.emit(overrides, parse_msgs)


class SchemaWorker(BaseWorker):
    """后台 Schema 生成线程"""
    progress = Signal(int, int, str)

    def __init__(self, config_dir: Path, schema_dir: Path,
                 service: AppService) -> None:
        super().__init__()
        self.config_dir = config_dir
        self.schema_dir = schema_dir
        self._service = service

    def _run(self) -> None:
        self._service.generate_schemas(
            self.config_dir, self.schema_dir,
            progress_cb=lambda cur, total, name: self.progress.emit(cur, total, name),
        )


class UpdateCheckWorker(BaseWorker):
    """后台检查更新线程"""
    done = Signal(object)

    def __init__(self, service: AppService) -> None:
        super().__init__()
        self._service = service

    def _run(self) -> None:
        self.done.emit(self._service.check_for_update(timeout=8))

