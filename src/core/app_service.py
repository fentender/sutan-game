"""
AppService — 应用顶层服务，GUI 的唯一入口。

管理所有逻辑模块初始化、配置读写、业务常量暴露。
GUI 不接触 UserConfig / core 内部模块，只通过此类交互。
"""
import sys
import threading
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from sultan_core.delta import DeltaDict, compute_delta
from sultan_core.json import JsonDoc, ParseError

from ..config import (
    APP_VERSION,
    HISTORY_DIR,
    MERGED_OUTPUT_PATH,
    MOD_OVERRIDES_DIR,
    SCHEMA_DIR,
    SYNTHETIC_MOD_ID,
    UserConfig,
    detect_game_path,
    detect_workshop_path,
    infer_workshop_path_from_game,
)
from .data_manager import DataManager
from .infra.async_task import (
    AsyncTaskHandle,
    TaskCallback,
    TaskDone,
    TaskError,
    TaskProgress,
    _Cancelled,
    async_task,
)
from .infra.diagnostics import diag
from .infra.types import (
    CancelCheck,
    JsonObject,
    MergeMode,
    ParseFailure,
    ProgressCallback,
)
from .json import classify_json as _classify_json
from .json.parser import reset_dir_cache as _reset_dir_cache
from .merge.cache import FileMergeState
from .merge.delta import ModDelta, _to_cpp_mode
from .merge.merger import (
    MergeResult,
    copy_failed_files as _copy_failed_files,
    merge_file as _merge_file,
)
from .mod.conflict import FileOverrideInfo, analyze_all_overrides as _analyze_all_overrides
from .mod.deployer import (
    copy_resources as _copy_resources,
    generate_info_json as _generate_info_json,
    scan_synthetic_mods as _scan_synthetic_mods,
)
from .mod.id_remap import RemapTable, remap_mod_configs as _remap_mod_configs
from .mod.overlap import compute_all_overlaps as _compute_all_overlaps
from .mod.scanner import ModInfo, scan_all_mods as _scan_all_mods
from .mod_manager import ModManager
from .platform.steam import (
    MAJOR_UPDATE_TS,
    get_game_update_time as _get_game_update_time,
    get_steamapps_from_workshop as _get_steamapps_from_workshop,
)
from .schema.loader import (
    get_schema_root_key as _get_schema_root_key,
    load_schemas as _load_schemas,
    resolve_schema as _resolve_schema,
)


class InitState(Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    GENERATING_SCHEMAS = "generating_schemas"
    LOADING_JSON = "loading_json"
    AWAITING_FIX = "awaiting_fix"
    COMPUTING_DELTA = "computing_delta"
    ANALYZING = "analyzing"
    READY = "ready"
    ERROR = "error"


class AppService:
    """应用顶层服务 — GUI 唯一入口。"""

    def __init__(self) -> None:
        if not getattr(sys, 'frozen', False):
            from .infra import profiler
            profiler.enable()

        self._config = UserConfig.load()
        self._mod_manager = ModManager(self._config)

        # ── 初始化状态机 ──
        self._init_state: InitState = InitState.IDLE
        self._init_progress: tuple[int, int] = (0, 0)
        self._init_schema_progress: tuple[int, int, str] = (0, 0, "")
        self._init_error: str | None = None
        self._mods: list[ModInfo] = []
        self._mod_configs: list[tuple[str, str, Path]] = []
        self._parse_failures: list[ParseFailure] = []
        self._overrides: list[FileOverrideInfo] = []
        self._overlap_map: dict[str, bool] = {}

        if self.paths_valid():
            self._init_state = InitState.SCANNING
            self._do_scan()
            self._init_state = InitState.LOADING_JSON
            threading.Thread(target=self._async_init_chain, daemon=True).start()

    # ══════════════════════════════════════════════════════════════
    # 初始化状态机
    # ══════════════════════════════════════════════════════════════

    @property
    def init_state(self) -> InitState:
        return self._init_state

    @property
    def init_progress(self) -> tuple[int, int]:
        return self._init_progress

    @property
    def init_schema_progress(self) -> tuple[int, int, str]:
        return self._init_schema_progress

    @property
    def init_error(self) -> str | None:
        return self._init_error

    @property
    def mods(self) -> list[ModInfo]:
        return self._mods

    @property
    def parse_failures(self) -> list[ParseFailure]:
        return self._parse_failures

    @property
    def init_overrides(self) -> list[FileOverrideInfo]:
        return self._overrides

    @property
    def overlap_map(self) -> dict[str, bool]:
        return self._overlap_map

    def restart_init(self) -> None:
        """路径配置变更后重新启动初始化链"""
        if not self.paths_valid():
            return
        self._init_state = InitState.SCANNING
        self._init_error = None
        self._do_scan()
        self._init_state = InitState.LOADING_JSON
        threading.Thread(target=self._async_init_chain, daemon=True).start()

    def continue_after_fix(self, fixed_paths: list[Path],
                           ignored: list[ParseFailure]) -> None:
        """JSON 修复完成后继续初始化链。"""
        dm = DataManager.instance()
        remaining_failures: list[ParseFailure] = []

        if fixed_paths:
            remaining_failures = dm.reload(fixed_paths)

        mod_ignored = [f for f in ignored if not f.is_base]
        if mod_ignored:
            dm.set_ignored_failures(mod_ignored)

        ignored_paths = {str(f.file_path) for f in ignored}
        still_broken = [f for f in remaining_failures
                        if str(f.file_path) not in ignored_paths]
        if still_broken:
            self._parse_failures = still_broken
            return

        self._parse_failures = []
        threading.Thread(target=self._continue_after_load, daemon=True).start()

    def _async_init_chain(self) -> None:
        try:
            self._ensure_schemas()
            self._do_load_json()
            failures = DataManager.instance().take_failures()
            if failures:
                self._parse_failures = failures
                self._init_state = InitState.AWAITING_FIX
                return
            self._continue_after_load()
        except Exception as e:
            self._init_error = f"{type(e).__name__}: {e}"
            self._init_state = InitState.ERROR

    def _ensure_schemas(self) -> None:
        schema_dir = SCHEMA_DIR
        config_dir = self._config.game_config_path
        if not config_dir.exists():
            return
        if schema_dir.exists() and any(schema_dir.glob("*.schema.json")):
            return
        self._init_state = InitState.GENERATING_SCHEMAS
        from .schema.generator import generate_all
        generate_all(
            str(config_dir), str(schema_dir),
            lambda cur, total, name: setattr(
                self, '_init_schema_progress', (cur, total, name),
            ),
        )
        self._init_state = InitState.LOADING_JSON

    def _continue_after_load(self) -> None:
        try:
            all_mod_ids = [m.mod_id for m in self._mods]
            self._overlap_map = _compute_all_overlaps(
                DataManager.instance(), all_mod_ids,
            )

            self._init_state = InitState.COMPUTING_DELTA
            self._init_progress = (0, 0)
            enabled_ids = [mid for mid in self._config.mod_order
                           if mid in set(self._config.enabled_mods)]
            if enabled_ids:
                try:
                    merge_mode = MergeMode(self._config.merge_mode)
                except ValueError:
                    merge_mode = MergeMode.SMART
                mod_merge_modes: dict[str, MergeMode] = {}
                for k, v in self._config.mod_merge_modes.items():
                    try:
                        mod_merge_modes[k] = MergeMode(v)
                    except ValueError:
                        continue
                self._mod_manager.init_delta(
                    enabled_ids,
                    merge_mode=merge_mode,
                    mod_merge_modes=mod_merge_modes,
                    progress_cb=lambda c, t: setattr(self, '_init_progress', (c, t)),
                )

            self._init_state = InitState.ANALYZING
            mod_configs = self._get_enabled_mod_configs()
            if mod_configs:
                self._overrides = _analyze_all_overrides(
                    mod_configs, schema_dir=SCHEMA_DIR,
                )

            self._init_state = InitState.READY
        except Exception as e:
            self._init_error = f"{type(e).__name__}: {e}"
            self._init_state = InitState.ERROR

    def _do_scan(self) -> None:
        diag.snapshot("scan", "parse")
        mods = _scan_all_mods(
            self._config.workshop_dir,
            exclude_ids={SYNTHETIC_MOD_ID},
        )
        local_dir = self._config.local_mod_dir
        if local_dir.exists():
            local_mods = _scan_all_mods(local_dir)
            existing_ids = {m.mod_id for m in mods}
            for lm in local_mods:
                if lm.mod_id not in existing_ids:
                    mods.append(lm)
        self._mods = mods
        self._mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]

    def _do_load_json(self) -> None:
        mod_update_times = {
            m.mod_id: m.update_time
            for m in self._mods if m.update_time is not None
        }
        DataManager.instance().init(
            self._config, self._mod_configs,
            mod_update_times=mod_update_times or None,
            on_progress=lambda c, t: setattr(self, '_init_progress', (c, t)),
        )

    def _get_enabled_mod_configs(self) -> list[tuple[str, str, Path]]:
        enabled_set = set(self._config.enabled_mods)
        return [(mid, name, path) for mid, name, path in self._mod_configs
                if mid in enabled_set]

    # ══════════════════════════════════════════════════════════════
    # 业务常量
    # ══════════════════════════════════════════════════════════════

    @property
    def schema_dir(self) -> Path:
        return SCHEMA_DIR

    @property
    def history_dir(self) -> Path:
        return HISTORY_DIR

    @property
    def overrides_dir(self) -> Path:
        return MOD_OVERRIDES_DIR

    @property
    def merged_output_path(self) -> Path:
        return MERGED_OUTPUT_PATH

    @property
    def synthetic_mod_id(self) -> str:
        return SYNTHETIC_MOD_ID

    @property
    def app_version(self) -> str:
        return APP_VERSION

    # ══════════════════════════════════════════════════════════════
    # 配置读写
    # ══════════════════════════════════════════════════════════════

    @property
    def game_path(self) -> str:
        return self._config.game_path

    @property
    def workshop_dir(self) -> Path:
        return self._config.workshop_dir

    @property
    def local_mod_dir(self) -> Path:
        return self._config.local_mod_dir

    @property
    def local_mod_path(self) -> str:
        return self._config.local_mod_path

    @property
    def game_config_path(self) -> Path:
        return self._config.game_config_path

    @property
    def workshop_path(self) -> str:
        return self._config.workshop_path

    @property
    def mod_order(self) -> list[str]:
        return self._config.mod_order

    @property
    def enabled_mods(self) -> list[str]:
        return self._config.enabled_mods

    @property
    def merge_mode(self) -> str:
        return self._config.merge_mode

    @property
    def mod_merge_modes(self) -> dict[str, str]:
        return self._config.mod_merge_modes

    def set_game_path(self, path: str) -> None:
        self._config.update(game_path=path)

    def set_workshop_path(self, path: str) -> None:
        self._config.update(workshop_path=path)

    def set_local_mod_path(self, path: str) -> None:
        self._config.update(local_mod_path=path)

    def update_mod_order(self, order: list[str], enabled: list[str]) -> None:
        self._config.update(mod_order=order, enabled_mods=enabled)

    def update_merge_mode(self, mode: str) -> None:
        self._config.update(merge_mode=mode)

    def update_mod_merge_mode(self, mod_id: str, mode: str | None) -> None:
        new_modes = dict(self._config.mod_merge_modes)
        if mode is not None:
            new_modes[mod_id] = mode
        else:
            new_modes.pop(mod_id, None)
        self._config.update(mod_merge_modes=new_modes)

    def paths_valid(self) -> bool:
        return (
            bool(self._config.game_path)
            and Path(self._config.game_path).exists()
            and bool(self._config.workshop_path)
            and Path(self._config.workshop_path).exists()
        )

    def detect_game_path(self) -> str:
        return detect_game_path()

    def detect_workshop_path(self) -> str:
        return detect_workshop_path()

    def infer_workshop_path_from_game(self, game_path: str) -> str:
        return infer_workshop_path_from_game(game_path)

    # ══════════════════════════════════════════════════════════════
    # JSON 验证/解析
    # ══════════════════════════════════════════════════════════════

    def validate_json(self, text: str) -> tuple[bool, int, str]:
        """验证 JSON 文本。返回 (valid, error_line, error_msg)。"""
        try:
            JsonDoc.parse(text, False)
            return (True, 0, "")
        except ParseError as e:
            return (False, e.line, str(e))

    def parse_json(self, text: str) -> JsonDoc:
        """解析 JSON 文本，失败抛出 ValueError。"""
        try:
            return JsonDoc.parse(text, False)
        except ParseError as e:
            raise ValueError(str(e)) from e

    # ══════════════════════════════════════════════════════════════
    # Schema
    # ══════════════════════════════════════════════════════════════

    def generate_schemas(
        self,
        config_dir: Path,
        schema_dir: Path,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> None:
        from .schema.generator import generate_all
        generate_all(str(config_dir), str(schema_dir), progress_cb)

    # ══════════════════════════════════════════════════════════════
    # Platform
    # ══════════════════════════════════════════════════════════════

    def check_for_update(self, timeout: int = 8) -> dict[str, str] | None:
        from .platform.updater import check_for_update
        return check_for_update(timeout=timeout)

    # ══════════════════════════════════════════════════════════════
    # 诊断
    # ══════════════════════════════════════════════════════════════

    def snapshot_diagnostics(self, *categories: str) -> list[tuple[str, str]]:
        return diag.snapshot(*categories)

    # ══════════════════════════════════════════════════════════════
    # Mod 扫描
    # ══════════════════════════════════════════════════════════════

    def scan_mods(self, workshop_dir: Path,
                  exclude_ids: set[str] | None = None) -> list[ModInfo]:
        return _scan_all_mods(workshop_dir, exclude_ids=exclude_ids)

    def get_game_update_time(self, workshop_dir: Path) -> int | None:
        steamapps = _get_steamapps_from_workshop(workshop_dir)
        return _get_game_update_time(steamapps)

    def get_major_update_ts(self) -> int:
        return MAJOR_UPDATE_TS

    # ══════════════════════════════════════════════════════════════
    # Store 初始化
    # ══════════════════════════════════════════════════════════════

    def take_failures(self) -> list[ParseFailure]:
        return DataManager.instance().take_failures()

    def reload_files(self, paths: list[Path]) -> list[ParseFailure]:
        return DataManager.instance().reload(paths)

    def set_ignored_failures(self, failures: list[ParseFailure]) -> None:
        DataManager.instance().set_ignored_failures(failures)

    # ══════════════════════════════════════════════════════════════
    # Override
    # ══════════════════════════════════════════════════════════════

    def load_overrides(self, overrides_dir: Path, enabled_ids: list[str]) -> None:
        DataManager.instance().load_overrides(overrides_dir, enabled_ids)

    def has_override(self, mod_id: str, rel_path: str) -> bool:
        return DataManager.instance().has_override(mod_id, rel_path)

    def set_override_node(self, mod_id: str, rel_path: str,
                          node: DeltaDict) -> None:
        DataManager.instance().set_override_node(mod_id, rel_path, node)

    def remove_override(self, mod_id: str, rel_path: str) -> bool:
        return DataManager.instance().remove_override(mod_id, rel_path)

    # ══════════════════════════════════════════════════════════════
    # 数据查询
    # ══════════════════════════════════════════════════════════════

    def get_base(self, rel_path: str) -> JsonDoc:
        return DataManager.instance().get_base(rel_path)

    def has_base(self, rel_path: str) -> bool:
        return DataManager.instance().has_base(rel_path)

    def get_mod(self, mod_id: str, rel_path: str) -> JsonDoc:
        return DataManager.instance().get_mod(mod_id, rel_path)

    def has_mod(self, mod_id: str, rel_path: str) -> bool:
        return DataManager.instance().has_mod(mod_id, rel_path)

    def reload_mod(self, mod_id: str) -> None:
        DataManager.instance().reload_mod(mod_id)

    # ══════════════════════════════════════════════════════════════
    # Delta
    # ══════════════════════════════════════════════════════════════

    def init_delta(self, mod_ids: list[str],
                   merge_mode: MergeMode = MergeMode.SMART,
                   mod_merge_modes: dict[str, MergeMode] | None = None,
                   progress_cb: Callable[[int, int], None] | None = None,
                   cancel_check: CancelCheck | None = None) -> None:
        self._mod_manager.init_delta(
            mod_ids, merge_mode=merge_mode,
            mod_merge_modes=mod_merge_modes, progress_cb=progress_cb,
            cancel_check=cancel_check,
        )

    @async_task
    def refresh_delta_async(
        self,
        mod_ids: list[str],
        merge_mode: MergeMode,
        mod_merge_modes: dict[str, MergeMode] | None,
        cb: TaskCallback,
        *,
        handle: AsyncTaskHandle,
    ) -> None:
        try:
            def _progress(cur: int, total: int) -> None:
                handle.check_cancel()
                cb(TaskProgress(cur, total))
            self.init_delta(
                mod_ids, merge_mode, mod_merge_modes,
                progress_cb=_progress,
                cancel_check=handle.check_cancel,
            )
            cb(TaskDone())
        except _Cancelled:
            pass
        except Exception as e:
            if not handle.is_cancelled:
                cb(TaskError(str(e)))

    def invalidate_delta(self) -> None:
        self._mod_manager.invalidate_delta()

    def get_delta(self, mod_id: str, rel_path: str) -> DeltaDict | None:
        return self._mod_manager.get_delta(mod_id, rel_path)

    def has_delta(self, mod_id: str, rel_path: str) -> bool:
        return ModDelta.has(mod_id, rel_path)

    def compute_delta_node(
        self, base_doc: JsonDoc, mod_doc: JsonDoc,
        file_type: str,
        merge_mode: MergeMode = MergeMode.SMART,
    ) -> DeltaDict | None:
        is_dict = (file_type == "dictionary")
        return compute_delta(
            base_doc, mod_doc, _to_cpp_mode(merge_mode), is_dict,
        )

    # ══════════════════════════════════════════════════════════════
    # 合并缓存
    # ══════════════════════════════════════════════════════════════

    def invalidate_merge_cache(self, rel_path: str | None = None) -> None:
        self._mod_manager.invalidate_merge_cache(rel_path)

    def get_merge_state(self, rel_path: str,
                        mod_configs: list[tuple[str, str, Path]],
                        need_steps: bool = True) -> FileMergeState:
        return self._mod_manager.get_merge_state(
            rel_path, mod_configs, need_steps=need_steps,
        )

    # ══════════════════════════════════════════════════════════════
    # 合并执行
    # ══════════════════════════════════════════════════════════════

    def merge_all_files(
        self, mod_configs: list[tuple[str, str, Path]], output_path: Path,
        cancel_check: CancelCheck | None = None,
        progress_cb: ProgressCallback | None = None,
    ) -> dict[str, MergeResult]:
        return self._mod_manager.merge_all_files(
            mod_configs, output_path,
            cancel_check=cancel_check, progress_cb=progress_cb,
        )

    def copy_failed_files(self, mod_configs: list[tuple[str, str, Path]],
                          output_path: Path) -> list[str]:
        return _copy_failed_files(mod_configs, output_path)

    def copy_resources(
        self, mod_paths: list[tuple[str, Path]], output_path: Path,
        cancel_check: CancelCheck | None = None,
        remap_tables: dict[str, RemapTable] | None = None,
    ) -> None:
        _copy_resources(mod_paths, output_path,
                        cancel_check=cancel_check, remap_tables=remap_tables)

    def reset_dir_cache(self) -> None:
        _reset_dir_cache()

    # ══════════════════════════════════════════════════════════════
    # ID Remap
    # ══════════════════════════════════════════════════════════════

    def remap_mod_configs(
        self, mod_configs: list[tuple[str, str, Path]],
    ) -> tuple[list[str], dict[str, RemapTable]]:
        return _remap_mod_configs(mod_configs)

    def cleanup_remap(self, remap_tables: dict[str, RemapTable]) -> None:
        self._mod_manager.cleanup_remap(remap_tables)

    # ══════════════════════════════════════════════════════════════
    # 冲突分析
    # ══════════════════════════════════════════════════════════════

    def analyze_all_overrides(
        self, mod_configs: list[tuple[str, str, Path]],
        schema_dir: Path | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> list[FileOverrideInfo]:
        return _analyze_all_overrides(mod_configs, schema_dir=schema_dir,
                                      cancel_check=cancel_check)

    def compute_all_overlaps(self, mod_ids: list[str]) -> dict[str, bool]:
        return _compute_all_overlaps(DataManager.instance(), mod_ids)

    # ══════════════════════════════════════════════════════════════
    # 部署
    # ══════════════════════════════════════════════════════════════

    def generate_info_json(self, mod_names: list[str], output_path: Path) -> None:
        _generate_info_json(mod_names, output_path)

    def scan_synthetic_mods(self,
                            local_mod_dir: Path) -> list[tuple[str, str, Path]]:
        return _scan_synthetic_mods(local_mod_dir)

    # ══════════════════════════════════════════════════════════════
    # Schema / Classify / Merge
    # ══════════════════════════════════════════════════════════════

    def classify_json(self, doc: JsonDoc) -> str:
        return _classify_json(doc)

    def merge_file(
        self, base_doc: JsonDoc,
        mod_data_list: list[tuple[str, str, DeltaDict, str]],
        rel_path: str = "",
    ) -> MergeResult:
        return _merge_file(base_doc, mod_data_list, rel_path)

    def load_schemas(self, schema_dir: Path) -> dict[str, JsonObject]:
        return _load_schemas(schema_dir)

    def resolve_schema(self, rel_path: str,
                       schemas: dict[str, JsonObject]) -> JsonObject | None:
        return _resolve_schema(rel_path, schemas)

    def get_schema_root_key(self, schema: JsonObject | None) -> str | None:
        if schema is None:
            return None
        return _get_schema_root_key(schema)
