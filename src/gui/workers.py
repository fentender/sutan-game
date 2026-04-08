"""
后台工作线程 - 合并、冲突分析、Schema 生成
"""
import threading

from PySide6.QtCore import QThread, Signal

from ..config import SCHEMA_DIR, MOD_OVERRIDES_DIR
from ..core.mod_scanner import scan_all_mods
from ..core.merger import merge_all_files
from ..core.diagnostics import diag
from ..core.conflict import analyze_all_overrides
from ..core.deployer import copy_resources


class _MergeCancelled(Exception):
    """合并被用户取消"""
    pass


class CancellableWorker(QThread):
    """可取消的工作线程基类"""
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self._cancelled = threading.Event()

    def cancel(self):
        """请求取消"""
        self._cancelled.set()

    def _check_cancel(self):
        """检查取消标志，已取消则抛出异常"""
        if self._cancelled.is_set():
            raise _MergeCancelled()


class MergeWorker(CancellableWorker):
    """后台合并线程"""
    finished = Signal(dict, list)  # 合并结果, 警告列表
    progress = Signal(str)

    def __init__(self, game_config_path, mod_configs, output_path, mod_paths,
                 allow_deletions=False):
        super().__init__()
        self.game_config_path = game_config_path
        self.mod_configs = mod_configs
        self.output_path = output_path
        self.mod_paths = mod_paths
        self.allow_deletions = allow_deletions

    def run(self):
        try:
            self.progress.emit("正在合并 JSON 文件...")
            results = merge_all_files(
                self.game_config_path,
                self.mod_configs,
                self.output_path / "config",
                schema_dir=SCHEMA_DIR,
                allow_deletions=self.allow_deletions,
                cancel_check=self._check_cancel,
                overrides_dir=MOD_OVERRIDES_DIR,
            )
            self._check_cancel()
            # 在工作线程内快照警告，避免跨线程竞态
            warnings_snapshot = [msg for _, msg in diag.snapshot("merge")]
            self.progress.emit("正在复制资源文件...")
            copy_resources(self.mod_paths, self.output_path,
                           cancel_check=self._check_cancel)
            self.finished.emit(results, warnings_snapshot)
        except _MergeCancelled:
            pass
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


class AnalyzeWorker(CancellableWorker):
    """后台冲突分析线程"""
    finished = Signal(list, list)  # overrides, parse_messages

    def __init__(self, game_config_path, mod_configs, schema_dir):
        super().__init__()
        self.game_config_path = game_config_path
        self.mod_configs = mod_configs
        self.schema_dir = schema_dir

    def run(self):
        try:
            diag.snapshot("parse")
            overrides = analyze_all_overrides(
                self.game_config_path,
                self.mod_configs,
                schema_dir=self.schema_dir,
                cancel_check=self._check_cancel,
            )
            parse_msgs = diag.snapshot("parse")
            self.finished.emit(overrides, parse_msgs)
        except _MergeCancelled:
            pass
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


class SchemaWorker(QThread):
    """后台 Schema 生成线程"""
    progress = Signal(int, int, str)  # current, total, name
    finished = Signal()
    error = Signal(str)

    def __init__(self, config_dir, schema_dir):
        super().__init__()
        self.config_dir = config_dir
        self.schema_dir = schema_dir

    def run(self):
        try:
            from ..core.schema_generator import generate_all
            generate_all(
                str(self.config_dir), str(self.schema_dir),
                progress_callback=lambda cur, total, name: self.progress.emit(cur, total, name),
            )
            self.finished.emit()
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")
