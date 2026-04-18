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


# ── 合并模式 ──


class MergeMode(enum.Enum):
    """合并模式枚举。

    NORMAL: 正常合并——全部应用 APPEND/CHANGE/DELETED
    SMART:  智能合并——APPEND/CHANGE 全部应用，DELETED 按字段规则选择性应用
    REPLACE: 简单替换——直接用 Mod 文件替换，不做字段级合并
    """
    NORMAL = "normal"
    SMART = "smart"
    REPLACE = "replace"


# ── delta 差异描述类型 ──


class ChangeKind(enum.IntFlag):
    """字段变化类型（二进制标志位）。

    低 3 位为基础类型（ORIGIN/ADDED/DELETED/CHANGED 四选一），
    高位为修饰标志（MULTI_MOD 表示被多个 mod 修改）。
    """
    ORIGIN    = 0    # 未修改，来自 base
    ADDED     = 1    # 新增
    DELETED   = 2    # 删除
    CHANGED   = 4    # 修改
    MULTI_MOD = 8    # 标志位：被多个 mod 修改过（冲突标记）

    @property
    def base_kind(self) -> ChangeKind:
        """提取基础变化类型，去掉修饰标志"""
        return ChangeKind(self & 0x07)

    @property
    def is_multi_mod(self) -> bool:
        """是否被多个 mod 修改过"""
        return bool(self & ChangeKind.MULTI_MOD)


@dataclass(slots=True)
class FieldDiff:
    """标量字段的差异标签。

    作为 delta 树的叶子节点，替代 _DELETED 哨兵和直接放的值。
    不含路径——嵌套位置本身即路径。
    """
    kind: ChangeKind
    value: object       # ADDED/CHANGED: 新值; DELETED: None; ORIGIN: 当前值
    old_value: object = None   # CHANGED: 旧值; DELETED: 被删除的值; ADDED/ORIGIN: None
    version: int = 0    # 哪次 mod 迭代修改了此字段（0=原始）


@dataclass
class DiffDict:
    """dict 的字段级 delta / 全状态注解树。

    作为稀疏 delta 时（compute_delta 产出）：仅含被修改的 key。
    作为全状态树时（from_dict 产出）：包含所有 key，每个标注 ChangeKind。
    """
    items: dict[str, FieldDiff | DiffDict | ArrayFieldDiff] = field(
        default_factory=dict,
    )

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> DiffDict:
        """将普通 dict 转换为全状态 DiffDict，每个字段初始为 ORIGIN"""
        from .json_parser import DupList
        items: dict[str, FieldDiff | DiffDict | ArrayFieldDiff] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                items[key] = cls.from_dict(value)
            elif isinstance(value, (list, DupList)):
                items[key] = ArrayFieldDiff.from_list(value)
            else:
                items[key] = FieldDiff(ChangeKind.ORIGIN, value)
        return cls(items=items)

    def to_dict(self) -> dict[str, object]:
        """转换回普通 dict，跳过 DELETED 字段"""
        result: dict[str, object] = {}
        for key, diff in self.items.items():
            if isinstance(diff, FieldDiff):
                if diff.kind.base_kind == ChangeKind.DELETED:
                    continue
                val = diff.value
                if isinstance(val, DiffDict):
                    result[key] = val.to_dict()
                elif isinstance(val, ArrayFieldDiff):
                    result[key] = val.to_list()
                else:
                    result[key] = val
            elif isinstance(diff, DiffDict):
                result[key] = diff.to_dict()
            elif isinstance(diff, ArrayFieldDiff):
                result[key] = diff.to_list()
        return result


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
    diffs: list[FieldDiff]    # 每个变化元素的 diff
    base_count: int           # base 数组的元素数量
    indices: list[int]        # diffs 中每个元素的 ID (len == len(diffs))
    order: list[int]          # 应用 delta 后数组的完整顺序（含边界标记 0/-1）
    is_duplist: bool = False  # 原始数组是否为 DupList（重复键序列化需要）
    old_order: list[int] | None = None  # apply_array_delta 重建 order 时保存旧 order

    @classmethod
    def from_list(cls, data: list[object]) -> ArrayFieldDiff:
        """将普通 list 转换为全状态 ArrayFieldDiff，每个元素初始为 ORIGIN"""
        from .json_parser import DupList
        diffs: list[FieldDiff] = []
        for elem in data:
            if isinstance(elem, dict):
                diffs.append(FieldDiff(ChangeKind.ORIGIN, DiffDict.from_dict(elem)))
            elif isinstance(elem, (list, DupList)):
                diffs.append(FieldDiff(ChangeKind.ORIGIN, cls.from_list(elem)))
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

    def to_list(self) -> list[object]:
        """按 order 还原为普通 list，跳过 DELETED 元素"""
        from .json_parser import DupList
        id_to_diff = dict(zip(self.indices, self.diffs))
        result: list[object] = []
        for eid in self.order:
            if eid == 0 or eid == -1:
                continue
            diff = id_to_diff.get(eid)
            if diff is None or diff.kind.base_kind == ChangeKind.DELETED:
                continue
            val = diff.value
            if isinstance(val, DiffDict):
                result.append(val.to_dict())
            elif isinstance(val, ArrayFieldDiff):
                result.append(val.to_list())
            else:
                result.append(val)
        return DupList(result) if self.is_duplist else result


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
