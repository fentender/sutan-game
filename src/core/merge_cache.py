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
from .json_parser import format_json
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

    def get(
        self,
        rel_path: str,
        mod_configs: list[tuple[str, str, Path]],
        schema_dir: Path | None = None,
    ) -> FileMergeState:
        """获取文件的合并状态，有缓存直接返回，否则计算并缓存"""
        if rel_path in self._cache:
            return self._cache[rel_path]

        state = self._compute_file(rel_path, mod_configs, schema_dir)
        self._cache[rel_path] = state
        return state

    @profile
    def _compute_file(
        self,
        rel_path: str,
        mod_configs: list[tuple[str, str, Path]],
        schema_dir: Path | None = None,
    ) -> FileMergeState:
        """统一的合并循环：一次遍历产出中间状态和最终结果"""
        diag.snapshot("merge")
        store = JsonStore.instance()
        base_data = store.get_base(rel_path)

        schemas = load_schemas(schema_dir) if schema_dir else {}
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

            # 产出中间状态（diff_dialog 用）
            with profile_block("merge_cache.format_delta_json"):
                left_lines, right_lines, left_kinds, right_kinds = format_delta_json(
                    current, highlight_version=mod_version,
                )

            # 检查用户 override
            override = store.get_override(mod_id, rel_path)
            if override is not None:
                override_text = format_json(override)
                right_lines = override_text.splitlines()
                right_kinds_override: list[ChangeKind | None] = [None] * len(right_lines)
                max_len = max(len(left_lines), len(right_lines))
                while len(left_lines) < max_len:
                    left_lines.append('')
                    left_kinds.append(None)
                while len(right_lines) < max_len:
                    right_lines.append('')
                    right_kinds_override.append(None)
                right_kinds = right_kinds_override
                current = DiffDict.from_dict(override)

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
