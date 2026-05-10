"""
ModManager — 服务层最顶层，管理 mod 列表状态 + 合并缓存 + 流程编排

GUI 通过此类访问合并相关业务功能。
"""
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sultan_core.delta import (
    DeltaDict,
    apply_delta,
)
from sultan_core.state import JsonState

from ..config import ConfigChangeEvent, UserConfig
from .data_manager import DataManager
from .infra.profiler import profile
from .infra.types import (
    CancelCheck,
    ChangeKind,
    MergeMode,
    ProgressCallback,
)
from .merge.cache import FileMergeState, StepState
from .merge.delta import ModDelta
from .merge.merger import MergeResult
from .mod.id_remap import RemapTable
from .platform.steam import (
    get_game_update_time as _get_game_update_time,
    get_steamapps_from_workshop as _get_steamapps_from_workshop,
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
    """Mod 合并管理器 — 管理 mod 列表状态和合并缓存。"""

    def __init__(self, config: UserConfig) -> None:
        self._config = config
        self._lock = threading.RLock()

        # === 缓存 ===
        self._delta_versions: dict[tuple[str, str], _DeltaVersionInfo] = {}
        self._merge_cache: dict[str, _MergeEntry] = {}

        config.register_listener(self._on_config_changed)

    def _on_config_changed(self, event: ConfigChangeEvent) -> None:
        if event.changed_fields & {'merge_mode', 'mod_merge_modes'}:
            self.invalidate_delta()
        elif event.changed_fields & {'mod_order', 'enabled_mods'}:
            self.invalidate_merge_cache()

    @property
    def _data(self) -> DataManager:
        return DataManager.instance()

    # === 扫描 ===

    def get_game_update_time(self, workshop_dir: Path) -> int | None:
        steamapps = _get_steamapps_from_workshop(workshop_dir)
        return _get_game_update_time(steamapps)

    # === Delta 管理 ===

    def init_delta(self, mod_ids: list[str],
                   merge_mode: MergeMode = MergeMode.SMART,
                   mod_merge_modes: dict[str, MergeMode] | None = None,
                   progress_cb: Callable[[int, int], None] | None = None,
                   cancel_check: CancelCheck | None = None) -> None:
        """预计算所有 mod 的 delta（委托 ModDelta），并记录 version 快照。"""
        ModDelta.init(
            mod_ids,
            merge_mode=merge_mode,
            mod_merge_modes=mod_merge_modes,
            progress_cb=progress_cb,
            cancel_check=cancel_check,
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

    def clear(self) -> None:
        ModDelta.clear()
        with self._lock:
            self._delta_versions.clear()
            self._merge_cache.clear()

    def __del__(self) -> None:
        self.clear()

    def get_delta(self, mod_id: str, rel_path: str) -> DeltaDict | None:
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

    # === 合并缓存 ===

    def _build_merge_version_key(
        self, rel_path: str, mod_ids: list[str],
    ) -> tuple[tuple[str, int, int], ...]:
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

        mod_data_list: list[tuple[str, str, DeltaDict]] = []
        for mod_id, mod_name, _ in mod_configs:
            if not dm.has_mod(mod_id, rel_path):
                continue
            delta = self.get_delta(mod_id, rel_path)
            if delta is None:
                continue
            mod_data_list.append((mod_id, mod_name, delta))

        steps: list[StepState] = []
        for version, (mod_id, mod_name, delta) in enumerate(mod_data_list, 1):
            apply_delta(delta, state, version=version)

            override_node = dm.get_override_node(mod_id, rel_path)
            if override_node is not None:
                apply_delta(override_node, state,
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

    def cleanup_remap(self, remap_tables: dict[str, RemapTable]) -> None:
        for mod_id in remap_tables:
            self._data.reload_mod(mod_id)

    # === 合并执行 ===

    def merge_all_files(
        self, mod_configs: list[tuple[str, str, Path]], output_path: Path,
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
                                               need_steps=False)
            result = MergeResult(merged_doc=merge_state.final_doc)
            results[rel_path] = result

            out_file = output_path / rel_path
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(merge_state.final_doc.to_string(), encoding='utf-8')

        if progress_cb:
            progress_cb(total, total)
        return results
