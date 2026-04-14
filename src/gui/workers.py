"""
后台工作线程 - 合并、冲突分析、Schema 生成
"""
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ..config import MOD_OVERRIDES_DIR, SCHEMA_DIR
from ..core.conflict import analyze_all_overrides
from ..core.deployer import copy_resources
from ..core.diagnostics import diag
from ..core.id_remapper import RemapTable
from ..core.json_parser import clear_json_cache, dump_json, load_json
from ..core.merger import ParseFailure, merge_all_files, raw_copy_file


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


class MergeWorker(CancellableWorker):
    """后台合并线程"""
    finished = Signal(dict, list)  # 合并结果, 警告列表
    progress = Signal(str)
    parse_errors = Signal(list)  # list[ParseFailure]

    def __init__(self, game_config_path: Path, mod_configs: list[tuple[str, str, Path]],
                 output_path: Path, mod_paths: list[tuple[str, Path]],
                 allow_deletions: bool = False,
                 remap_tables: dict[str, RemapTable] | None = None) -> None:
        super().__init__()
        self.game_config_path = game_config_path
        self.mod_configs = mod_configs
        self.output_path = output_path
        self.mod_paths = mod_paths
        self.allow_deletions = allow_deletions
        self.remap_tables = remap_tables
        self._resume_event = threading.Event()
        self._error_resolutions: dict[str, dict[str, str]] | None = None

    def set_error_resolution(self, resolutions: dict[str, dict[str, str]]) -> None:
        """主线程回传用户处理结果后唤醒 worker。

        resolutions: {file_path_str: {'action': 'fixed'|'ignored'}}
        """
        self._error_resolutions = resolutions
        self._resume_event.set()

    def run(self) -> None:
        try:
            self.progress.emit("正在合并 JSON 文件...")
            results, parse_failures = merge_all_files(
                self.game_config_path,
                self.mod_configs,
                self.output_path / "config",
                schema_dir=SCHEMA_DIR,
                allow_deletions=self.allow_deletions,
                cancel_check=self._check_cancel,
                overrides_dir=MOD_OVERRIDES_DIR,
            )
            self._check_cancel()

            # 有解析失败的文件时，通知主线程弹窗，阻塞等待用户处理
            if parse_failures:
                self._resume_event.clear()
                self.parse_errors.emit(parse_failures)
                self._resume_event.wait()
                self._check_cancel()
                self._apply_error_resolutions(parse_failures)

            # 在工作线程内快照警告，避免跨线程竞态
            warnings_snapshot = [msg for _, msg in diag.snapshot("merge")]
            self.progress.emit("正在复制资源文件...")
            copy_resources(self.mod_paths, self.output_path,
                           cancel_check=self._check_cancel,
                           remap_tables=self.remap_tables)
            self.finished.emit(results, warnings_snapshot)
        except _MergeCancelled:
            pass
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")

    def _apply_error_resolutions(self, failures: list[ParseFailure]) -> None:
        """根据用户选择处理解析失败的文件"""
        if not self._error_resolutions:
            # 没有 resolutions（如用户直接关闭窗口），全部原样复制
            for f in failures:
                raw_copy_file(f.file_path, f.rel_path, self.output_path / "config")
            return

        for failure in failures:
            key = str(failure.file_path)
            resolution = self._error_resolutions.get(key, {})
            action = resolution.get('action', 'ignored')

            if action == 'fixed':
                # 用户已修复并保存到原文件，清除缓存后重新加载
                clear_json_cache()
                try:
                    data = load_json(failure.file_path)
                    out_file = self.output_path / "config" / failure.rel_path
                    dump_json(data, out_file)
                except Exception:
                    # 重新加载仍失败，原样复制
                    raw_copy_file(failure.file_path, failure.rel_path,
                                  self.output_path / "config")
            else:
                # 忽视：原样复制到输出
                raw_copy_file(failure.file_path, failure.rel_path,
                              self.output_path / "config")


class AnalyzeWorker(CancellableWorker):
    """后台冲突分析线程"""
    finished = Signal(list, list)  # overrides, parse_messages
    parse_errors = Signal(list)    # list[ParseFailure]

    def __init__(self, game_config_path: Path, mod_configs: list[tuple[str, str, Path]],
                 schema_dir: Path) -> None:
        super().__init__()
        self.game_config_path = game_config_path
        self.mod_configs = mod_configs
        self.schema_dir = schema_dir
        self._resume_event = threading.Event()
        self._error_resolutions: dict[str, dict[str, str]] | None = None

    def set_error_resolution(self, resolutions: dict[str, dict[str, str]]) -> None:
        """主线程回传用户处理结果后唤醒 worker。"""
        self._error_resolutions = resolutions
        self._resume_event.set()

    def run(self) -> None:
        try:
            diag.snapshot("parse")
            overrides, parse_failures = analyze_all_overrides(
                self.game_config_path,
                self.mod_configs,
                schema_dir=self.schema_dir,
                cancel_check=self._check_cancel,
            )
            self._check_cancel()

            # 有解析失败的文件时，通知主线程弹窗，阻塞等待用户处理
            if parse_failures:
                self._resume_event.clear()
                self.parse_errors.emit(parse_failures)
                self._resume_event.wait()
                self._check_cancel()
                # 用户修复后重新分析
                if self._error_resolutions and any(
                    r.get('action') == 'fixed'
                    for r in self._error_resolutions.values()
                ):
                    clear_json_cache()
                    diag.snapshot("parse")
                    overrides, _ = analyze_all_overrides(
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
            self.finished.emit()
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


class UpdateCheckWorker(QThread):
    """后台检查更新线程"""
    finished = Signal(object)  # dict（有新版本）或 None

    def run(self) -> None:
        from ..core.updater import check_for_update
        self.finished.emit(check_for_update(timeout=8))
