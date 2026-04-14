"""
全局类型别名和哨兵类型

提供 JSON 值类型别名、cancel_check 回调类型，以及合并 delta 中的删除哨兵类型。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar, TypedDict

# JSON 值类型（务实方案：不做完全递归 TypeAlias，避免 mypy 3.10 下的递归类型问题）
JsonPrimitive = bool | int | float | str | None
JsonValue = JsonPrimitive | list[object] | dict[str, object]
JsonObject = dict[str, object]

# cancel_check 回调类型（无参数、无返回值）
CancelCheck = Callable[[], None]


class _DeletedType:
    """合并 delta 中标记字段删除的哨兵单例类型。

    替代 `_DELETED = object()`，使 mypy 能区分"删除标记"和普通 JSON 值。
    `object()` 与所有 JSON 值类型相同，mypy 无法在类型层面区分；
    `_DeletedType` 是唯一的具名类型，可在 isinstance / is 检查中被识别。
    """

    _instance: ClassVar[_DeletedType | None] = None

    def __new__(cls) -> _DeletedType:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<_DELETED>"

    def __bool__(self) -> bool:
        return True


_DELETED: _DeletedType = _DeletedType()


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
