"""
全局类型别名和 delta 数据结构

提供 JSON 值类型别名、cancel_check 回调类型，以及统一的 delta 差异描述类型。
"""
from __future__ import annotations

import enum
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

# JSON 值类型（务实方案：不做完全递归 TypeAlias，避免 mypy 3.10 下的递归类型问题）
JsonPrimitive = bool | int | float | str | None
JsonValue = JsonPrimitive | list[object] | dict[str, object]
JsonObject = dict[str, object]

# cancel_check 回调类型（无参数、无返回值）
CancelCheck = Callable[[], None]

# 路径分隔符（内部使用，避免和 JSON key 中的点号冲突）
FIELD_SEP: str = '\x01'


# ── delta 差异描述类型 ──


class ChangeKind(enum.IntEnum):
    """字段变化类型"""
    ADDED = 1
    DELETED = 2
    CHANGED = 3


@dataclass(slots=True)
class FieldDiff:
    """标量字段的差异标签。

    作为 delta 树的叶子节点，替代 _DELETED 哨兵和直接放的值。
    不含路径——嵌套位置本身即路径。
    """
    kind: ChangeKind
    value: object  # ADDED/CHANGED: 新值; DELETED: None


@dataclass
class DictDelta:
    """dict 的字段级 delta。

    每个 key 映射到叶子(FieldDiff)、嵌套变化(DictDelta)或数组变化(ArrayFieldDiff)。
    """
    items: dict[str, FieldDiff | DictDelta | ArrayFieldDiff] = field(
        default_factory=dict,
    )


@dataclass
class ArrayFieldDiff:
    """数组的元素级 delta，基于 ID 追踪。

    ID 规则:
    - 原始数组元素 ID = 1-based 索引（第 1 个元素 ID=1）
    - 新增元素 ID = base_count + 递增序号
    - 特殊值: 0 = 数组开头, -1 = 数组末尾
    - 约束: CHANGED/DELETED 的 ID 必须 <= base_count
    """
    diffs: list[FieldDiff]    # 每个变化元素的 diff
    base_count: int           # base 数组的元素数量
    indices: list[int]        # diffs 中每个元素的 ID (len == len(diffs))
    order: list[int]          # 应用 delta 后数组的完整顺序（含边界标记 0/-1）


@dataclass(slots=True)
class ArrayMatching:
    """base 与 mod 数组的元素对应关系。索引均为 0-based。"""
    pairs: list[tuple[int, int]]        # (base_idx, mod_idx)
    unmatched_mod: list[int]            # mod 中无对应 base 的索引
    unmatched_base: list[int]           # base 中无对应 mod 的索引
    confidence: float = 1.0             # 匹配置信度（0.0~1.0），1.0 表示完全可信


# ── schema_generator 字段信息 TypedDict ──


class GlobalFieldEntry(TypedDict):
    """_global_field_info 中每个条目的类型"""
    child_keys: set[str]
    child_key_counts: dict[str, int]
    child_key_types: dict[str, dict[str, int]]
    elem_child_key_counts: dict[str, int]
    elem_child_key_types: dict[str, dict[str, int]]
    count: int
    paths: set[str]


class FieldInfo(TypedDict, total=False):
    """collect_field_info 收集的单路径字段信息"""
    types: set[str]
    child_keys: set[str]
    array_elem_types: set[str]
    has_guid: bool
    has_condition: bool
    has_action: bool
    has_result_title: bool
    has_tag: bool
    has_id: bool
    has_key: bool
    sample_values: list[object]
    count: int
    child_key_counts: dict[str, int]
    child_key_types: dict[str, dict[str, int]]


# ── JSON 解析失败记录 ──


@dataclass
class ParseFailure:
    """JSON 解析失败的记录"""
    file_path: Path
    rel_path: str
    error_msg: str
    error_line: int
    is_base: bool
    mod_id: str
    mod_name: str

    @classmethod
    def from_error(
        cls,
        error: json.JSONDecodeError,
        file_path: Path,
        rel_path: str,
        *,
        is_base: bool = False,
        mod_id: str = "",
        mod_name: str = "",
    ) -> ParseFailure:
        """从 JSONDecodeError 构造 ParseFailure"""
        return cls(
            file_path=file_path,
            rel_path=rel_path,
            error_msg=error.msg,
            error_line=getattr(error, 'lineno', 0) or 0,
            is_base=is_base,
            mod_id=mod_id,
            mod_name=mod_name,
        )


# ── 路径工具 ──


def normalize_rel_path(path: Path, base: Path) -> str:
    """计算相对路径并规范化分隔符为 /"""
    return str(path.relative_to(base)).replace("\\", "/")
