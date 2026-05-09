"""
合并结果缓存 — 统一合并计算逻辑，避免 diff_dialog 和 merger 重复计算

单例 MergeCache 按文件缓存合并结果，包含逐 mod 中间状态（diff_dialog 用）
和最终合并文档（merger 用）。缓存在 mod 列表/模式/override 变更时失效。
"""
from dataclasses import dataclass, field
from pathlib import Path

from sultan_core.json import JsonDoc
from sultan_core.state import JsonState
from sultan_core.delta import deserialize_and_apply

from ..infra.diagnostics import diag
from ..infra.profiler import profile
from ..infra.types import ChangeKind
from ..data_manager import DataManager
from .delta import ModDelta


@dataclass
class StepState:
    """单步合并后的中间状态（供 diff_dialog 可视化）"""
    mod_id: str
    mod_name: str
    left_lines: list[str]
    right_lines: list[str]
    left_kinds: list[ChangeKind | None]
    right_kinds: list[ChangeKind | None]


@dataclass
class FileMergeState:
    """单个文件的完整合并状态"""
    final_doc: JsonDoc
    steps: list[StepState] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class MergeCache:
    """合并结果缓存单例"""

    _instance: "MergeCache | None" = None

    def __init__(self) -> None:
        self._cache: dict[str, FileMergeState] = {}

    @classmethod
    def instance(cls) -> "MergeCache":
        if cls._instance is None:
            cls._instance = cls()
            DataManager.instance().set_on_override_change(cls._instance.invalidate)
        return cls._instance

    def invalidate(self, rel_path: str) -> None:
        self._cache.pop(rel_path, None)

    def invalidate_all(self) -> None:
        self._cache.clear()

    def get(
        self,
        rel_path: str,
        mod_configs: list[tuple[str, str, Path]],
        schema_dir: Path | None = None,
        need_steps: bool = True,
    ) -> FileMergeState:
        """获取文件的合并状态，有缓存直接返回，否则计算并缓存。

        need_steps=False 时跳过 format（merger 路径无需可视化数据）。
        注意：need_steps=False 的结果缓存后，后续 need_steps=True 会重新计算。
        """
        cached = self._cache.get(rel_path)
        if cached is not None and (not need_steps or cached.steps):
            return cached

        state = self._compute_file(rel_path, mod_configs, schema_dir, need_steps)
        self._cache[rel_path] = state
        return state

    @profile
    def _compute_file(
        self,
        rel_path: str,
        mod_configs: list[tuple[str, str, Path]],
        schema_dir: Path | None = None,
        need_steps: bool = True,
    ) -> FileMergeState:
        """统一的合并循环：C++ State/Delta/Format 一次遍历产出中间状态和最终结果"""
        diag.snapshot("merge")
        dm = DataManager.instance()
        base_doc = dm.get_base(rel_path)
        state = JsonState.from_doc(base_doc)

        mod_data_list: list[tuple[str, str, JsonDoc]] = []
        for mod_id, mod_name, _ in mod_configs:
            if not dm.has_mod(mod_id, rel_path):
                continue
            delta_doc = ModDelta.get(mod_id, rel_path)
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
