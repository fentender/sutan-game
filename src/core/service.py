"""
MergeService — GUI 与核心模块之间的业务编排层

GUI 通过此类访问所有核心业务功能，不直接操作 DataManager / MergeCache / ModDelta 等单例。
"""
from collections.abc import Callable
from pathlib import Path

from ..config import UserConfig
from .data_manager import DataManager
from .infra.types import (
    CancelCheck,
    DictFieldDiff,
    JsonObject,
    MergeMode,
    ParseFailure,
    ProgressCallback,
)
from .json.classify import classify_json as _classify_json
from .json.parser import reset_dir_cache as _reset_dir_cache
from .merge.cache import FileMergeState, MergeCache
from .merge.delta import ModDelta, compute_delta as _compute_delta
from .merge.merger import (
    MergeResult,
    copy_failed_files as _copy_failed_files,
    merge_all_files as _merge_all_files,
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


class MergeService:
    """业务编排层 — GUI 通过此类访问所有核心业务功能"""

    def __init__(self, config: UserConfig) -> None:
        self._config = config

    @property
    def _data(self) -> DataManager:
        return DataManager.instance()

    @property
    def _merge_cache(self) -> MergeCache:
        return MergeCache.instance()

    # === 扫描 ===

    def scan_mods(self, workshop_dir: Path,
                  exclude_ids: set[str] | None = None) -> list[ModInfo]:
        return _scan_all_mods(workshop_dir, exclude_ids=exclude_ids)

    def get_game_update_time(self, workshop_dir: Path) -> int | None:
        steamapps = _get_steamapps_from_workshop(workshop_dir)
        return _get_game_update_time(steamapps)

    def get_major_update_ts(self) -> int:
        return MAJOR_UPDATE_TS

    # === Store 初始化 ===

    def init_store(
        self, game_config_path: Path,
        mod_configs: list[tuple[str, str, Path]],
        history_dir: Path | None = None,
        mod_update_times: dict[str, int] | None = None,
    ) -> None:
        self._data.init(game_config_path, mod_configs,
                        history_dir=history_dir,
                        mod_update_times=mod_update_times)

    def take_failures(self) -> list[ParseFailure]:
        return self._data.take_failures()

    def reload_files(self, paths: list[Path]) -> list[ParseFailure]:
        return self._data.reload(paths)

    def set_ignored_failures(self, failures: list[ParseFailure]) -> None:
        self._data.set_ignored_failures(failures)

    # === Override 管理 ===

    def load_overrides(self, overrides_dir: Path, enabled_ids: list[str]) -> None:
        self._data.load_overrides(overrides_dir, enabled_ids)

    def invalidate_overrides(self, mod_ids: set[str]) -> list[str]:
        return self._data.invalidate_overrides(mod_ids)

    def has_override(self, mod_id: str, rel_path: str) -> bool:
        return self._data.has_override(mod_id, rel_path)

    def set_override(self, mod_id: str, rel_path: str,
                     delta: DictFieldDiff) -> None:
        self._data.set_override(mod_id, rel_path, delta)

    def remove_override(self, mod_id: str, rel_path: str) -> bool:
        return self._data.remove_override(mod_id, rel_path)

    # === 数据查询 ===

    def get_base(self, rel_path: str) -> JsonObject:
        return self._data.get_base(rel_path)

    def has_base(self, rel_path: str) -> bool:
        return self._data.has_base(rel_path)

    def get_mod(self, mod_id: str, rel_path: str) -> JsonObject:
        return self._data.get_mod(mod_id, rel_path)

    def has_mod(self, mod_id: str, rel_path: str) -> bool:
        return self._data.has_mod(mod_id, rel_path)

    def reload_mod(self, mod_id: str) -> None:
        self._data.reload_mod(mod_id)

    # === Delta 管理 ===

    def init_delta(self, mod_ids: list[str],
                   schema_dir: Path | None = None,
                   merge_mode: MergeMode = MergeMode.SMART,
                   mod_merge_modes: dict[str, MergeMode] | None = None,
                   progress_cb: Callable[[int, int], None] | None = None) -> None:
        ModDelta.init(
            mod_ids,
            schema_dir=schema_dir,
            merge_mode=merge_mode,
            mod_merge_modes=mod_merge_modes,
            progress_cb=progress_cb,
        )

    def invalidate_delta(self) -> None:
        ModDelta.invalidate()

    def get_delta(self, mod_id: str, rel_path: str) -> DictFieldDiff | None:
        return ModDelta.get(mod_id, rel_path)

    def compute_delta(self, base: JsonObject, mod: JsonObject, file_type: str,
                      root_key: str | None = None,
                      merge_mode: MergeMode = MergeMode.SMART) -> DictFieldDiff | None:
        return _compute_delta(base, mod, file_type,
                              root_key=root_key, merge_mode=merge_mode)

    # === 合并缓存 ===

    def invalidate_merge_cache(self, rel_path: str | None = None) -> None:
        if rel_path is None:
            self._merge_cache.invalidate_all()
        else:
            self._merge_cache.invalidate(rel_path)

    def get_merge_state(self, rel_path: str,
                        mod_configs: list[tuple[str, str, Path]],
                        schema_dir: Path | None = None,
                        need_steps: bool = True) -> FileMergeState:
        return self._merge_cache.get(rel_path, mod_configs,
                                     schema_dir=schema_dir, need_steps=need_steps)

    # === ID 重分配 ===

    def remap_mod_configs(
        self, mod_configs: list[tuple[str, str, Path]],
    ) -> tuple[list[str], dict[str, RemapTable]]:
        return _remap_mod_configs(mod_configs)

    def cleanup_remap(self, remap_tables: dict[str, RemapTable]) -> None:
        for mod_id in remap_tables:
            self._data.reload_mod(mod_id)

    # === 冲突分析 ===

    def analyze_all_overrides(
        self, mod_configs: list[tuple[str, str, Path]],
        schema_dir: Path | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> list[FileOverrideInfo]:
        return _analyze_all_overrides(mod_configs, schema_dir=schema_dir,
                                      cancel_check=cancel_check)

    def compute_all_overlaps(self, mod_ids: list[str]) -> dict[str, bool]:
        return _compute_all_overlaps(self._data, mod_ids)

    # === 合并执行 ===

    def merge_all_files(
        self, mod_configs: list[tuple[str, str, Path]], output_path: Path,
        schema_dir: Path | None = None,
        cancel_check: CancelCheck | None = None,
        progress_cb: ProgressCallback | None = None,
    ) -> dict[str, MergeResult]:
        return _merge_all_files(mod_configs, output_path,
                                schema_dir=schema_dir,
                                cancel_check=cancel_check,
                                progress_cb=progress_cb)

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

    # === 部署 ===

    def generate_info_json(self, mod_names: list[str], output_path: Path) -> None:
        _generate_info_json(mod_names, output_path)

    def scan_synthetic_mods(self,
                            local_mod_dir: Path) -> list[tuple[str, str, Path]]:
        return _scan_synthetic_mods(local_mod_dir)

    # === Deletion 预览支持 ===

    def classify_json(self, data: JsonObject) -> str:
        return _classify_json(data)

    def merge_file(
        self, base_data: JsonObject,
        mod_data_list: list[tuple[str, str, DictFieldDiff, str]],
        rel_path: str = "",
        schema: JsonObject | None = None,
    ) -> MergeResult:
        return _merge_file(base_data, mod_data_list, rel_path, schema=schema)

    def load_schemas(self, schema_dir: Path) -> dict[str, JsonObject]:
        return _load_schemas(schema_dir)

    def resolve_schema(self, rel_path: str,
                       schemas: dict[str, JsonObject]) -> JsonObject | None:
        return _resolve_schema(rel_path, schemas)

    def get_schema_root_key(self, schema: JsonObject | None) -> str | None:
        if schema is None:
            return None
        return _get_schema_root_key(schema)
