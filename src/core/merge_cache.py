"""
合并结果缓存 — 统一合并计算逻辑，避免 diff_dialog 和 merger 重复计算

单例 MergeCache 按文件缓存合并结果，包含逐 mod 中间状态（diff_dialog 用）
和最终合并字典（merger 用）。缓存在 mod 列表/模式/override 变更时失效。
"""
from dataclasses import dataclass, field
from pathlib import Path

from .delta_store import ModDelta
from .diagnostics import diag, merge_ctx
from .diff_formatter import format_delta_json
from .json_store import JsonStore
from .merger import apply_delta
from .profiler import profile, profile_block
from .schema_loader import get_schema_root_key, load_schemas, resolve_schema
from .types import ChangeKind, DiffDict


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
    final_dict: dict[str, object]
    steps: list[StepState] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class MergeCache:
    """合并结果缓存单例"""

    _instance: "MergeCache | None" = None

    def __init__(self) -> None:
        self._cache: dict[str, FileMergeState] = {}
        self._schemas: dict[str, dict[str, object]] | None = None
        self._schema_dir: Path | None = None

    @classmethod
    def instance(cls) -> "MergeCache":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def invalidate(self, rel_path: str) -> None:
        """使单个文件的缓存失效"""
        self._cache.pop(rel_path, None)

    def invalidate_all(self) -> None:
        """清除所有缓存"""
        self._cache.clear()
        self._schemas = None
        self._schema_dir = None

    def get(
        self,
        rel_path: str,
        mod_configs: list[tuple[str, str, Path]],
        schema_dir: Path | None = None,
        need_steps: bool = True,
    ) -> FileMergeState:
        """获取文件的合并状态，有缓存直接返回，否则计算并缓存。

        need_steps=False 时跳过 format_delta_json（merger 路径无需可视化数据）。
        注意：need_steps=False 的结果缓存后，后续 need_steps=True 会重新计算。
        """
        cached = self._cache.get(rel_path)
        if cached is not None:
            if need_steps and not cached.steps:
                # 之前是 need_steps=False 缓存的，需要重新计算
                pass
            else:
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
        """统一的合并循环：一次遍历产出中间状态和最终结果"""
        diag.snapshot("merge")
        store = JsonStore.instance()
        base_data = store.get_base(rel_path)

        # schemas 缓存：同一 schema_dir 只加载一次
        if schema_dir and (self._schemas is None or self._schema_dir != schema_dir):
            self._schemas = load_schemas(schema_dir)
            self._schema_dir = schema_dir
        schemas = self._schemas if self._schemas else {}
        schema = resolve_schema(rel_path, schemas) if schemas else None
        root_key = get_schema_root_key(schema) if schema else None

        current = DiffDict.from_dict(base_data)
        steps: list[StepState] = []
        mod_version = 0

        for mod_id, mod_name, config_path in mod_configs:
            if not store.has_mod(mod_id, rel_path):
                continue

            merge_ctx.mod_name = mod_name
            merge_ctx.mod_id = mod_id
            merge_ctx.rel_path = rel_path
            merge_ctx.source_file = str(config_path / rel_path)

            delta = ModDelta.get(mod_id, rel_path)
            if not delta:
                continue

            field_path = [root_key] if root_key else None
            mod_version += 1
            with profile_block("merge_cache.apply_delta"):
                apply_delta(current, delta, schema, field_path,
                            version=mod_version)

            # 检查用户 override delta
            override_delta = store.get_override(mod_id, rel_path)
            if override_delta is not None:
                with profile_block("merge_cache.apply_override"):
                    apply_delta(current, override_delta, schema, field_path,
                                version=mod_version, is_override=True)

            # 产出中间状态（diff_dialog 用，merger 路径跳过）
            if need_steps:
                with profile_block("merge_cache.format_delta_json"):
                    left_lines, right_lines, left_kinds, right_kinds = format_delta_json(
                        current, highlight_version=mod_version,
                    )

                steps.append(StepState(
                    mod_id=mod_id,
                    mod_name=mod_name,
                    left_lines=left_lines,
                    right_lines=right_lines,
                    left_kinds=left_kinds,
                    right_kinds=right_kinds,
                ))

        warnings = [msg for _, msg in diag.snapshot("merge")]

        return FileMergeState(
            final_dict=current.to_dict(),
            steps=steps,
            warnings=warnings,
        )
