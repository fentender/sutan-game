"""
后台工作线程 - Store 初始化、合并、冲突分析、Schema 生成
"""
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..config import SCHEMA_DIR
from ..core.conflict import analyze_all_overrides
from ..core.deployer import copy_resources
from ..core.diagnostics import diag
from ..core.id_remapper import RemapTable
from ..core.json_store import JsonStore
from ..core.merger import merge_all_files


class _MergeCancelled(Exception):
    """合并被用户取消"""
    pass


class CancellableWorker(QThread):
    """可取消的工作线程基类"""
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        """请求取消"""
        self._cancelled.set()

    def _check_cancel(self) -> None:
        """检查取消标志，已取消则抛出异常"""
        if self._cancelled.is_set():
            raise _MergeCancelled()


class StoreInitWorker(QThread):
    """后台初始化 JsonStore"""
    error = Signal(str)

    def __init__(self, game_config_path: Path,
                 mod_configs: list[tuple[str, str, Path]]) -> None:
        super().__init__()
        self.game_config_path = game_config_path
        self.mod_configs = mod_configs

    def run(self) -> None:
        try:
            store = JsonStore.instance()
            store.init(self.game_config_path, self.mod_configs)
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


class DeltaInitWorker(QThread):
    """后台预计算所有 mod 的 delta"""
    progress = Signal(int, int)  # completed, total
    error = Signal(str)

    def __init__(self, mod_ids: list[str],
                 schema_dir: Path | None = None) -> None:
        super().__init__()
        self.mod_ids = mod_ids
        self.schema_dir = schema_dir

    def run(self) -> None:
        from ..core.delta_store import ModDelta
        try:
            ModDelta.init(
                self.mod_ids,
                schema_dir=self.schema_dir,
                progress_cb=self.progress.emit,
            )
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


class MergeWorker(CancellableWorker):
    """后台合并线程"""
    done = Signal(dict, list)  # 合并结果, 警告列表
    progress = Signal(str)

    def __init__(self, mod_configs: list[tuple[str, str, Path]],
                 output_path: Path, mod_paths: list[tuple[str, Path]],
                 allow_deletions: bool = False,
                 remap_tables: dict[str, RemapTable] | None = None) -> None:
        super().__init__()
        self.mod_configs = mod_configs
        self.output_path = output_path
        self.mod_paths = mod_paths
        self.allow_deletions = allow_deletions
        self.remap_tables = remap_tables

    def run(self) -> None:
        try:
            self.progress.emit("正在合并 JSON 文件...")
            results = merge_all_files(
                self.mod_configs,
                self.output_path / "config",
                schema_dir=SCHEMA_DIR,
                allow_deletions=self.allow_deletions,
                cancel_check=self._check_cancel,
            )
            self._check_cancel()

            # 在工作线程内快照警告，避免跨线程竞态
            warnings_snapshot = [msg for _, msg in diag.snapshot("merge")]
            self.progress.emit("正在复制资源文件...")
            copy_resources(self.mod_paths, self.output_path,
                           cancel_check=self._check_cancel,
                           remap_tables=self.remap_tables)
            self.done.emit(results, warnings_snapshot)
        except _MergeCancelled:
            pass
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


class AnalyzeWorker(CancellableWorker):
    """后台冲突分析线程"""
    done = Signal(list, list)  # overrides, parse_messages

    def __init__(self, mod_configs: list[tuple[str, str, Path]],
                 schema_dir: Path) -> None:
        super().__init__()
        self.mod_configs = mod_configs
        self.schema_dir = schema_dir

    def run(self) -> None:
        try:
            diag.snapshot("parse")
            overrides = analyze_all_overrides(
                self.mod_configs,
                schema_dir=self.schema_dir,
                cancel_check=self._check_cancel,
            )
            self._check_cancel()

            parse_msgs = diag.snapshot("parse")
            self.done.emit(overrides, parse_msgs)
        except _MergeCancelled:
            pass
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


class SchemaWorker(QThread):
    """后台 Schema 生成线程"""
    progress = Signal(int, int, str)  # current, total, name
    error = Signal(str)

    def __init__(self, config_dir: Path, schema_dir: Path) -> None:
        super().__init__()
        self.config_dir = config_dir
        self.schema_dir = schema_dir

    def run(self) -> None:
        try:
            from ..core.schema_generator import generate_all
            generate_all(
                str(self.config_dir), str(self.schema_dir),
                progress_callback=lambda cur, total, name: self.progress.emit(cur, total, name),
            )
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


class UpdateCheckWorker(QThread):
    """后台检查更新线程"""
    done = Signal(object)  # dict（有新版本）或 None

    def run(self) -> None:
        from ..core.updater import check_for_update
        self.done.emit(check_for_update(timeout=8))
