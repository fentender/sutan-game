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
from typing import TypedDict, cast

from beartype.door import die_if_unbearable

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


# ── delta 差异描述类型 ──

# type 语句 RHS 延迟求值，可在类定义之前声明
type DiffEntry = FieldDiff | DictFieldDiff | ArrayFieldDiff


class ChangeKind(enum.IntFlag):
    """字段变化类型（二进制标志位）。

    低 2 位为基础类型（ORIGIN/ADDED/DELETED/CHANGED 四选一），
    bit2 为 MULTI_MOD（被多个 mod 修改），
    bit3 为 OVERRIDE（被用户手动覆写）。
    """
    ORIGIN    = 0    # 未修改，来自 base
    ADDED     = 1    # 新增
    DELETED   = 2    # 删除
    CHANGED   = 3    # 修改
    MULTI_MOD = 4    # 标志位：被多个 mod 修改过（冲突标记）
    OVERRIDE  = 8    # 标志位：被用户手动覆写

    @property
    def base_kind(self) -> ChangeKind:
        """提取基础变化类型，去掉修饰标志"""
        return ChangeKind.__new__(ChangeKind, self._value_ & 0x03)

    @property
    def flags(self) -> ChangeKind:
        """提取修饰标志位，去掉基础变化类型"""
        return ChangeKind.__new__(ChangeKind, self._value_ & ~0x03)

    @property
    def is_multi_mod(self) -> bool:
        """是否被多个 mod 修改过"""
        return bool(self._value_ & 4)

    @property
    def is_override(self) -> bool:
        """是否被用户手动覆写"""
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


class DupList(list):
    """JSON 重复键展开后的值列表。

    游戏 JSON 中同一对象内可出现同名键（如 "type":"char", "type":"item"），
    解析时将同名键的多个值收集为 DupList，序列化时还原为重复键。
    与普通 list 通过类型区分，合并逻辑按索引逐元素处理。
    """


@dataclass(slots=True)
class FieldDiff:
    """标量字段的差异标签。

    作为 delta 树的叶子节点，替代 _DELETED 哨兵和直接放的值。
    不含路径——嵌套位置本身即路径。
    """
    kind: ChangeKind
    value: JsonValue
    old_value: JsonValue = None
    version: int = 0    # 哪次 mod 迭代修改了此字段（0=原始）

    def __setattr__(self, name: str, val: object) -> None:
        if name in ("value", "old_value"):
            die_if_unbearable(val, JsonValue)  # pyright: ignore[reportArgumentType]
        object.__setattr__(self, name, val)

    @property
    def is_modified(self) -> bool:
        return self.kind.base_kind != ChangeKind.ORIGIN

    def to_delta_dict(self) -> JsonObject:
        """序列化为可 JSON 化的 dict，保留完整 ChangeKind"""
        result: JsonObject = {
            "__type": "field",
            "kind": self.kind.value,
        }
        result["version"] = self.version
        result["value"] = self.value
        if self.old_value is not None:
            result["old_value"] = self.old_value
        return result

    @classmethod
    def from_delta(cls, raw: JsonObject) -> FieldDiff:
        """从序列化 dict 恢复 FieldDiff"""
        kind = ChangeKind(cast(int, raw["kind"]))
        value = raw["value"]
        old_value = raw.get("old_value")
        version = cast(int, raw["version"])
        return cls(kind=kind, value=value, old_value=old_value, version=version)


@dataclass
class DictFieldDiff:
    """dict 的字段级 delta / 全状态注解树。

    作为稀疏 delta 时（compute_delta 产出）：仅含被修改的 key。
    作为全状态树时（from_dict 产出）：包含所有 key，每个标注 ChangeKind。
    """
    items: dict[str, DiffEntry] = field(
        default_factory=dict,
    )

    @property
    def is_modified(self) -> bool:
        return any(v.is_modified for v in self.items.values())

    @classmethod
    def from_dict(cls, data: JsonObject) -> DictFieldDiff:
        """将普通 dict 转换为全状态 DiffDict，每个字段初始为 ORIGIN"""
        items: dict[str, DiffEntry] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                items[key] = cls.from_dict(value)
            elif isinstance(value, (list, DupList)):
                items[key] = ArrayFieldDiff.from_list(value)
            else:
                items[key] = FieldDiff(ChangeKind.ORIGIN, value)
        return cls(items=items)

    def to_dict(self) -> JsonObject:
        """转换回普通 dict，跳过 DELETED 字段"""
        result: JsonObject = {}
        for key, diff in self.items.items():
            if isinstance(diff, FieldDiff):
                if diff.kind.base_kind == ChangeKind.DELETED:
                    continue
                result[key] = diff.value
            elif isinstance(diff, DictFieldDiff):
                result[key] = diff.to_dict()
            elif isinstance(diff, ArrayFieldDiff):
                result[key] = diff.to_list()
            else:
                raise TypeError(f"字段 '{key}' 类型异常: {type(diff)}")
        return result

    def to_delta_dict(self) -> JsonObject:
        """将稀疏 delta DiffDict 序列化为可 JSON 化的 dict（保留 ChangeKind）"""
        items: JsonObject = {}
        for key, diff in self.items.items():
            if isinstance(diff, FieldDiff):
                items[key] = diff.to_delta_dict()
            elif isinstance(diff, DictFieldDiff):
                items[key] = diff.to_delta_dict()
            elif isinstance(diff, ArrayFieldDiff):
                items[key] = diff.to_delta_dict()
            else:
                raise TypeError(f"字段 '{key}' 类型异常: {type(diff)}")
        return {"__type": "dict_delta", "items": items}

    @classmethod
    def from_delta_dict(cls, data: JsonObject) -> DictFieldDiff:
        """从序列化的 delta dict 恢复 DictFieldDiff"""
        items_raw = cast(JsonObject, data["items"])
        items: dict[str, DiffEntry] = {}
        for key, raw in items_raw.items():
            items[key] = _delta_entry_from_dict(cast(JsonObject, raw))
        return cls(items=items)


@dataclass
class ArrayFieldDiff:
    """数组的元素级 delta，基于 ID 追踪。

    ID 规则:
    - 原始数组元素 ID = 1-based 索引（第 1 个元素 ID=1）
    - 新增元素 ID = base_count + 递增序号
    - 特殊值: 0 = 数组开头, -1 = 数组末尾
    - 约束: CHANGED/DELETED 的 ID 必须 <= base_count

    作为全状态时（from_list 产出）：diffs 包含所有元素，每个标注 ChangeKind。
    """
    diffs: list[DiffEntry]   # 每个变化元素的 diff
    base_count: int           # base 数组的元素数量
    indices: list[int]        # diffs 中每个元素的 ID (len == len(diffs))
    order: list[int]          # 应用 delta 后数组的完整顺序（含边界标记 0/-1）
    is_duplist: bool = False  # 原始数组是否为 DupList（重复键序列化需要）
    old_order: list[int] | None = None  # apply_array_delta 重建 order 时保存旧 order

    @property
    def is_modified(self) -> bool:
        return any(d.is_modified for d in self.diffs)

    @classmethod
    def wrap(cls, value: DiffEntry | None, is_dup: bool) -> ArrayFieldDiff:
        if value is None:
            return ArrayFieldDiff(
                diffs=[], indices=[], base_count=0,
                order=[0, -1], is_duplist=is_dup,
            )
        return ArrayFieldDiff(
            diffs=[value], indices=[1], base_count=1,
            order=[0, 1, -1], is_duplist=is_dup,
        )

    @classmethod
    def from_list(cls, data: JsonArray) -> ArrayFieldDiff:
        """将普通 list 转换为全状态 ArrayFieldDiff，每个元素初始为 ORIGIN"""
        diffs: list[DiffEntry] = []
        for elem in data:
            if isinstance(elem, dict):
                diffs.append(DictFieldDiff.from_dict(elem))
            elif isinstance(elem, (list, DupList)):
                diffs.append(cls.from_list(elem))
            else:
                diffs.append(FieldDiff(ChangeKind.ORIGIN, elem))
        n = len(data)
        return cls(
            diffs=diffs,
            base_count=n,
            indices=list(range(1, n + 1)),
            order=[0, *range(1, n + 1), -1],
            is_duplist=isinstance(data, DupList),
        )

    def to_list(self) -> JsonArray:
        """按 order 还原为普通 list，跳过 DELETED 元素"""
        id_to_diff = dict(zip(self.indices, self.diffs, strict=True))
        result: JsonArray = []
        for eid in self.order:
            if eid == 0 or eid == -1:
                continue
            diff = id_to_diff.get(eid)
            if diff is None:
                continue
            if isinstance(diff, FieldDiff):
                if diff.kind.base_kind == ChangeKind.DELETED:
                    continue
                result.append(diff.value)
            elif isinstance(diff, DictFieldDiff):
                result.append(diff.to_dict())
            elif isinstance(diff, ArrayFieldDiff):
                result.append(diff.to_list())
        return DupList(result) if self.is_duplist else result

    def to_delta_dict(self) -> JsonObject:
        """序列化 ArrayFieldDiff 为可 JSON 化的 dict"""
        serialized: list[JsonObject] = []
        for d in self.diffs:
            if isinstance(d, FieldDiff):
                serialized.append(d.to_delta_dict())
            elif isinstance(d, DictFieldDiff):
                serialized.append(d.to_delta_dict())
            elif isinstance(d, ArrayFieldDiff):
                serialized.append(d.to_delta_dict())
            else:
                raise TypeError(f"数组元素类型异常: {type(d)}")
        result: JsonObject = {
            "__type": "array_delta",
            "diffs": cast(JsonArray, serialized),
            "base_count": self.base_count,
            "indices": cast(JsonArray, self.indices),
            "order": cast(JsonArray, self.order),
            "is_duplist": self.is_duplist,
        }
        if self.old_order is not None:
            result["old_order"] = cast(JsonArray, self.old_order)
        return result

    @classmethod
    def from_delta_dict(cls, data: JsonObject) -> ArrayFieldDiff:
        """从序列化 dict 恢复 ArrayFieldDiff"""
        raw_diffs = cast(list[JsonObject], data["diffs"])
        diffs: list[DiffEntry] = [_delta_entry_from_dict(d) for d in raw_diffs]
        raw_old_order = data.get("old_order")
        return cls(
            diffs=diffs,
            base_count=cast(int, data["base_count"]),
            indices=list(cast(list[int], data["indices"])),
            order=list(cast(list[int], data["order"])),
            is_duplist=bool(data.get("is_duplist", False)),
            old_order=list(cast(list[int], raw_old_order)) if raw_old_order is not None else None,
        )


# ── delta 序列化辅助函数 ──

def _delta_entry_from_dict(raw: JsonObject) -> DiffEntry:
    """根据 __type 标记恢复对应类型"""
    kind_str = cast(str, raw["__type"])
    if kind_str == "dict_delta":
        return DictFieldDiff.from_delta_dict(raw)
    if kind_str == "array_delta":
        return ArrayFieldDiff.from_delta_dict(raw)
    # FieldDiff 类型
    return FieldDiff.from_delta(raw)


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
