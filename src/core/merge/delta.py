"""
全局 Delta 缓存管理器

启动时预计算所有 mod 相对于游戏本体的 delta，缓存结果供冲突分析、
合并、Diff 对话框等模块直接取用，避免重复计算。

所有方法和属性均为类级别，直接通过 ModDelta.get(...) 调用。
"""
from collections import defaultdict

from sultan_core.delta import (
    DeltaDict,
    batch_process_all_groups as _cpp_batch,
)
from sultan_core.json import JsonDoc
from sultan_core.state import MergeMode as CppMergeMode

from ..data_manager import DataManager
from ..infra.profiler import profile
from ..infra.types import (
    MergeMode,
    ProgressCallback,
)

# ==================== MergeMode 映射 ====================


_CPP_MODE: dict[MergeMode, CppMergeMode] = {
    MergeMode.NORMAL: CppMergeMode.NORMAL,
    MergeMode.SMART: CppMergeMode.SMART,
    MergeMode.REPLACE: CppMergeMode.REPLACE,
    MergeMode.ADAPTIVE: CppMergeMode.ADAPTIVE,
}


def _to_cpp_mode(mode: MergeMode) -> CppMergeMode:
    return _CPP_MODE[mode]

# ==================== init() 辅助函数 ====================


def _effective_mode(
    mod_id: str,
    merge_mode: MergeMode,
    mod_merge_modes: dict[str, MergeMode] | None,
) -> MergeMode:
    if mod_merge_modes and mod_id in mod_merge_modes:
        return mod_merge_modes[mod_id]
    return merge_mode


# ==================== 全局 Delta 缓存 ====================


class ModDelta:
    """全局 Delta 缓存管理器（纯静态类）。

    启动时调用 init() 预计算所有 delta，后续通过 get() 直接取缓存结果。
    缓存存储 C++ DeltaDict（内存中的 delta 树）。
    """

    _cache: dict[tuple[str, str], DeltaDict | None] = {}
    _progress: tuple[int, int] = (0, 0)

    @classmethod
    @profile
    def init(
        cls,
        mod_ids: list[str],
        progress_cb: ProgressCallback | None = None,
        merge_mode: MergeMode = MergeMode.SMART,
        mod_merge_modes: dict[str, MergeMode] | None = None,
    ) -> None:
        dm = DataManager.instance()

        tasks_by_file: dict[str, list[str]] = defaultdict(list)
        for mod_id in mod_ids:
            for rel_path in dm.mod_files(mod_id):
                tasks_by_file[rel_path].append(mod_id)

        total = sum(len(mids) for mids in tasks_by_file.values())
        cls._cache.clear()
        cls._progress = (0, total)
        if progress_cb:
            progress_cb(0, total)

        base_docs_list: list[JsonDoc] = []
        group_offsets: list[int] = []
        flat_modes: list[CppMergeMode] = []
        flat_mod_docs: list[JsonDoc] = []
        flat_hist_docs: list[JsonDoc | None] = []
        flat_keys: list[tuple[str, str]] = []

        for rel_path, file_mod_ids in tasks_by_file.items():
            base_docs_list.append(dm.get_base(rel_path))
            group_offsets.append(len(flat_modes))
            for mod_id in file_mod_ids:
                effective = _effective_mode(mod_id, merge_mode, mod_merge_modes)
                flat_modes.append(_to_cpp_mode(effective))
                flat_mod_docs.append(dm.get_mod(mod_id, rel_path))
                flat_hist_docs.append(
                    dm.get_history_base(mod_id, rel_path)
                    if effective == MergeMode.ADAPTIVE else None
                )
                flat_keys.append((mod_id, rel_path))

        deltas = _cpp_batch(
            base_docs_list, group_offsets,
            flat_modes, flat_mod_docs, flat_hist_docs,
        )

        for (mod_id, rel_path), delta in zip(flat_keys, deltas):
            cls._cache[(mod_id, rel_path)] = delta

        cls._progress = (total, total)
        if progress_cb:
            progress_cb(total, total)

    @classmethod
    def get(cls, mod_id: str, rel_path: str) -> DeltaDict | None:
        return cls._cache[(mod_id, rel_path)]

    @classmethod
    def has(cls, mod_id: str, rel_path: str) -> bool:
        return (mod_id, rel_path) in cls._cache

    @classmethod
    def progress(cls) -> tuple[int, int]:
        return cls._progress

    @classmethod
    def invalidate(cls) -> None:
        cls._cache.clear()
        cls._progress = (0, 0)

    @classmethod
    def clear(cls) -> None:
        cls._cache.clear()
        cls._progress = (0, 0)
