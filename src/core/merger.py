"""
核心合并算法 - 基于 schema 规则的字典合并、实体合并、数组智能匹配
"""
import copy
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from .array_match import find_matching_item, get_key_vals, is_obj_array, resolve_duplicates
from .diagnostics import diag, merge_ctx
from .dsl_patterns import classify_dsl_key
from .json_parser import DupList, _pairs_hook, dump_json, load_json
from .mod_scanner import collect_mod_files
from .profiler import profile
from .schema_loader import (
    check_type_match,
    get_field_def,
    get_schema_root_key,
    load_schemas,
    resolve_schema,
)
from .type_utils import classify_json
from .types import _DELETED, CancelCheck, _DeletedType

# 需要整文件替换而非合并的文件
WHOLE_FILE_REPLACE = {'sfx_config.json'}


class DupListDelta(DupList):
    """DupList 的元素级 delta，含 _delta/_new_entry/_deleted 标记。

    与 DupList 区分：DupListDelta 表示一组变更指令，
    DupList 表示实际的重复键值列表。
    """
    pass


@dataclass
class OverrideRecord:
    """单个字段的覆盖记录"""
    file_path: str
    field_path: str
    base_value: object = None
    # [(mod_name, mod_id, value), ...]
    mod_values: list[tuple[str, str, object]] = field(default_factory=list)
    final_value: object = None


@dataclass
class MergeResult:
    """合并结果"""
    merged_data: dict[str, object] = field(default_factory=dict)
    overrides: list[OverrideRecord] = field(default_factory=list) # TODO： 没有用到的字段删掉
    new_entries: list[tuple[str, str, str]] = field(default_factory=list)  # (file, mod_name, description)


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


# ==================== 通用数组合并 ====================

def _strip_marker(item: dict[str, object], marker: str) -> dict[str, object]:
    """移除 delta 标记字段，返回清理后的副本"""
    return {k: v for k, v in item.items() if k != marker}


def _classify_delta_items(
    mod_arr: list[object], context: str = ""
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    """
    按 _delta / _new_entry / _deleted 标记分类 delta 数组元素。
    返回 (delta_items, new_entry_items, deleted_items)。
    """
    delta_items: list[dict[str, object]] = []
    new_entry_items: list[dict[str, object]] = []
    deleted_items: list[dict[str, object]] = []

    for mod_item in mod_arr:
        if not isinstance(mod_item, dict):
            continue
        if mod_item.get('_deleted'):
            deleted_items.append(mod_item)
        elif mod_item.get('_new_entry'):
            new_entry_items.append(mod_item)
        elif mod_item.get('_delta'):
            delta_items.append(mod_item)
        else:
            raise ValueError(
                f"{context}收到未标记的元素（缺少 _delta/_new_entry/_deleted），"
                f"元素 keys={list(mod_item.keys())}"
            )

    return delta_items, new_entry_items, deleted_items


@profile
def _apply_deltas_to_result(
    result: list[object],
    delta_items: list[dict[str, object]],
    match_keys: list[str],
    matched: set[int],
    schema: dict[str, object] | None,
    element_path: list[str] | None,
) -> None:
    """将 delta 元素按 match_key 匹配到 result 并 deep_merge，原地修改 result。"""
    # 建立 result 索引
    result_index: dict[tuple[object, ...], list[int]] = {}
    for ri, r_item in enumerate(result):
        if isinstance(r_item, dict):
            kv = get_key_vals(r_item, match_keys)
            if kv is not None:
                result_index.setdefault(kv, []).append(ri)

    # 按 key 分组 delta 元素
    delta_groups: dict[tuple[object, ...] | None, list[tuple[int, dict[str, object]]]] = {}
    for i, item in enumerate(delta_items):
        kv = get_key_vals(item, match_keys)
        delta_groups.setdefault(kv, []).append((i, item))

    for kv, items in delta_groups.items():
        if kv is None:
            for _, item in items:
                result.append(_strip_marker(item, '_delta'))
            continue

        res_candidates = [ri for ri in result_index.get(kv, []) if ri not in matched]

        if res_candidates:
            pairs, unmatched = resolve_duplicates(items, result, res_candidates)
            for _, mod_item, res_idx in pairs:
                clean = _strip_marker(mod_item, '_delta')
                result[res_idx] = deep_merge(result[res_idx], clean, schema, element_path)
                matched.add(res_idx)
            for _, mod_item in unmatched:
                result.append(_strip_marker(mod_item, '_delta'))
        else:
            for _, item in items:
                result.append(_strip_marker(item, '_delta'))


@profile
def _merge_settlement_array(
    base_arr: list[object],
    mod_arr: list[object],
    schema: dict[str, object] | None,
    element_path: list[str] | None,
    allow_deletions: bool = False,
) -> list[object]:
    """
    智能合并 array<object> 数组。
    mod_arr 必须是经过 _object_array_delta 产出的 delta 数组，
    每个元素必须带有 _delta / _new_entry / _deleted 标记。
    """
    result: list[object] = copy.deepcopy(base_arr)

    # 从 schema 读取 match_key
    match_keys: list[str] | None = None
    if schema and element_path:
        field_def = get_field_def(schema, element_path)
        if field_def:
            mk = field_def.get("__match_key__")
            match_keys = mk if isinstance(mk, list) else None

    if not match_keys:
        raise ValueError(
            f"smart_match 数组缺少 match_key 定义 (path: {element_path})"
        )

    delta_items, new_entry_items, deleted_items = _classify_delta_items(
        mod_arr, context=f"smart_match 数组 (match_keys={match_keys}) "
    )

    # 处理删除
    matched: set[int] = set()
    to_remove: set[int] = set()
    for mod_item in deleted_items:
        idx = find_matching_item(
            [r for r in result if isinstance(r, dict)],
            mod_item, matched, match_keys
        )
        if idx is not None:
            if allow_deletions:
                to_remove.add(idx)
            matched.add(idx)

    # 应用 delta
    _apply_deltas_to_result(result, delta_items, match_keys,
                            matched, schema, element_path)

    # 追加新增元素
    for item in new_entry_items:
        result.append(copy.deepcopy(_strip_marker(item, '_new_entry')))

    # 移除删除元素
    if to_remove:
        result = [item for i, item in enumerate(result) if i not in to_remove]

    return result


def _coerce_and_merge_array(base_val: object, override_val: object) -> object:
    """
    类型不匹配时的数组合并策略。
    将标量一侧包裹为单元素列表，然后合并（去重追加）。
    DupList 不参与 coerce——它是同名重复键的值集合，不是数组。
    """
    # DupList 不是真正的数组，直接替换
    if isinstance(base_val, DupList) or isinstance(override_val, DupList):
        return copy.deepcopy(override_val)
    if isinstance(base_val, list) and not isinstance(override_val, list):
        if override_val in base_val:
            return copy.deepcopy(base_val)
        result = copy.deepcopy(base_val)
        result.append(copy.deepcopy(override_val))
        return result
    elif not isinstance(base_val, list) and isinstance(override_val, list):
        if base_val in override_val:
            return copy.deepcopy(override_val)
        result = copy.deepcopy(override_val)
        result.insert(0, copy.deepcopy(base_val))
        return result
    else:
        return copy.deepcopy(override_val)


# 对象数组中常见的标识字段
COMMON_MATCH_KEYS = ('id', 'tag', 'guid', 'key')


def find_array_match_key(arr: list[object]) -> str | None:
    """在对象数组中找到可用于匹配的唯一标识字段"""
    if not arr or not all(isinstance(x, dict) for x in arr):
        return None
    for key in COMMON_MATCH_KEYS:
        values = []
        for item in arr:
            if not isinstance(item, dict) or key not in item:
                break
            values.append(item[key])
        else:
            if len({(type(v), v) for v in values}) == len(values):
                return key
    return None


def _has_index_markers(arr: list[object]) -> bool:
    """检测数组是否来自 _index_array_delta（含 _index 标记的 delta）"""
    return (bool(arr)
            and all(isinstance(x, dict) for x in arr)
            and any('_index' in x for x in arr if isinstance(x, dict)))


def _merge_index_array(
    base_arr: list[object],
    mod_arr: list[object],
    schema: dict[str, object] | None,
    element_path: list[str] | None,
    allow_deletions: bool = False,
) -> list[object]:
    """应用按序号位置标记的 delta 到 base 数组。
    mod_arr 元素带 _delta(+_index)/_new_entry/_deleted(+_index) 标记。"""
    result: list[object] = copy.deepcopy(base_arr)

    delta_items, new_entry_items, deleted_items = _classify_delta_items(
        mod_arr, context="index-match 数组 "
    )

    # 按 _index 应用 delta
    for item in delta_items:
        idx = item.get('_index')
        if isinstance(idx, int) and idx < len(result):
            clean = _strip_marker(item, '_delta')
            clean.pop('_index', None)
            result[idx] = deep_merge(result[idx], clean, schema, element_path,
                                     allow_deletions=allow_deletions)

    # 追加新增
    for item in new_entry_items:
        clean = _strip_marker(item, '_new_entry')
        clean.pop('_index', None)
        result.append(copy.deepcopy(clean))

    # 删除（用 set 收集后统一过滤，避免索引偏移）
    to_remove: set[int] = set()
    for item in deleted_items:
        idx = item.get('_index')
        if isinstance(idx, int):
            to_remove.add(idx)
    if to_remove and allow_deletions:
        result = [r for i, r in enumerate(result) if i not in to_remove]

    return result


def _dup_list_delta(
    base_dl: DupList,
    mod_dl: DupList,
    schema: dict[str, object] | None = None,
    field_path: list[str] | None = None,
) -> DupListDelta | None:
    """DupList 按索引 delta。

    field_path 不变（跳过 DupList 层），元素通过 _recursive_delta 递归比较，
    按 schema 对应字段的类型和策略处理。
    """
    delta_items: list[object] = []
    min_len = min(len(base_dl), len(mod_dl))

    for i in range(min_len):
        # field_path 不变：schema 规则应用到元素（非 DupList 整体）
        elem_delta = _recursive_delta(base_dl[i], mod_dl[i], schema, field_path)
        if elem_delta is not None:
            if isinstance(elem_delta, dict):
                elem_delta['_delta'] = True
                elem_delta['_index'] = i
                delta_items.append(elem_delta)
            else:
                # 标量元素的 delta：包裹为 dict
                delta_items.append({"_delta": True, "_index": i, "_value": elem_delta})

    # mod 多出的 = 新增
    for i in range(min_len, len(mod_dl)):
        val = copy.deepcopy(mod_dl[i])
        if isinstance(val, dict):
            val['_new_entry'] = True
            delta_items.append(val)
        else:
            delta_items.append({"_new_entry": True, "_value": val})

    # base 多出的 = 始终标记删除
    for i in range(min_len, len(base_dl)):
        delta_items.append({"_deleted": True, "_index": i})

    return DupListDelta(delta_items) if delta_items else None


def _merge_dup_list(
    base_dl: DupList,
    delta_dl: DupListDelta,
    allow_deletions: bool = False,
    schema: dict[str, object] | None = None,
    field_path: list[str] | None = None,
) -> DupList:
    """应用 DupListDelta 到 base DupList。

    field_path 不变（跳过 DupList 层），dict 元素用 deep_merge 合并。
    """
    result: list[object] = list(base_dl)
    new_items: list[object] = []
    to_delete: set[int] = set()

    for item in delta_dl:
        if not isinstance(item, dict):
            continue
        if item.get("_delta"):
            idx = item["_index"]
            if isinstance(idx, int) and idx < len(result):
                if "_value" in item:
                    result[idx] = item["_value"]
                else:
                    clean = {k: v for k, v in item.items()
                             if k not in ("_delta", "_index")}
                    result[idx] = deep_merge(result[idx], clean,
                                             schema, field_path, _in_place=True,
                                             allow_deletions=allow_deletions)
        elif item.get("_new_entry"):
            if "_value" in item:
                new_items.append(item["_value"])
            else:
                clean = {k: v for k, v in item.items() if k != "_new_entry"}
                new_items.append(clean)
        elif item.get("_deleted") and allow_deletions:
            idx = item.get("_index")
            if isinstance(idx, int):
                to_delete.add(idx)

    result.extend(new_items)
    if to_delete:
        result = [v for i, v in enumerate(result) if i not in to_delete]
    return DupList(result)


def _append_array(base_arr: list[object], override_arr: list[object]) -> list[object]:
    """数组追加去重（仅用于标量数组或无标记的情况）。"""
    result: list[object] = copy.deepcopy(base_arr)

    for item in override_arr:
        if item not in result:
            result.append(copy.deepcopy(item))
    return result


def _build_warn_msg(field_path: list[str] | None, msg: str) -> str:
    """拼接合并警告消息，从 merge_ctx 读取 mod 名称和文件路径"""
    parts = []
    if merge_ctx.mod_name:
        parts.append(f"[{merge_ctx.mod_name}]")
    if merge_ctx.source_file:
        parts.append(merge_ctx.source_file)
    elif merge_ctx.rel_path:
        parts.append(merge_ctx.rel_path)
    if field_path:
        parts.append(".".join(field_path))
    prefix = " > ".join(parts)
    return f"{prefix}: {msg}" if prefix else msg


@profile
def deep_merge(
    base: object,
    override: object,
    schema: dict[str, object] | None,
    field_path: list[str] | None,
    _in_place: bool = False,
    allow_deletions: bool = False,
) -> object:
    """
    递归深度合并，由 schema 驱动合并策略。

    参数:
        base: 基础数据（当前合并状态）
        override: 覆盖数据（mod 的 delta）
        schema: schema 规则字典
        field_path: 当前字段路径（用于查找 schema 定义）
        _in_place: 内部参数，为 True 时直接修改 base 而不做 deepcopy（递归调用时使用）
        allow_deletions: 是否应用 _DELETED/_deleted 标记（由 merge_file 传入）
    """
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override if _in_place else copy.deepcopy(override)

    # 查找当前层的 schema 定义
    current_def: dict[str, object] | None = None
    if schema and field_path:
        current_def = get_field_def(schema, field_path)

    result = base if _in_place else copy.deepcopy(base)

    for key, value in override.items():
        if isinstance(value, _DeletedType):
            if allow_deletions:
                result.pop(key, None)
            continue
        child_path = field_path + [key] if field_path is not None else None
        child_def = get_field_def(schema, child_path) if schema and child_path else None

        # 确定合并策略
        merge_strategy, type_warn = _resolve_merge_strategy(child_def, result.get(key), value, key)

        if type_warn:
            diag.warn("merge", _build_warn_msg(child_path, type_warn))

        if key in result:
            result[key] = _apply_merge_strategy(
                merge_strategy, result[key], value, schema, child_path,
                _in_place=True, allow_deletions=allow_deletions,
            )
        else:
            # 新增字段：对含 delta 标记的数组仍需走 _apply_merge_strategy
            # 以正确清理 _new_entry/_delta/_deleted 标记
            if (merge_strategy in ("smart_match", "append") and isinstance(value, list)
                    or isinstance(value, DupListDelta)):
                # DupListDelta 需要 DupList 作为 base
                empty_base: DupList | list[object] = DupList() if isinstance(value, DupListDelta) else []
                result[key] = _apply_merge_strategy(
                    merge_strategy, empty_base, value, schema, child_path,
                    _in_place=False, allow_deletions=allow_deletions,
                )
            else:
                result[key] = value if _in_place else copy.deepcopy(value)

    # 未知 key 警告：检查 override 中的 key 是否在 schema 中有定义
    if current_def and isinstance(current_def, dict):
        # 收集 schema 中已知的字段名
        known_keys: set[str] = set()

        # current_def 可能是 _entry/_fields 层（直接包含字段定义），
        # 也可能是某个 object 字段的定义（通过 __fields__ 包含子字段）
        fields = current_def.get("__fields__")
        if isinstance(fields, dict):
            known_keys = set(fields.keys())
        else:
            # 检查是否是字段列表层：每个 value 都是 field_def
            meta_keys = {"__type__", "__merge__", "__fields__", "__element__", "__match_key__",
                         "__template__", "__use_template__", "__templates__"}
            field_candidates = {k for k in current_def if k not in meta_keys}
            if field_candidates and all(
                isinstance(current_def[k], dict) and (
                    "__type__" in current_def[k]  # type: ignore[operator]
                    or "__use_template__" in current_def[k]  # type: ignore[operator]
                )
                for k in field_candidates
            ):
                known_keys = field_candidates

        if known_keys:
            for key in override:
                if key not in known_keys:
                    # 全局 DSL 模式兜底：匹配 DSL pattern 的 key 不发警告
                    if classify_dsl_key(key):
                        continue
                    path_with_key = field_path + [key] if field_path is not None else None
                    msg = f"未知字段 '{key}'，schema 中未定义"
                    diag.warn("merge", _build_warn_msg(path_with_key, msg))

    return result


def _resolve_merge_strategy(
    child_def: dict[str, object] | None,
    base_val: object,
    override_val: object,
    key: str,
) -> tuple[str, str | None]:
    """确定字段的合并策略，返回 (strategy, type_warn_or_None)"""
    if child_def:
        strategy_val = child_def.get("__merge__", "replace")
        strategy = str(strategy_val) if strategy_val is not None else "replace"

        # 类型校验
        schema_type = child_def.get("__type__")
        if schema_type and override_val is not None:
            # DupListDelta：元素含 _delta/_new_entry/_deleted 标记，需要特殊提取值
            if isinstance(override_val, DupListDelta):
                from .type_utils import get_type_str
                for elem in override_val:
                    if isinstance(elem, dict) and ("_delta" in elem or "_new_entry" in elem or "_deleted" in elem):
                        val = elem.get("_value", elem)
                        if val is elem:
                            continue  # dict delta，跳过类型检查
                    else:
                        val = elem
                    if not check_type_match(
                        schema_type if isinstance(schema_type, (str, list)) else None,
                        val,
                    ):
                        actual = get_type_str(val)
                        type_warn = f"字段 '{key}' DupListDelta 元素类型不匹配: schema 期望 {schema_type}，实际为 {actual}"
                        return strategy, type_warn
                return strategy, None

            # 普通值（含 DupList）：check_type_match 已能正确处理 DupList 逐元素检查
            if not check_type_match(
                schema_type if isinstance(schema_type, (str, list)) else None,
                override_val,
            ):
                from .type_utils import get_type_str
                actual = get_type_str(override_val)
                type_warn = f"字段 '{key}' 类型不匹配: schema 期望 {schema_type}，实际为 {actual}"
                return strategy, type_warn

        return strategy, None

    # 无 schema 时的默认策略：根据数据类型推断
    if isinstance(base_val, dict) and isinstance(override_val, dict):
        return "merge", None
    return "replace", None


@profile
def _apply_merge_strategy(
    strategy: str,
    base_val: object,
    override_val: object,
    schema: dict[str, object] | None,
    child_path: list[str] | None,
    _in_place: bool = False,
    allow_deletions: bool = False,
) -> object:
    """根据合并策略执行合并"""
    # DupList 相关合并：override 是 DupListDelta，或 base 是 DupList
    if isinstance(override_val, DupListDelta) or isinstance(base_val, DupList):
        base_dl = base_val if isinstance(base_val, DupList) else DupList([base_val])
        if isinstance(override_val, DupListDelta):
            delta_dl = override_val
        elif isinstance(override_val, dict):
            delta_dl = DupListDelta([{**override_val, "_delta": True, "_index": 0}])
        else:
            delta_dl = DupListDelta([{"_delta": True, "_index": 0, "_value": override_val}])
        return _merge_dup_list(base_dl, delta_dl, allow_deletions, schema, child_path)

    if strategy == "replace":
        if (isinstance(base_val, list) and isinstance(override_val, list)
                and _has_index_markers(override_val)):
            return _merge_index_array(base_val, override_val, schema, child_path,
                                      allow_deletions=allow_deletions)
        return override_val if _in_place else copy.deepcopy(override_val)

    elif strategy == "merge":
        if isinstance(base_val, dict) and isinstance(override_val, dict):
            return deep_merge(base_val, override_val, schema, child_path,
                              _in_place=_in_place, allow_deletions=allow_deletions)
        return override_val if _in_place else copy.deepcopy(override_val)

    elif strategy == "append":
        if isinstance(base_val, list) and isinstance(override_val, list):
            if _has_index_markers(override_val):
                return _merge_index_array(base_val, override_val, schema, child_path,
                                          allow_deletions=allow_deletions)
            if _has_append_markers(override_val):
                return _merge_append_array(base_val, override_val, allow_deletions)
            return _append_array(base_val, override_val)
        return override_val if _in_place else copy.deepcopy(override_val)

    elif strategy == "smart_match":
        if isinstance(base_val, list) and isinstance(override_val, list):
            return _merge_settlement_array(base_val, override_val, schema, child_path,
                                           allow_deletions=allow_deletions)
        return override_val if _in_place else copy.deepcopy(override_val)

    elif strategy == "coerce":
        return _coerce_and_merge_array(base_val, override_val)

    else:
        return override_val if _in_place else copy.deepcopy(override_val)


@profile
def compute_mod_delta(
    base_data: dict[str, object],
    mod_data: dict[str, object],
    file_type: str,
    schema: dict[str, object] | None = None,
    root_key: str | None = None,
) -> dict[str, object]:
    """
    计算 mod 相对于游戏本体的实际差异。

    只提取 mod 真正修改的部分，忽略与本体完全相同的内容。
    对 dictionary 类型文件按条目级 + 字段级递归 diff，
    对 entity/config 类型文件按字段级递归 diff。
    始终产出完整差异（含 _DELETED），是否应用由 merge_file 的 allow_deletions 决定。

    schema 和 root_key 用于让 _recursive_delta 识别 smart_match 数组的 match_key，
    避免自动检测失败时产出无标记的 delta。
    """
    field_path = [root_key] if root_key else None

    if not base_data:
        # 本体无此文件，全部是新增
        # 仍需走 _recursive_delta 以便 smart_match 数组产出带标记的 delta
        result = _recursive_delta({}, mod_data, schema, field_path)
        return cast(dict[str, object], result) if result is not None else mod_data

    if file_type == "dictionary":
        delta: dict[str, object] = {}
        for key, mod_val in mod_data.items():
            if key not in base_data:
                delta[key] = mod_val  # 新增条目
            else:
                sub = _recursive_delta(base_data[key], mod_val, schema, field_path)
                if sub is not None:
                    delta[key] = sub  # 有变化的条目（只含变化字段）
        # 始终标记删除
        for key in base_data:
            if key not in mod_data:
                delta[key] = _DELETED
        return delta
    else:
        # entity/config：递归提取变化字段
        result = _recursive_delta(base_data, mod_data, schema, field_path)
        return cast(dict[str, object], result) if result is not None else {}


@profile
def _object_array_delta(
    base_arr: list[dict[str, object]],
    mod_arr: list[dict[str, object]],
    match_keys: list[str],
    schema: dict[str, object] | None = None,
    field_path: list[str] | None = None,
) -> list[dict[str, object]] | None:
    """
    对象数组的元素级 delta（按 match_keys 匹配）。
    每个 delta 元素只含变化字段 + match_keys。
    同 key 多对多时用相似度做全局最优配对。
    始终产出 _deleted 标记（是否应用由合并层决定）。
    返回 None 表示无变化。
    """
    # base 按 key 分组（支持重复 key）
    base_groups: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for item in base_arr:
        kv = get_key_vals(item, match_keys)
        if kv is not None:
            base_groups.setdefault(kv, []).append(item)

    # mod 按 key 分组
    mod_groups: dict[tuple[object, ...], list[dict[str, object]]] = {}
    mod_no_key: list[dict[str, object]] = []
    for item in mod_arr:
        kv = get_key_vals(item, match_keys)
        if kv is not None:
            mod_groups.setdefault(kv, []).append(item)
        else:
            mod_no_key.append(item)

    delta_items: list[dict[str, object]] = []
    seen_keys: set[tuple[object, ...]] = set()

    for kv, mod_items in mod_groups.items():
        seen_keys.add(kv)
        base_items = base_groups.get(kv, [])

        if not base_items:
            # 全部新增
            for mod_item in mod_items:
                new_item = copy.deepcopy(mod_item)
                new_item['_new_entry'] = True
                delta_items.append(new_item)
            continue

        # 全局最优配对
        mod_indexed: list[tuple[int, dict[str, object]]] = list(enumerate(mod_items))
        base_indices = list(range(len(base_items)))
        pairs, unmatched = resolve_duplicates(
            mod_indexed, cast(list[object], base_items), base_indices
        )

        for _, mod_item, base_idx in pairs:
            elem_delta = _recursive_delta(base_items[base_idx], mod_item,
                                          schema, field_path)
            if elem_delta is not None and isinstance(elem_delta, dict):
                for k, v in zip(match_keys, kv, strict=True):
                    elem_delta[k] = v
                elem_delta['_delta'] = True
                delta_items.append(elem_delta)

        for _, mod_item in unmatched:
            new_item = copy.deepcopy(mod_item)
            new_item['_new_entry'] = True
            delta_items.append(new_item)

    # 无 key 的 mod 元素直接作为新增
    for item in mod_no_key:
        new_item = copy.deepcopy(item)
        new_item['_new_entry'] = True
        delta_items.append(new_item)

    # 始终标记删除的元素
    for kv, base_items in base_groups.items():
        if kv not in seen_keys:
            for _ in base_items:
                deleted_entry: dict[str, object] = dict(zip(match_keys, kv, strict=True))
                deleted_entry['_deleted'] = True
                delta_items.append(deleted_entry)

    return delta_items if delta_items else None


def _index_array_delta(
    base_arr: list[object],
    mod_arr: list[object],
    schema: dict[str, object] | None = None,
    field_path: list[str] | None = None,
) -> list[dict[str, object]] | None:
    """按序号位置对应的对象数组 delta。
    每个元素带 _delta/_new_entry/_deleted 标记，_delta 元素带 _index 标记位置。
    始终产出 _deleted 标记（是否应用由合并层决定）。"""
    delta_items: list[dict[str, object]] = []
    min_len = min(len(base_arr), len(mod_arr))

    for i in range(min_len):
        elem_delta = _recursive_delta(base_arr[i], mod_arr[i], schema, field_path)
        if elem_delta is not None and isinstance(elem_delta, dict):
            elem_delta['_delta'] = True
            elem_delta['_index'] = i
            delta_items.append(elem_delta)

    # mod 多出的 = 新增（带 _index 标记来源，确保 _has_index_markers 可识别）
    for i in range(min_len, len(mod_arr)):
        item = mod_arr[i]
        new_item: dict[str, object] = copy.deepcopy(item) if isinstance(item, dict) else {"_value": item}
        new_item['_new_entry'] = True
        new_item['_index'] = i
        delta_items.append(new_item)

    # base 多出的 = 始终标记删除
    for i in range(min_len, len(base_arr)):
        delta_items.append({'_deleted': True, '_index': i})

    return delta_items if delta_items else None


def _append_array_delta(
    base_arr: list[object],
    mod_arr: list[object],
) -> list[dict[str, object]] | None:
    """消耗式匹配，始终产出 _new_entry/_deleted 标记（是否应用由合并层决定）。"""
    delta: list[dict[str, object]] = []
    remaining_base = list(base_arr)
    for item in mod_arr:
        if item in remaining_base:
            remaining_base.remove(item)
        else:
            delta.append({"_new_entry": True, "_value": item})
    for item in remaining_base:
        delta.append({"_deleted": True, "_value": item})
    return delta if delta else None


def _has_append_markers(arr: list[object]) -> bool:
    """检测数组是否来自 _append_array_delta（含 _new_entry/_deleted + _value 标记）。"""
    return (bool(arr)
            and all(isinstance(x, dict) for x in arr)
            and all(("_new_entry" in x or "_deleted" in x) and "_value" in x
                    for x in arr if isinstance(x, dict)))


def _merge_append_array(
    base_arr: list[object],
    delta_arr: list[object],
    allow_deletions: bool = False,
) -> list[object]:
    """按 _new_entry/_deleted 标记执行追加/删除。"""
    result: list[object] = copy.deepcopy(base_arr)
    for item in delta_arr:
        if not isinstance(item, dict):
            continue
        if item.get("_new_entry"):
            val = item["_value"]
            if val not in result:
                result.append(copy.deepcopy(val))
        elif item.get("_deleted") and allow_deletions:
            val = item["_value"]
            if val in result:
                result.remove(val)
    return result


def _recursive_delta(
    base: object,
    mod: object,
    schema: dict[str, object] | None = None,
    field_path: list[str] | None = None,
) -> object | None:
    """递归比较，返回 mod 相对于 base 的变化部分。None 表示无差异。
    始终产出完整差异（含 _DELETED/_deleted 标记），是否应用由合并层决定。"""
    # 早期相等退出：跳过完全相同的子树，避免大量递归
    if base == mod:
        return None
    if isinstance(base, dict) and isinstance(mod, dict):
        delta: dict[str, object] = {}
        for key, mod_val in mod.items():
            child_path = field_path + [key] if field_path is not None else None
            if key not in base:
                # 新增字段：如果是数组仍需走 delta 标记逻辑
                if isinstance(mod_val, list) and schema and child_path:
                    empty_base: DupList | list[object] = DupList() if isinstance(mod_val, DupList) else []
                    sub = _recursive_delta(empty_base, mod_val, schema, child_path)
                    delta[key] = sub if sub is not None else copy.deepcopy(mod_val)
                else:
                    delta[key] = copy.deepcopy(mod_val)
            else:
                sub = _recursive_delta(base[key], mod_val, schema, child_path)
                if sub is not None:
                    delta[key] = sub
        # 始终标记删除
        for key in base:
            if key not in mod:
                delta[key] = _DELETED
        return delta if delta else None

    # DupList：统一为 DupList 后按索引递归比较（field_path 不变，跳过 DupList 层）
    if isinstance(base, DupList) or isinstance(mod, DupList):
        base_dl = base if isinstance(base, DupList) else DupList([base])
        mod_dl = mod if isinstance(mod, DupList) else DupList([mod])
        return _dup_list_delta(base_dl, mod_dl, schema, field_path)

    if isinstance(base, list) and isinstance(mod, list):
        # 从 schema 查询数组的合并策略和 match_keys
        schema_match_keys: list[str] | None = None
        merge_strategy: str | None = None
        if schema and field_path:
            field_def = get_field_def(schema, field_path)
            if field_def:
                ms = field_def.get("__merge__")
                merge_strategy = str(ms) if ms is not None else None
                if merge_strategy == "smart_match":
                    mk = field_def.get("__match_key__")
                    schema_match_keys = mk if isinstance(mk, list) else None

        # 对象数组：按 key 匹配做元素级 delta
        if is_obj_array(mod) and (not base or is_obj_array(base)):
            if schema_match_keys:
                return _object_array_delta(
                    cast(list[dict[str, object]], base),
                    cast(list[dict[str, object]], mod),
                    schema_match_keys, schema, field_path,
                )
            # 无 match_key 的对象数组：按序号位置匹配
            return _index_array_delta(base, mod, schema, field_path)

        # 非对象数组：检查 append 策略
        if merge_strategy == "append":
            return _append_array_delta(base, mod)

        # 其他非对象数组：原子比较
        if base == mod:
            return None
        return copy.deepcopy(mod)

    # TODO: 不该兼容，增添、删除和改变字段是什么就返回什么
    # 标量与数组兼容：mod 作者可能将 [value, extra] 简化为 value
    if isinstance(base, list) and not isinstance(mod, (list, dict)):
        if not isinstance(base, DupList) and mod in base:
            return None  # 标量已在数组中，视为无变化

    # 标量比较
    if base == mod:
        return None
    return copy.deepcopy(mod)


@profile
def merge_file(
    base_data: dict[str, object],
    mod_data_list: list[tuple[str, str, dict[str, object], str]],
    rel_path: str = "",
    schema: dict[str, object] | None = None,
    overrides_dir: Path | None = None,
    allow_deletions: bool = False,
) -> MergeResult:
    """
    合并单个文件。

    参数:
        base_data: 游戏本体的 JSON 数据
        mod_data_list: [(mod_id, mod_name, mod_json_data, source_file), ...] 按优先级排序
        rel_path: 文件相对路径（用于判断特殊文件）
        schema: 该文件对应的 schema 规则
        overrides_dir: 用户 override 文件目录，存在时检查并加载用户编辑的合并结果
        allow_deletions: 是否应用 _DELETED/_deleted 标记（删减 mod 删除的内容）
    """
    result = MergeResult()
    file_name = Path(rel_path).name if rel_path else ""

    # sfx_config.json 等特殊文件：整文件替换
    if file_name in WHOLE_FILE_REPLACE:
        if mod_data_list:
            _, last_mod_name, last_mod_data, _ = mod_data_list[-1]
            result.merged_data = copy.deepcopy(last_mod_data)
            if len(mod_data_list) > 1:
                diag.warn("merge", f"{rel_path}: 多个 mod 修改此文件（整文件替换模式），最终使用 {last_mod_name}")
        else:
            result.merged_data = copy.deepcopy(base_data)
        return result

    current: dict[str, object] = copy.deepcopy(base_data)

    # 从 schema 或数据结构确定文件类型
    if schema:
        meta = schema.get("_meta", {})
        file_type_val = meta.get("file_type", None) if isinstance(meta, dict) else None
        file_type = str(file_type_val) if file_type_val is not None else classify_json(base_data)
    else:
        file_type = classify_json(base_data)

    # 确定 schema 根 key
    root_key = get_schema_root_key(schema) if schema else None

    for mod_id, mod_name, mod_data, source_file in mod_data_list:
        #TODO： 写法不太好
        # 设置线程本地上下文，供 deep_merge 内部的警告使用
        merge_ctx.mod_name = mod_name
        merge_ctx.mod_id = mod_id
        merge_ctx.rel_path = rel_path
        merge_ctx.source_file = source_file

        if file_type == "dictionary":
            for key, value in mod_data.items():
                if isinstance(value, _DeletedType):
                    if allow_deletions:
                        current.pop(key, None)
                    continue
                if key in current:
                    fp: list[str] | None = [root_key] if root_key else None
                    current[key] = deep_merge(current[key], value, schema, fp,
                                              _in_place=True, allow_deletions=allow_deletions)
                else:
                    current[key] = copy.deepcopy(value)
                    result.new_entries.append(("", mod_name, f"新增 key: {key}"))
        else:
            # 实体型和配置型
            field_path: list[str] | None = [root_key] if root_key else None
            current = cast(
                dict[str, object],
                deep_merge(current, mod_data, schema, field_path,
                           _in_place=True, allow_deletions=allow_deletions)
            )

        # 检查用户 override：如果存在则用 override 替换累积状态
        if overrides_dir:
            override_file = overrides_dir / mod_id / rel_path
            if override_file.exists():
                current = json.loads(override_file.read_text(encoding="utf-8"),
                                     object_pairs_hook=_pairs_hook)

    result.merged_data = current
    return result


@profile
def merge_all_files(
    game_config_path: Path,
    mod_configs: list[tuple[str, str, Path]],
    output_path: Path,
    schema_dir: Path | None = None,
    allow_deletions: bool = False,
    cancel_check: CancelCheck | None = None,
    overrides_dir: Path | None = None,
) -> tuple[dict[str, MergeResult], list[ParseFailure]]:
    """
    合并所有文件。

    参数:
        game_config_path: 游戏本体 config 目录
        mod_configs: [(mod_id, mod_name, mod_config_path), ...] 按优先级排序
        output_path: 输出目录
        schema_dir: schema 规则文件目录
        allow_deletions: 是否允许删减（mod 中缺少的条目从结果中删除）
        cancel_check: 可选的取消检查回调，调用时若已取消则抛出异常
        overrides_dir: 用户 override 文件目录，存在时传递给 merge_file

    返回:
        (合并结果字典, 解析失败列表)
    """
    diag.snapshot("merge")  # 清空上次的合并警告

    # 加载 schemas
    schemas = load_schemas(schema_dir) if schema_dir else {}

    all_files = collect_mod_files(mod_configs)

    results: dict[str, MergeResult] = {}
    parse_failures: list[ParseFailure] = []
    for rel_path, mod_file_list in all_files.items():
        if cancel_check:
            cancel_check()
        # 加载游戏本体文件
        base_file = game_config_path / rel_path
        if base_file.exists():
            try:
                base_data = load_json(base_file, readonly=True)
            except json.JSONDecodeError as e:
                msg = f"{base_file}: JSON 解析失败 ({e.msg})"
                diag.error("merge", msg)
                parse_failures.append(ParseFailure(
                    file_path=base_file, rel_path=rel_path,
                    error_msg=e.msg, error_line=getattr(e, 'lineno', 0) or 0,
                    is_base=True, mod_id="", mod_name="",
                ))
                continue
        else:
            base_data = {}

        # 确定文件类型（用于 delta 计算）
        file_type = classify_json(base_data) if base_data else "config"

        # 查找 schema（delta 计算需要 schema 的 match_key 信息）
        schema = resolve_schema(rel_path, schemas) if schemas else None
        root_key = get_schema_root_key(schema) if schema else None

        # 加载各 mod 的数据，计算 delta（只保留实际修改的部分）
        mod_data_list: list[tuple[str, str, dict[str, object], str]] = []
        for mod_id, mod_name, mod_file in mod_file_list:
            try:
                mod_data = load_json(mod_file, readonly=True)
            except json.JSONDecodeError as e:
                msg = f"{mod_file}: JSON 解析失败 ({e.msg})"
                diag.error("merge", msg)
                parse_failures.append(ParseFailure(
                    file_path=mod_file, rel_path=rel_path,
                    error_msg=e.msg, error_line=getattr(e, 'lineno', 0) or 0,
                    is_base=False, mod_id=mod_id, mod_name=mod_name,
                ))
                continue

            # tag.json name 匹配验证（需在 delta 计算前用原始数据验证）
            if rel_path == "tag.json" and base_data:
                _validate_tag_names(base_data, [(mod_id, mod_name, mod_data)])

            delta = compute_mod_delta(base_data, mod_data, file_type,
                                      schema=schema, root_key=root_key)
            if delta:
                mod_data_list.append((mod_id, mod_name, delta, str(mod_file)))

        if not mod_data_list:
            continue

        # 合并
        merge_result = merge_file(base_data, mod_data_list, rel_path,
                                   schema=schema, overrides_dir=overrides_dir,
                                   allow_deletions=allow_deletions)
        results[rel_path] = merge_result

        # TODO： 根本没必要每次都写一遍，只用最后才写
        # 输出
        out_file = output_path / rel_path
        dump_json(merge_result.merged_data, out_file)

    return results, parse_failures


def raw_copy_file(source: Path, rel_path: str, output_path: Path) -> None:
    """将文件原样复制到输出目录"""
    dest = output_path / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)


def _validate_tag_names(
    base_data: dict[str, object],
    mod_data_list: list[tuple[str, str, dict[str, object]]],
) -> None:
    """验证 tag.json 中覆盖的 tag 的 name 是否与原 tag 一致"""
    for _, mod_name, mod_data in mod_data_list:
        for key, value in mod_data.items():
            base_val = base_data.get(key)
            if base_val is not None and isinstance(value, dict) and isinstance(base_val, dict):
                base_name = base_val.get('name', '')
                mod_tag_name = value.get('name', '')
                if mod_tag_name and base_name and mod_tag_name != base_name:
                    msg = (f"tag.json: Mod [{mod_name}] 的 tag [{key}] "
                           f"name=\"{mod_tag_name}\" 与本体 name=\"{base_name}\" 不一致，可能导致游戏出错")
                    diag.warn("merge", msg)
