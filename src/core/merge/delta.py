"""
全局 Delta 缓存管理器

启动时预计算所有 mod 相对于游戏本体的 delta，缓存结果供冲突分析、
合并、Diff 对话框等模块直接取用，避免重复计算。

所有方法和属性均为类级别，直接通过 ModDelta.get(...) 调用。
"""
from collections import defaultdict

from sultan_core.delta import (
    DeltaDict,
    apply_delta,
    compute_delta,
    remap_delta,
)
from sultan_core.state import JsonState, MergeMode as CppMergeMode

from ..data_manager import DataManager
from ..infra.profiler import profile
from ..infra.types import (
    CancelCheck,
    MergeMode,
    ProgressCallback,
)
from sultan_core.json_ops import classify_json

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


@profile
def _process_file_group(
    rel_path: str,
    file_mod_ids: list[str],
    dm: DataManager,
    merge_mode: MergeMode,
    mod_merge_modes: dict[str, MergeMode] | None,
) -> list[tuple[str, str, DeltaDict | None]]:
    """处理单个文件的所有 mod delta 计算（C++ API）。"""
    base_doc = dm.get_base(rel_path)
    is_dict = classify_json(base_doc) == "dictionary"

    state: JsonState | None = None
    results: list[tuple[str, str, DeltaDict | None]] = []

    for mod_id in file_mod_ids:
        effective = _effective_mode(mod_id, merge_mode, mod_merge_modes)
        mod_doc = dm.get_mod(mod_id, rel_path)
        cpp_mode = _to_cpp_mode(effective)

        if effective == MergeMode.REPLACE:
            cumulative_doc = state.to_doc() if state is not None else base_doc
            delta = compute_delta(
                cumulative_doc, mod_doc, cpp_mode, is_dict,
            )
        elif effective == MergeMode.ADAPTIVE:
            hist_doc = dm.get_history_base(mod_id, rel_path)
            adaptive_doc = hist_doc if hist_doc is not None else base_doc
            delta = compute_delta(
                adaptive_doc, mod_doc, CppMergeMode.SMART, is_dict,
            )
            if hist_doc is not None and delta is not None:
                if not remap_delta(delta, hist_doc, base_doc):
                    delta = None
        else:
            delta = compute_delta(
                base_doc, mod_doc, cpp_mode, is_dict,
            )

        valid = delta is not None
        results.append((mod_id, rel_path, delta if valid else None))

        if valid:
            if state is None:
                state = JsonState.from_doc(base_doc)
            apply_delta(delta, state, version=0)

    return results


# ==================== 全局 Delta 缓存 ====================


class ModDelta:
    """全局 Delta 缓存管理器（纯静态类）。

    启动时调用 init() 预计算所有 delta，后续通过 get() 直接取缓存结果。
    缓存存储 C++ DeltaDict（内存中的 delta 树）。
    """

    _cache: dict[tuple[str, str], DeltaDict | None] = {}
    _progress: tuple[int, int] = (0, 0)

    @classmethod
    def init(
        cls,
        mod_ids: list[str],
        progress_cb: ProgressCallback | None = None,
        merge_mode: MergeMode = MergeMode.SMART,
        mod_merge_modes: dict[str, MergeMode] | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> None:
        dm = DataManager.instance()

        tasks_by_file: dict[str, list[str]] = defaultdict(list)
        for mod_id in mod_ids:
            for rel_path in dm.mod_files(mod_id):
                tasks_by_file[rel_path].append(mod_id)

        total = sum(len(mids) for mids in tasks_by_file.values())
        completed = 0
        cls._cache.clear()
        cls._progress = (0, total)
        if progress_cb:
            progress_cb(0, total)

        for rel_path, file_mod_ids in tasks_by_file.items():
            if cancel_check:
                cancel_check()
            results = _process_file_group(
                rel_path, file_mod_ids, dm,
                merge_mode, mod_merge_modes,
            )
            for mod_id, rp, delta in results:
                cls._cache[(mod_id, rp)] = delta
                completed += 1
                cls._progress = (completed, total)
            if progress_cb:
                progress_cb(completed, total)

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
