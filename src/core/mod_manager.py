"""
ModManager — 服务层最顶层，管理 mod 列表状态 + 合并缓存 + 流程编排

GUI 通过此类访问所有核心业务功能。
"""
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sultan_core.json import JsonDoc
from sultan_core.state import JsonState
from sultan_core.delta import (
    compute_and_serialize,
    deserialize_and_apply,
)

from ..config import UserConfig
from .data_manager import DataManager
from .infra.profiler import profile
from .infra.types import (
    CancelCheck,
    ChangeKind,
    DictFieldDiff,
    JsonObject,
    MergeMode,
    ParseFailure,
    ProgressCallback,
)
from .json import classify_json as _classify_json
from .json.parser import reset_dir_cache as _reset_dir_cache
from .merge.cache import FileMergeState, StepState
from .merge.delta import ModDelta, _to_cpp_mode, _is_valid_delta
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


# ==================== 缓存数据结构 ====================


@dataclass
class _DeltaVersionInfo:
    """delta 缓存的 version 快照（delta_doc 由 ModDelta 持有）"""
    base_version: int
    mod_version: int


@dataclass
class _MergeEntry:
    """merge 缓存条目，含计算时的 version 快照"""
    state: FileMergeState
    has_steps: bool
    version_key: tuple[tuple[str, int, int], ...]


# ==================== ModManager ====================


class ModManager:
    """Mod 合并管理器 — GUI 唯一入口，管理 mod 列表状态和合并缓存。"""

    def __init__(self, config: UserConfig) -> None:
        self._config = config
        self._lock = threading.RLock()

        # === 状态 ===
        self._enabled_mods: list[str] = []
        self._merge_mode: MergeMode = MergeMode.SMART
        self._mod_merge_modes: dict[str, MergeMode] = {}

        # === 缓存 ===
        # delta version 快照：用于惰性失效检测（delta_doc 仍由 ModDelta 持有）
        self._delta_versions: dict[tuple[str, str], _DeltaVersionInfo] = {}
        # merge 缓存：完全由 ModManager 管理
        self._merge_cache: dict[str, _MergeEntry] = {}

    @property
    def _data(self) -> DataManager:
        return DataManager.instance()

    # === Mod 列表管理 ===

    @property
    def enabled_mods(self) -> list[str]:
        return list(self._enabled_mods)

    def set_enabled_mods(self, mod_ids: list[str]) -> None:
        with self._lock:
            self._enabled_mods = list(mod_ids)
            self._merge_cache.clear()

    @property
    def merge_mode(self) -> MergeMode:
        return self._merge_mode

    def set_merge_mode(self, mode: MergeMode) -> None:
        with self._lock:
            self._merge_mode = mode
            self._delta_versions.clear()
            self._merge_cache.clear()

    def set_mod_merge_mode(self, mod_id: str, mode: MergeMode | None) -> None:
        with self._lock:
            if mode is None:
                self._mod_merge_modes.pop(mod_id, None)
            else:
                self._mod_merge_modes[mod_id] = mode
            self._delta_versions.clear()
            self._merge_cache.clear()

    @property
    def mod_merge_modes(self) -> dict[str, MergeMode]:
        return dict(self._mod_merge_modes)

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
        overrides_dir: Path | None = None,
        enabled_mod_ids: list[str] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        self._data.init(game_config_path, mod_configs,
                        history_dir=history_dir,
                        mod_update_times=mod_update_times,
                        overrides_dir=overrides_dir,
                        enabled_mod_ids=enabled_mod_ids,
                        on_progress=on_progress)

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

    def get_base(self, rel_path: str) -> JsonDoc:
        return self._data.get_base(rel_path)

    def has_base(self, rel_path: str) -> bool:
        return self._data.has_base(rel_path)

    def get_mod(self, mod_id: str, rel_path: str) -> JsonDoc:
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
        """预计算所有 mod 的 delta（委托 ModDelta），并记录 version 快照。"""
        ModDelta.init(
            mod_ids,
            schema_dir=schema_dir,
            merge_mode=merge_mode,
            mod_merge_modes=mod_merge_modes,
            progress_cb=progress_cb,
        )
        dm = self._data
        with self._lock:
            self._delta_versions.clear()
            for key in ModDelta._cache:
                mod_id, rel_path = key
                base_ver = dm.config_version("", rel_path)
                mod_ver = dm.config_version(mod_id, rel_path)
                self._delta_versions[key] = _DeltaVersionInfo(
                    base_version=base_ver,
                    mod_version=mod_ver,
                )

    def invalidate_delta(self) -> None:
        ModDelta.invalidate()
        with self._lock:
            self._delta_versions.clear()
            self._merge_cache.clear()

    def get_delta(self, mod_id: str, rel_path: str) -> JsonDoc | None:
        """获取 delta，带 version 惰性失效检测。"""
        key = (mod_id, rel_path)
        if not ModDelta.has(mod_id, rel_path):
            return None

        with self._lock:
            ver_info = self._delta_versions.get(key)
        if ver_info is not None:
            dm = self._data
            base_ver = dm.config_version("", rel_path)
            mod_ver = dm.config_version(mod_id, rel_path)
            if ver_info.base_version != base_ver or ver_info.mod_version != mod_ver:
                return None

        return ModDelta.get(mod_id, rel_path)

    def has_delta(self, mod_id: str, rel_path: str) -> bool:
        return ModDelta.has(mod_id, rel_path)

    def compute_delta_doc(
        self, base_doc: JsonDoc, mod_doc: JsonDoc,
        file_type: str,
        merge_mode: MergeMode = MergeMode.SMART,
    ) -> JsonDoc | None:
        is_dict = (file_type == "dictionary")
        result = compute_and_serialize(
            base_doc, mod_doc, _to_cpp_mode(merge_mode), is_dict,
        )
        return result if _is_valid_delta(result) else None

    # === 合并缓存 ===

    def _build_merge_version_key(
        self, rel_path: str, mod_ids: list[str],
    ) -> tuple[tuple[str, int, int], ...]:
        """构造 merge 缓存的 version_key。"""
        dm = self._data
        parts: list[tuple[str, int, int]] = []
        for mod_id in mod_ids:
            if not dm.has_mod(mod_id, rel_path):
                continue
            config_ver = dm.config_version(mod_id, rel_path)
            override_ver = dm.override_version(mod_id, rel_path)
            parts.append((mod_id, config_ver, override_ver))
        return tuple(parts)

    def invalidate_merge_cache(self, rel_path: str | None = None) -> None:
        with self._lock:
            if rel_path is None:
                self._merge_cache.clear()
            else:
                self._merge_cache.pop(rel_path, None)

    def get_merge_state(self, rel_path: str,
                        mod_configs: list[tuple[str, str, Path]],
                        schema_dir: Path | None = None,
                        need_steps: bool = True) -> FileMergeState:
        """获取文件的合并状态，version 惰性失效。"""
        mod_ids = [mid for mid, _, _ in mod_configs]
        current_key = self._build_merge_version_key(rel_path, mod_ids)

        with self._lock:
            cached = self._merge_cache.get(rel_path)
            if cached is not None:
                if cached.version_key == current_key:
                    if not need_steps or cached.has_steps:
                        return cached.state

        state = self._compute_merge_file(rel_path, mod_configs, need_steps)
        entry = _MergeEntry(
            state=state,
            has_steps=need_steps,
            version_key=current_key,
        )
        with self._lock:
            self._merge_cache[rel_path] = entry
        return state

    @profile
    def _compute_merge_file(
        self,
        rel_path: str,
        mod_configs: list[tuple[str, str, Path]],
        need_steps: bool = True,
    ) -> FileMergeState:
        """统一的合并循环：C++ State/Delta/Format 一次遍历。"""
        from .infra.diagnostics import diag
        diag.snapshot("merge")
        dm = self._data
        base_doc = dm.get_base(rel_path)
        state = JsonState.from_doc(base_doc)

        mod_data_list: list[tuple[str, str, JsonDoc]] = []
        for mod_id, mod_name, _ in mod_configs:
            if not dm.has_mod(mod_id, rel_path):
                continue
            delta_doc = self.get_delta(mod_id, rel_path)
            if delta_doc is None:
                continue
            mod_data_list.append((mod_id, mod_name, delta_doc))

        steps: list[StepState] = []
        for version, (mod_id, mod_name, delta_doc) in enumerate(mod_data_list, 1):
            deserialize_and_apply(delta_doc, state, version=version)

            override_doc = dm.get_override_doc(mod_id, rel_path)
            if override_doc is not None:
                deserialize_and_apply(override_doc, state,
                                      version=version, is_override=True)

            if need_steps:
                fmt = state.format(version)
                steps.append(StepState(
                    mod_id=mod_id,
                    mod_name=mod_name,
                    left_lines=list(fmt.left_lines),
                    right_lines=list(fmt.right_lines),
                    left_kinds=[ChangeKind(k) if k >= 0 else None
                                for k in fmt.left_kinds],
                    right_kinds=[ChangeKind(k) if k >= 0 else None
                                 for k in fmt.right_kinds],
                ))

        final_doc = state.to_doc()
        warnings = [msg for _, msg in diag.snapshot("merge")]

        return FileMergeState(
            final_doc=final_doc,
            steps=steps,
            warnings=warnings,
        )

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
        """合并所有文件并写出（使用内部 merge 缓存）。"""
        from .infra.diagnostics import diag
        diag.snapshot("merge")
        dm = self._data
        mod_ids = [mod_id for mod_id, _, _ in mod_configs]

        results: dict[str, MergeResult] = {}
        all_paths = sorted(dm.all_rel_paths())
        total = len(all_paths)

        for i, rel_path in enumerate(all_paths):
            if cancel_check:
                cancel_check()
            if progress_cb:
                progress_cb(i, total)

            has_mod = any(dm.has_mod(mid, rel_path) for mid in mod_ids)
            if not has_mod:
                continue

            merge_state = self.get_merge_state(rel_path, mod_configs,
                                               schema_dir, need_steps=False)
            result = MergeResult(merged_doc=merge_state.final_doc)
            results[rel_path] = result

            out_file = output_path / rel_path
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(merge_state.final_doc.to_string(), encoding='utf-8')

        if progress_cb:
            progress_cb(total, total)
        return results

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

    def classify_json(self, doc: JsonDoc) -> str:
        return _classify_json(doc)

    def merge_file(
        self, base_doc: JsonDoc,
        mod_data_list: list[tuple[str, str, JsonDoc, str]],
        rel_path: str = "",
        schema: JsonObject | None = None,
    ) -> MergeResult:
        return _merge_file(base_doc, mod_data_list, rel_path, schema=schema)

    def load_schemas(self, schema_dir: Path) -> dict[str, JsonObject]:
        return _load_schemas(schema_dir)

    def resolve_schema(self, rel_path: str,
                       schemas: dict[str, JsonObject]) -> JsonObject | None:
        return _resolve_schema(rel_path, schemas)

    def get_schema_root_key(self, schema: JsonObject | None) -> str | None:
        if schema is None:
            return None
        return _get_schema_root_key(schema)
