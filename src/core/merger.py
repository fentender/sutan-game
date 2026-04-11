"""
核心合并算法 - 基于 schema 规则的字典合并、实体合并、数组智能匹配
"""
import json
import logging
import copy
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .array_match import find_matching_item, resolve_duplicates, get_key_vals
from .dsl_patterns import classify_dsl_key
from .profiler import profile
from .json_parser import load_json, dump_json, DupList, _pairs_hook
from .schema_loader import (
    load_schemas, resolve_schema, get_field_def,
    get_schema_root_key, check_type_match,
)
from .type_utils import classify_json
from .mod_scanner import collect_mod_files
from .diagnostics import diag, merge_ctx

log = logging.getLogger(__name__)

# 需要整文件替换而非合并的文件
WHOLE_FILE_REPLACE = {'sfx_config.json'}

# 删除标记（用于 allow_deletions 模式）
_DELETED = object()


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
    merged_data: dict = field(default_factory=dict)
    overrides: list[OverrideRecord] = field(default_factory=list)
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

def _strip_marker(item: dict, marker: str) -> dict:
    """移除 delta 标记字段，返回清理后的副本"""
    return {k: v for k, v in item.items() if k != marker}


def _classify_delta_items(
    mod_arr: list, context: str = ""
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    按 _delta / _new_entry / _deleted 标记分类 delta 数组元素。
    返回 (delta_items, new_entry_items, deleted_items)。
    """
    delta_items: list[dict] = []
    new_entry_items: list[dict] = []
    deleted_items: list[dict] = []

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
    result: list, delta_items: list[dict], match_keys: list[str],
    matched: set[int], schema: dict | None, element_path: list[str] | None,
) -> None:
    """将 delta 元素按 match_key 匹配到 result 并 deep_merge，原地修改 result。"""
    # 建立 result 索引
    result_index: dict[tuple, list[int]] = {}
    for ri, r_item in enumerate(result):
        if isinstance(r_item, dict):
            kv = get_key_vals(r_item, match_keys)
            if kv is not None:
                result_index.setdefault(kv, []).append(ri)

    # 按 key 分组 delta 元素
    delta_groups: dict[tuple | None, list[tuple[int, dict]]] = {}
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
def _merge_settlement_array(base_arr: list, mod_arr: list,
                            schema: dict | None,
                            element_path: list[str] | None) -> list:
    """
    智能合并 array<object> 数组。
    mod_arr 必须是经过 _object_array_delta 产出的 delta 数组，
    每个元素必须带有 _delta / _new_entry / _deleted 标记。
    """
    result = copy.deepcopy(base_arr)

    # 从 schema 读取 match_key
    match_keys = None
    if schema and element_path:
        field_def = get_field_def(schema, element_path)
        if field_def:
            match_keys = field_def.get("__match_key__")

    if not match_keys:
        raise ValueError(
            f"smart_match 数组缺少 match_key 定义 (path: {element_path})"
        )

    delta_items, new_entry_items, deleted_items = _classify_delta_items(
        mod_arr, context=f"smart_match 数组 (match_keys={match_keys}) "
    )

    # 处理删除
    matched = set()
    to_remove = set()
    for mod_item in deleted_items:
        idx = find_matching_item(result, mod_item, matched, match_keys)
        if idx is not None:
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


def _coerce_and_merge_array(base_val, override_val):
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


def find_array_match_key(arr: list) -> str | None:
    """在对象数组中找到可用于匹配的唯一标识字段"""
    if not arr or not all(isinstance(x, dict) for x in arr):
        return None
    for key in COMMON_MATCH_KEYS:
        values = []
        for item in arr:
            if key not in item:
                break
            values.append(item[key])
        else:
            if len(set((type(v), v) for v in values)) == len(values):
                return key
    return None


def _is_object_array(arr: list) -> bool:
    """判断是否是对象数组"""
    return bool(arr) and all(isinstance(x, dict) for x in arr)


def _has_index_markers(arr: list) -> bool:
    """检测数组是否来自 _index_array_delta（含 _index 标记的 delta）"""
    return (bool(arr)
            and all(isinstance(x, dict) for x in arr)
            and any('_index' in x for x in arr))


def _merge_index_array(base_arr: list, mod_arr: list,
                       schema: dict | None,
                       element_path: list[str] | None) -> list:
    """应用按序号位置标记的 delta 到 base 数组。
    mod_arr 元素带 _delta(+_index)/_new_entry/_deleted(+_index) 标记。"""
    result = copy.deepcopy(base_arr)

    delta_items, new_entry_items, deleted_items = _classify_delta_items(
        mod_arr, context="index-match 数组 "
    )

    # 按 _index 应用 delta
    for item in delta_items:
        idx = item.get('_index')
        if idx is not None and idx < len(result):
            clean = _strip_marker(item, '_delta')
            clean.pop('_index', None)
            result[idx] = deep_merge(result[idx], clean, schema, element_path)

    # 追加新增
    for item in new_entry_items:
        result.append(copy.deepcopy(_strip_marker(item, '_new_entry')))

    # 删除（用 set 收集后统一过滤，避免索引偏移）
    to_remove = set()
    for item in deleted_items:
        idx = item.get('_index')
        if idx is not None:
            to_remove.add(idx)
    if to_remove:
        result = [r for i, r in enumerate(result) if i not in to_remove]

    return result


def _dup_list_delta(base_dl, mod_dl, allow_deletions=False,
                    schema=None, field_path=None):
    """DupList 按索引 delta。

    field_path 不变（跳过 DupList 层），元素通过 _recursive_delta 递归比较，
    按 schema 对应字段的类型和策略处理。
    """
    delta_items = []
    min_len = min(len(base_dl), len(mod_dl))

    for i in range(min_len):
        # field_path 不变：schema 规则应用到元素（非 DupList 整体）
        elem_delta = _recursive_delta(base_dl[i], mod_dl[i], allow_deletions,
                                       schema, field_path)
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

    # base 多出的 = 删除
    if allow_deletions:
        for i in range(min_len, len(base_dl)):
            delta_items.append({"_deleted": True, "_index": i})

    return DupListDelta(delta_items) if delta_items else None


def _merge_dup_list(base_dl, delta_dl, schema=None, field_path=None):
    """应用 DupListDelta 到 base DupList。

    field_path 不变（跳过 DupList 层），dict 元素用 deep_merge 合并。
    """
    result = list(base_dl)
    new_items = []
    to_delete = set()

    for item in delta_dl:
        if item.get("_delta"):
            idx = item["_index"]
            if idx < len(result):
                if "_value" in item:
                    result[idx] = item["_value"]
                else:
                    clean = {k: v for k, v in item.items()
                             if k not in ("_delta", "_index")}
                    result[idx] = deep_merge(result[idx], clean,
                                             schema, field_path, _in_place=True)
        elif item.get("_new_entry"):
            if "_value" in item:
                new_items.append(item["_value"])
            else:
                clean = {k: v for k, v in item.items() if k != "_new_entry"}
                new_items.append(clean)
        elif item.get("_deleted"):
            to_delete.add(item["_index"])

    result.extend(new_items)
    if to_delete:
        result = [v for i, v in enumerate(result) if i not in to_delete]
    return DupList(result)


def _append_array(base_arr: list, override_arr: list,
                  schema: dict | None = None,
                  child_path: list[str] | None = None) -> list:
    """数组追加去重。对象数组按标记驱动合并。
    override_arr 中的对象元素必须带 _delta/_new_entry/_deleted 标记。"""
    result = copy.deepcopy(base_arr)

    # 对象数组：按 key 字段匹配并合并
    if (base_arr and override_arr
            and all(isinstance(x, dict) for x in base_arr)
            and all(isinstance(x, dict) for x in override_arr)):
        match_key = find_array_match_key(base_arr)
        if match_key:
            key_index: dict = {}
            for i, item in enumerate(result):
                kv = item.get(match_key)
                if kv is not None:
                    key_index[kv] = i

            delta_items, new_entry_items, deleted_items = _classify_delta_items(
                override_arr, context=f"append 对象数组 (match_key={match_key}) "
            )

            to_remove = set()
            for item in deleted_items:
                kv = item.get(match_key)
                idx = key_index.get(kv) if kv is not None else None
                if idx is not None:
                    to_remove.add(idx)

            for item in new_entry_items:
                result.append(copy.deepcopy(_strip_marker(item, '_new_entry')))

            for item in delta_items:
                clean = _strip_marker(item, '_delta')
                kv = item.get(match_key)
                idx = key_index.get(kv) if kv is not None else None
                if idx is not None:
                    result[idx] = deep_merge(result[idx], clean, schema, child_path)
                else:
                    result.append(clean)

            if to_remove:
                result = [r for i, r in enumerate(result) if i not in to_remove]
            return result

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
def deep_merge(base: object, override: object,
               schema: dict | None,
               field_path: list[str] | None,
               _in_place: bool = False) -> object:
    """
    递归深度合并，由 schema 驱动合并策略。

    参数:
        base: 基础数据（当前合并状态）
        override: 覆盖数据（mod 的 delta）
        schema: schema 规则字典
        field_path: 当前字段路径（用于查找 schema 定义）
        _in_place: 内部参数，为 True 时直接修改 base 而不做 deepcopy（递归调用时使用）
    """
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override if _in_place else copy.deepcopy(override)

    # 查找当前层的 schema 定义
    current_def = None
    if schema and field_path:
        current_def = get_field_def(schema, field_path)

    result = base if _in_place else copy.deepcopy(base)

    for key, value in override.items():
        if value is _DELETED:
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
                _in_place=True,
            )
        else:
            # 新增字段：对含 delta 标记的数组仍需走 _apply_merge_strategy
            # 以正确清理 _new_entry/_delta/_deleted 标记
            if merge_strategy in ("smart_match", "append") and isinstance(value, list):
                result[key] = _apply_merge_strategy(
                    merge_strategy, [], value, schema, child_path,
                    _in_place=False,
                )
            else:
                result[key] = value if _in_place else copy.deepcopy(value)

    # 未知 key 警告：检查 override 中的 key 是否在 schema 中有定义
    if current_def and isinstance(current_def, dict):
        # 收集 schema 中已知的字段名
        known_keys = set()

        # current_def 可能是 _entry/_fields 层（直接包含字段定义），
        # 也可能是某个 object 字段的定义（通过 __fields__ 包含子字段）
        if "__fields__" in current_def:
            known_keys = set(current_def["__fields__"].keys())
        else:
            # 检查是否是字段列表层：每个 value 都是 field_def
            meta_keys = {"__type__", "__merge__", "__fields__", "__element__", "__match_key__",
                         "__template__", "__use_template__", "__templates__"}
            field_candidates = {k for k in current_def if k not in meta_keys}
            if field_candidates and all(
                isinstance(current_def[k], dict) and ("__type__" in current_def[k] or "__use_template__" in current_def[k])
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


def _resolve_merge_strategy(child_def: dict | None, base_val, override_val, key: str) -> tuple[str, str | None]:
    """确定字段的合并策略，返回 (strategy, type_warn_or_None)"""
    if child_def:
        strategy = child_def.get("__merge__", "replace")

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
                    if not check_type_match(schema_type, val):
                        actual = get_type_str(val)
                        type_warn = f"字段 '{key}' DupListDelta 元素类型不匹配: schema 期望 {schema_type}，实际为 {actual}"
                        return strategy, type_warn
                return strategy, None

            # 普通值（含 DupList）：check_type_match 已能正确处理 DupList 逐元素检查
            if not check_type_match(schema_type, override_val):
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
def _apply_merge_strategy(strategy: str, base_val, override_val,
                          schema: dict | None, child_path: list[str] | None,
                          _in_place: bool = False) -> object:
    """根据合并策略执行合并"""
    # DupListDelta：元素级合并，field_path 不变（跳过 DupList 层）
    if isinstance(override_val, DupListDelta):
        base_dl = base_val if isinstance(base_val, DupList) else DupList([base_val])
        return _merge_dup_list(base_dl, override_val, schema, child_path)

    if strategy == "replace":
        if (isinstance(base_val, list) and isinstance(override_val, list)
                and _has_index_markers(override_val)):
            return _merge_index_array(base_val, override_val, schema, child_path)
        return override_val if _in_place else copy.deepcopy(override_val)

    elif strategy == "merge":
        if isinstance(base_val, dict) and isinstance(override_val, dict):
            return deep_merge(base_val, override_val, schema, child_path,
                              _in_place=_in_place)
        return override_val if _in_place else copy.deepcopy(override_val)

    elif strategy == "append":
        if isinstance(base_val, list) and isinstance(override_val, list):
            if _has_index_markers(override_val):
                return _merge_index_array(base_val, override_val, schema, child_path)
            return _append_array(base_val, override_val, schema, child_path)
        return override_val if _in_place else copy.deepcopy(override_val)

    elif strategy == "smart_match":
        if isinstance(base_val, list) and isinstance(override_val, list):
            return _merge_settlement_array(base_val, override_val, schema, child_path)
        return override_val if _in_place else copy.deepcopy(override_val)

    elif strategy == "coerce":
        return _coerce_and_merge_array(base_val, override_val)

    else:
        return override_val if _in_place else copy.deepcopy(override_val)


@profile
def compute_mod_delta(base_data: dict, mod_data: dict,
                      file_type: str, allow_deletions: bool = False,
                      schema: dict | None = None,
                      root_key: str | None = None) -> dict:
    """
    计算 mod 相对于游戏本体的实际差异。

    只提取 mod 真正修改的部分，忽略与本体完全相同的内容。
    对 dictionary 类型文件按条目级 + 字段级递归 diff，
    对 entity/config 类型文件按字段级递归 diff。

    schema 和 root_key 用于让 _recursive_delta 识别 smart_match 数组的 match_key，
    避免自动检测失败时产出无标记的 delta。
    """
    field_path = [root_key] if root_key else None

    if not base_data:
        # 本体无此文件，全部是新增
        # 仍需走 _recursive_delta 以便 smart_match 数组产出带标记的 delta
        result = _recursive_delta({}, mod_data, allow_deletions, schema, field_path)
        return result if result is not None else mod_data

    if file_type == "dictionary":
        delta = {}
        for key, mod_val in mod_data.items():
            if key not in base_data:
                delta[key] = mod_val  # 新增条目
            else:
                sub = _recursive_delta(base_data[key], mod_val, allow_deletions,
                                       schema, field_path)
                if sub is not None:
                    delta[key] = sub  # 有变化的条目（只含变化字段）
        if allow_deletions:
            for key in base_data:
                if key not in mod_data:
                    delta[key] = _DELETED
        return delta
    else:
        # entity/config：递归提取变化字段
        result = _recursive_delta(base_data, mod_data, allow_deletions,
                                  schema, field_path)
        return result if result is not None else {}


@profile
def _object_array_delta(base_arr: list[dict], mod_arr: list[dict],
                        match_key: str, allow_deletions: bool = False,
                        schema=None, field_path=None) -> list[dict] | None:
    """
    对象数组的元素级 delta（按 match_key 匹配）。
    每个 delta 元素只含变化字段 + match_key。
    同 key 多对多时用相似度做全局最优配对。
    返回 None 表示无变化。
    """
    # base 按 key 分组（支持重复 key）
    base_groups: dict = {}
    for item in base_arr:
        kv = item.get(match_key)
        if kv is not None:
            base_groups.setdefault(kv, []).append(item)

    # mod 按 key 分组
    mod_groups: dict = {}
    mod_no_key: list[dict] = []
    for item in mod_arr:
        kv = item.get(match_key)
        if kv is not None:
            mod_groups.setdefault(kv, []).append(item)
        else:
            mod_no_key.append(item)

    delta_items: list[dict] = []
    seen_keys: set = set()

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
        mod_indexed = list(enumerate(mod_items))
        base_indices = list(range(len(base_items)))
        pairs, unmatched = resolve_duplicates(
            mod_indexed, base_items, base_indices
        )

        for _, mod_item, base_idx in pairs:
            elem_delta = _recursive_delta(base_items[base_idx], mod_item, allow_deletions,
                                                schema, field_path)
            if elem_delta is not None:
                elem_delta[match_key] = kv
                elem_delta['_delta'] = True
                delta_items.append(elem_delta)

        for _, mod_item in unmatched:
            # mod 剩余的作为新增，标记 _new_entry
            new_item = copy.deepcopy(mod_item)
            new_item['_new_entry'] = True
            delta_items.append(new_item)

    # 无 key 的 mod 元素直接作为新增
    for item in mod_no_key:
        new_item = copy.deepcopy(item)
        new_item['_new_entry'] = True
        delta_items.append(new_item)

    # 标记删除的元素
    if allow_deletions:
        for kv, base_items in base_groups.items():
            if kv not in seen_keys:
                for _ in base_items:
                    delta_items.append({match_key: kv, '_deleted': True})

    return delta_items if delta_items else None


def _index_array_delta(base_arr, mod_arr, allow_deletions=False,
                       schema=None, field_path=None):
    """按序号位置对应的对象数组 delta。
    每个元素带 _delta/_new_entry/_deleted 标记，_delta 元素带 _index 标记位置。"""
    delta_items = []
    min_len = min(len(base_arr), len(mod_arr))

    for i in range(min_len):
        elem_delta = _recursive_delta(base_arr[i], mod_arr[i], allow_deletions,
                                       schema, field_path)
        if elem_delta is not None:
            elem_delta['_delta'] = True
            elem_delta['_index'] = i
            delta_items.append(elem_delta)

    # mod 多出的 = 新增
    for i in range(min_len, len(mod_arr)):
        new_item = copy.deepcopy(mod_arr[i])
        new_item['_new_entry'] = True
        delta_items.append(new_item)

    # base 多出的 = 删除
    if allow_deletions:
        for i in range(min_len, len(base_arr)):
            delta_items.append({'_deleted': True, '_index': i})

    return delta_items if delta_items else None


def _recursive_delta(base, mod, allow_deletions=False,
                     schema=None, field_path=None):
    """递归比较，返回 mod 相对于 base 的变化部分。None 表示无差异。"""
    if isinstance(base, dict) and isinstance(mod, dict):
        delta = {}
        for key, mod_val in mod.items():
            child_path = field_path + [key] if field_path is not None else None
            if key not in base:
                # 新增字段：如果是数组仍需走 delta 标记逻辑
                if isinstance(mod_val, list) and schema and child_path:
                    sub = _recursive_delta([], mod_val, allow_deletions,
                                           schema, child_path)
                    delta[key] = sub if sub is not None else copy.deepcopy(mod_val)
                else:
                    delta[key] = copy.deepcopy(mod_val)
            else:
                sub = _recursive_delta(base[key], mod_val, allow_deletions,
                                       schema, child_path)
                if sub is not None:
                    delta[key] = sub
        if allow_deletions:
            for key in base:
                if key not in mod:
                    delta[key] = _DELETED
        return delta if delta else None

    # DupList：统一为 DupList 后按索引递归比较（field_path 不变，跳过 DupList 层）
    if isinstance(base, DupList) or isinstance(mod, DupList):
        base_dl = base if isinstance(base, DupList) else DupList([base])
        mod_dl = mod if isinstance(mod, DupList) else DupList([mod])
        return _dup_list_delta(base_dl, mod_dl, allow_deletions, schema, field_path)

    if isinstance(base, list) and isinstance(mod, list):
        # 从 schema 查询 smart_match 的 match_key
        schema_match_key = None
        if schema and field_path:
            field_def = get_field_def(schema, field_path)
            if field_def and field_def.get("__merge__") == "smart_match":
                mk = field_def.get("__match_key__")
                if mk:
                    schema_match_key = mk[0]

        # 对象数组：按 key 匹配做元素级 delta
        if (mod and all(isinstance(x, dict) for x in mod)
                and (not base or all(isinstance(x, dict) for x in base))):
            if schema_match_key:
                return _object_array_delta(base, mod, schema_match_key,
                                           allow_deletions,
                                           schema, field_path)
            # 无 match_key 的对象数组：按序号位置匹配
            return _index_array_delta(base, mod, allow_deletions,
                                      schema, field_path)

        # 非对象数组：原子比较
        if base == mod:
            return None
        return copy.deepcopy(mod)

    # 标量与数组兼容：mod 作者可能将 [value, extra] 简化为 value
    # 注意：只处理"标量简化数组"方向，反向（标量→数组）是真实修改不能跳过
    # DupList 不适用此启发式（DupList 到标量是真实变更，已在上方处理）
    if isinstance(base, list) and not isinstance(mod, (list, dict)):
        if not isinstance(base, DupList) and mod in base:
            return None  # 标量已在数组中，视为无变化

    # 标量比较
    if base == mod:
        return None
    return copy.deepcopy(mod)



@profile
def merge_file(
    base_data: dict,
    mod_data_list: list[tuple[str, str, dict]],
    rel_path: str = "",
    schema: dict | None = None,
    overrides_dir: Path | None = None,
) -> MergeResult:
    """
    合并单个文件。

    参数:
        base_data: 游戏本体的 JSON 数据
        mod_data_list: [(mod_id, mod_name, mod_json_data), ...] 按优先级排序
        rel_path: 文件相对路径（用于判断特殊文件）
        schema: 该文件对应的 schema 规则
        overrides_dir: 用户 override 文件目录，存在时检查并加载用户编辑的合并结果
    """
    result = MergeResult()
    file_name = Path(rel_path).name if rel_path else ""

    # sfx_config.json 等特殊文件：整文件替换
    if file_name in WHOLE_FILE_REPLACE:
        if mod_data_list:
            _, last_mod_name, last_mod_data = mod_data_list[-1]
            result.merged_data = copy.deepcopy(last_mod_data)
            if len(mod_data_list) > 1:
                log.warning(f"{rel_path}: 多个 mod 修改此文件（整文件替换模式），最终使用 {last_mod_name}")
        else:
            result.merged_data = copy.deepcopy(base_data)
        return result

    current: dict = copy.deepcopy(base_data)

    # 从 schema 或数据结构确定文件类型
    if schema:
        file_type = schema.get("_meta", {}).get("file_type", classify_json(base_data))
    else:
        file_type = classify_json(base_data)

    # 确定 schema 根 key
    root_key = get_schema_root_key(schema) if schema else None

    for mod_entry in mod_data_list:
        mod_id, mod_name, mod_data = mod_entry[0], mod_entry[1], mod_entry[2]
        source_file = mod_entry[3] if len(mod_entry) > 3 else ""

        # 设置线程本地上下文，供 deep_merge 内部的警告使用
        merge_ctx.mod_name = mod_name
        merge_ctx.mod_id = mod_id
        merge_ctx.rel_path = rel_path
        merge_ctx.source_file = source_file

        if file_type == "dictionary":
            for key, value in mod_data.items():
                if value is _DELETED:
                    current.pop(key, None)
                    continue
                if key in current:
                    field_path = [root_key] if root_key else None
                    current[key] = deep_merge(current[key], value, schema, field_path)
                else:
                    current[key] = copy.deepcopy(value)
                    result.new_entries.append(("", mod_name, f"新增 key: {key}"))
        else:
            # 实体型和配置型
            field_path = [root_key] if root_key else None
            current = deep_merge(current, mod_data, schema, field_path)

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
    cancel_check=None,
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

    results = {}
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
                log.warning(msg)
                diag.error("merge", msg)
                parse_failures.append(ParseFailure(
                    file_path=base_file, rel_path=rel_path,
                    error_msg=e.msg, error_line=getattr(e, 'lineno', 0) or 0,
                    is_base=True, mod_id="", mod_name="",
                ))
                continue
        else:
            # diag.info("merge", f"{rel_path}: 游戏本体中不存在此文件，视为 Mod 新增")
            base_data = {}

        # 确定文件类型（用于 delta 计算）
        file_type = classify_json(base_data) if base_data else "config"

        # 查找 schema（delta 计算需要 schema 的 match_key 信息）
        schema = resolve_schema(rel_path, schemas) if schemas else None
        root_key = get_schema_root_key(schema) if schema else None

        # 加载各 mod 的数据，计算 delta（只保留实际修改的部分）
        mod_data_list = []
        for mod_id, mod_name, mod_file in mod_file_list:
            try:
                mod_data = load_json(mod_file, readonly=True)
            except json.JSONDecodeError as e:
                msg = f"{mod_file}: JSON 解析失败 ({e.msg})"
                log.warning(msg)
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

            delta = compute_mod_delta(base_data, mod_data, file_type, allow_deletions,
                                      schema=schema, root_key=root_key)
            if delta:
                mod_data_list.append((mod_id, mod_name, delta, str(mod_file)))

        if not mod_data_list:
            continue

        # 合并
        merge_result = merge_file(base_data, mod_data_list, rel_path,
                                   schema=schema, overrides_dir=overrides_dir)
        results[rel_path] = merge_result

        # 输出
        out_file = output_path / rel_path
        dump_json(merge_result.merged_data, out_file)

    return results, parse_failures


def raw_copy_file(source: Path, rel_path: str, output_path: Path):
    """将文件原样复制到输出目录"""
    dest = output_path / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)


def _validate_tag_names(base_data: dict, mod_data_list: list[tuple[str, str, dict]]):
    """验证 tag.json 中覆盖的 tag 的 name 是否与原 tag 一致"""
    for _, mod_name, mod_data in mod_data_list:
        for key, value in mod_data.items():
            if key in base_data and isinstance(value, dict) and isinstance(base_data[key], dict):
                base_name = base_data[key].get('name', '')
                mod_tag_name = value.get('name', '')
                if mod_tag_name and base_name and mod_tag_name != base_name:
                    msg = (f"tag.json: Mod [{mod_name}] 的 tag [{key}] "
                           f"name=\"{mod_tag_name}\" 与本体 name=\"{base_name}\" 不一致，可能导致游戏出错")
                    log.warning(msg)
                    diag.warn("merge", msg)
