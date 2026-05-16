"""
全局类型别名

提供 JSON 值类型别名、cancel_check 回调类型。
"""
from __future__ import annotations

import enum
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

# JSON 值类型（Python 3.12+ type 语句，支持递归定义）
type JsonPrimitive = bool | int | float | str | None
type JsonValue = JsonPrimitive | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]

# cancel_check 回调类型（无参数、无返回值）
type CancelCheck = Callable[[], None]

# 进度回调类型 (completed, total)
type ProgressCallback = Callable[[int, int], None]

# 路径分隔符（内部使用，避免和 JSON key 中的点号冲突）
FIELD_SEP: str = '\x01'


# ── 合并模式 ──


class MergeMode(enum.Enum):
    """合并模式枚举。

    NORMAL: 正常合并——全部应用 APPEND/CHANGE/DELETED
    SMART:  智能合并——APPEND/CHANGE 全部应用，DELETED 按字段规则选择性应用
    REPLACE: 简单替换——直接用 Mod 文件替换，不做字段级合并
    ADAPTIVE: 自适应合并——基于 Mod 更新时间匹配历史游戏版本计算差异，合并行为同 SMART
    """
    NORMAL = "normal"
    SMART = "smart"
    REPLACE = "replace"
    ADAPTIVE = "adaptive"


# ── ChangeKind ──


class ChangeKind(enum.IntFlag):
    """字段变化类型（二进制标志位）。

    低 2 位为基础类型（ORIGIN/ADDED/DELETED/CHANGED 四选一），
    bit2 为 MULTI_MOD（被多个 mod 修改），
    bit3 为 OVERRIDE（被用户手动覆写）。
    """
    ORIGIN    = 0
    ADDED     = 1
    DELETED   = 2
    CHANGED   = 3
    MULTI_MOD = 4
    OVERRIDE  = 8

    @property
    def base_kind(self) -> ChangeKind:
        return ChangeKind.__new__(ChangeKind, self._value_ & 0x03)

    @property
    def flags(self) -> ChangeKind:
        return ChangeKind.__new__(ChangeKind, self._value_ & ~0x03)

    @property
    def is_multi_mod(self) -> bool:
        return bool(self._value_ & 4)

    @property
    def is_override(self) -> bool:
        return bool(self._value_ & 8)

    @property
    def is_origin(self) -> bool:
        return (self._value_ & 0x03) == 0

    @property
    def is_added(self) -> bool:
        return (self._value_ & 0x03) == 1

    @property
    def is_deleted(self) -> bool:
        return (self._value_ & 0x03) == 2

    @property
    def is_changed(self) -> bool:
        return (self._value_ & 0x03) == 3


# ── DupList ──


class DupList(list):
    """JSON 重复键展开后的值列表。

    游戏 JSON 中同一对象内可出现同名键（如 "type":"char", "type":"item"），
    解析时将同名键的多个值收集为 DupList，序列化时还原为重复键。
    与普通 list 通过类型区分，合并逻辑按索引逐元素处理。
    """


def duplist_pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            current = result[key]
            if isinstance(current, DupList):
                current.append(value)
            else:
                result[key] = DupList([current, value])
        else:
            result[key] = value
    return result

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
