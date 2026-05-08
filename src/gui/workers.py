"""
后台工作线程 - Store 初始化、合并、冲突分析、Schema 生成
"""
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from src.core.infra.diagnostics import diag
from src.core.infra.types import MergeMode
from src.core.mod.id_remap import RemapTable
from src.core.service import MergeService

from ..config import SCHEMA_DIR


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

    def __init__(self, game_config_path: Path,
                 mod_configs: list[tuple[str, str, Path]],
                 service: MergeService,
                 history_dir: Path | None = None,
                 mod_update_times: dict[str, int] | None = None) -> None:
        super().__init__()
        self.game_config_path = game_config_path
        self.mod_configs = mod_configs
        self.history_dir = history_dir
        self.mod_update_times = mod_update_times
        self._service = service

    def _run(self) -> None:
        self._service.init_store(
            self.game_config_path, self.mod_configs,
            history_dir=self.history_dir,
            mod_update_times=self.mod_update_times,
        )


class DeltaInitWorker(BaseWorker):
    """后台预计算所有 mod 的 delta"""
    progress = Signal(int, int)  # completed, total

    def __init__(self, mod_ids: list[str],
                 schema_dir: Path | None = None,
                 merge_mode: MergeMode = MergeMode.SMART,
                 mod_merge_modes: dict[str, MergeMode] | None = None,
                 service: MergeService | None = None) -> None:
        super().__init__()
        self.mod_ids = mod_ids
        self.schema_dir = schema_dir
        self.merge_mode = merge_mode
        self.mod_merge_modes = mod_merge_modes
        self._service = service

    def _run(self) -> None:
        assert self._service is not None
        self._service.init_delta(
            self.mod_ids,
            schema_dir=self.schema_dir,
            progress_cb=self.progress.emit,
            merge_mode=self.merge_mode,
            mod_merge_modes=self.mod_merge_modes,
        )


class MergeWorker(BaseWorker):
    """后台合并线程"""
    done = Signal(dict, list)  # 合并结果, 警告列表
    progress = Signal(int, int)  # completed, total
    stage = Signal(str)  # 阶段描述

    def __init__(self, mod_configs: list[tuple[str, str, Path]],
                 output_path: Path, mod_paths: list[tuple[str, Path]],
                 remap_tables: dict[str, RemapTable] | None = None,
                 service: MergeService | None = None) -> None:
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
            schema_dir=SCHEMA_DIR,
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
    done = Signal(list, list)  # overrides, parse_messages

    def __init__(self, mod_configs: list[tuple[str, str, Path]],
                 schema_dir: Path,
                 service: MergeService) -> None:
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
    progress = Signal(int, int, str)  # current, total, name

    def __init__(self, config_dir: Path, schema_dir: Path) -> None:
        super().__init__()
        self.config_dir = config_dir
        self.schema_dir = schema_dir

    def _run(self) -> None:
        from src.core.schema.generator import generate_all
        generate_all(
            str(self.config_dir), str(self.schema_dir),
            progress_callback=lambda cur, total, name: self.progress.emit(cur, total, name),
        )


class UpdateCheckWorker(BaseWorker):
    """后台检查更新线程"""
    done = Signal(object)  # dict（有新版本）或 None

    def _run(self) -> None:
        from src.core.platform.updater import check_for_update
        self.done.emit(check_for_update(timeout=8))
