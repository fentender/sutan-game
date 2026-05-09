"""
合并结果数据结构 — 供 diff_dialog 和 merger 使用
"""
from dataclasses import dataclass, field

from sultan_core.json import JsonDoc

from ..infra.types import ChangeKind


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
