"""
核心合并算法 - 基于 schema 规则的字典合并、实体合并、数组智能匹配
"""
import json
import logging
import copy
from dataclasses import dataclass, field
from pathlib import Path

from .dsl_patterns import classify_dsl_key
from .json_parser import load_json, dump_json
from .schema_loader import (
    load_schemas, resolve_schema, get_field_def,
    get_schema_root_key, check_type_match,
)
from .type_utils import classify_json
from .mod_scanner import collect_mod_files
from .diagnostics import diag

log = logging.getLogger(__name__)

# 需要整文件替换而非合并的文件
WHOLE_FILE_REPLACE = {'sfx_config.json'}

# 删除标记（用于 allow_deletions 模式）
_DELETED = object()


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


# ==================== 统一数组元素匹配 ====================

def find_matching_item(base_arr: list[dict], mod_item: dict,
                       matched: set[int], match_keys: list[str]) -> int | None:
    """
    按 match_keys 中所有字段精确匹配，全部相等才算匹配到。
    返回 base_arr 中第一个匹配元素的索引，或 None。
    （用于一对一场景，如 conflict 差异对比）
    """
    for i, base_item in enumerate(base_arr):
        if i in matched:
            continue
        if all(
            key in mod_item and key in base_item
            and mod_item[key] == base_item[key]
            for key in match_keys
        ):
            return i
    return None


def _item_similarity(a: dict, b: dict) -> float:
    """计算两个 dict 的字符串相似度（0.0 ~ 1.0）"""
    from difflib import SequenceMatcher
    a_str = json.dumps(a, sort_keys=True, ensure_ascii=False)
    b_str = json.dumps(b, sort_keys=True, ensure_ascii=False)
    return SequenceMatcher(None, a_str, b_str).ratio()


def _resolve_duplicates(
    mod_items: list[tuple[int, dict]],
    base_arr: list,
    base_indices: list[int],
) -> tuple[list[tuple[int, dict, int]], list[tuple[int, dict]]]:
    """
    多对多相似度匹配：mod 侧和 base 侧各有多个同 key 元素。
    贪心策略：每次从所有 mod×base 配对中选相似度最高的一对，
    双方移出待匹配池，重复直到 base 候选耗尽。

    参数:
        mod_items: [(mod 在 mod_arr 中的原始索引, mod_item), ...]
        base_arr: result 数组的引用
        base_indices: base 中候选元素的索引列表

    返回:
        matched_pairs: [(mod_orig_idx, mod_item, base_idx), ...]
        unmatched_mod: [(mod_orig_idx, mod_item), ...] — 未匹配的 mod 元素（新增）
    """
    if not base_indices:
        return [], list(mod_items)

    remaining_mod = list(mod_items)
    remaining_base = list(base_indices)
    matched_pairs = []

    while remaining_base and remaining_mod:
        best_ratio = -1.0
        best_mi = 0
        best_bi = 0
        for mi, (_, mod_item) in enumerate(remaining_mod):
            for bi, base_idx in enumerate(remaining_base):
                ratio = _item_similarity(mod_item, base_arr[base_idx])
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_mi = mi
                    best_bi = bi
        mod_orig_idx, mod_item = remaining_mod.pop(best_mi)
        base_idx = remaining_base.pop(best_bi)
        matched_pairs.append((mod_orig_idx, mod_item, base_idx))

    return matched_pairs, remaining_mod


def _get_key_vals(item: dict, match_keys: list[str]) -> tuple | None:
    """提取 match_key 值元组，任一 key 缺失则返回 None"""
    vals = tuple(item.get(k) for k in match_keys)
    if any(v is None for v in vals):
        return None
    return vals


# ==================== 通用数组合并 ====================

def _merge_settlement_array(base_arr: list, mod_arr: list,
                            schema: dict | None,
                            element_path: list[str] | None) -> list:
    """
    智能合并 array<object> 数组。
    mod_arr 必须是经过 _object_array_delta 产出的 delta 数组，
    每个元素必须带有 _delta / _new_entry / _deleted 标记。

    处理规则：
      _delta:     按 match_key 找到 result 中的对应元素，deep_merge 应用变化
      _new_entry: 直接追加到 result 末尾
      _deleted:   从 result 中移除对应元素
    """
    result = copy.deepcopy(base_arr)

    # 从 schema 读取 match_key
    match_keys = None
    if schema and element_path:
        field_def = get_field_def(schema, element_path)
        if field_def:
            match_keys = field_def.get("match_key")

    if not match_keys:
        raise ValueError(
            f"smart_match 数组缺少 match_key 定义 (path: {element_path})"
        )

    # --- 按标记分类 ---
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
                f"smart_match 数组收到未标记的元素（缺少 _delta/_new_entry/_deleted），"
                f"match_keys={match_keys}, 元素 keys={list(mod_item.keys())}"
            )

    # --- 处理删除 ---
    matched = set()
    to_remove = set()
    for mod_item in deleted_items:
        idx = find_matching_item(result, mod_item, matched, match_keys)
        if idx is not None:
            to_remove.add(idx)
            matched.add(idx)

    # --- 建立 result 索引 ---
    result_index: dict[tuple, list[int]] = {}
    for ri, r_item in enumerate(result):
        if isinstance(r_item, dict):
            kv = _get_key_vals(r_item, match_keys)
            if kv is not None:
                result_index.setdefault(kv, []).append(ri)

    # --- 按 key 分组 delta 元素并合并 ---
    delta_groups: dict[tuple | None, list[tuple[int, dict]]] = {}
    for i, item in enumerate(delta_items):
        kv = _get_key_vals(item, match_keys)
        delta_groups.setdefault(kv, []).append((i, item))

    for kv, items in delta_groups.items():
        if kv is None:
            # match_key 缺失，无法匹配，追加
            for _, item in items:
                clean = {k: v for k, v in item.items() if k != '_delta'}
                result.append(clean)
            continue

        res_candidates = [ri for ri in result_index.get(kv, []) if ri not in matched]

        if res_candidates:
            pairs, unmatched = _resolve_duplicates(items, result, res_candidates)
            for _, mod_item, res_idx in pairs:
                # 清除标记后合并变化字段
                clean = {k: v for k, v in mod_item.items() if k != '_delta'}
                result[res_idx] = deep_merge(result[res_idx], clean, schema, element_path)
                matched.add(res_idx)
            for _, mod_item in unmatched:
                clean = {k: v for k, v in mod_item.items() if k != '_delta'}
                result.append(clean)
        else:
            for _, item in items:
                clean = {k: v for k, v in item.items() if k != '_delta'}
                result.append(clean)

    # --- 追加新增元素 ---
    for item in new_entry_items:
        clean = {k: v for k, v in item.items() if k != '_new_entry'}
        result.append(copy.deepcopy(clean))

    # --- 移除删除元素 ---
    if to_remove:
        result = [item for i, item in enumerate(result) if i not in to_remove]

    return result


def _coerce_and_merge_array(base_val, override_val):
    """
    类型不匹配时的数组合并策略。
    将标量一侧包裹为单元素列表，然后合并（去重追加）。
    """
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

            to_remove = set()
            for item in override_arr:
                if not isinstance(item, dict):
                    continue

                if item.get('_deleted'):
                    kv = item.get(match_key)
                    idx = key_index.get(kv) if kv is not None else None
                    if idx is not None:
                        to_remove.add(idx)
                    continue

                if item.get('_new_entry'):
                    clean = {k: v for k, v in item.items() if k != '_new_entry'}
                    result.append(copy.deepcopy(clean))
                    continue

                if item.get('_delta'):
                    clean = {k: v for k, v in item.items() if k != '_delta'}
                    kv = item.get(match_key)
                    idx = key_index.get(kv) if kv is not None else None
                    if idx is not None:
                        result[idx] = deep_merge(result[idx], clean, schema, child_path)
                    else:
                        result.append(clean)
                    continue

                raise ValueError(
                    f"append 对象数组收到未标记的元素（缺少 _delta/_new_entry/_deleted），"
                    f"match_key={match_key}, 元素 keys={list(item.keys())}"
                )

            # 移除标记删除的元素
            if to_remove:
                result = [r for i, r in enumerate(result) if i not in to_remove]
            return result

    for item in override_arr:
        if item not in result:
            result.append(copy.deepcopy(item))
    return result


def deep_merge(base: object, override: object,
               schema: dict | None,
               field_path: list[str] | None) -> object:
    """
    递归深度合并，由 schema 驱动合并策略。

    参数:
        base: 基础数据（当前合并状态）
        override: 覆盖数据（mod 的 delta）
        schema: schema 规则字典
        field_path: 当前字段路径（用于查找 schema 定义）
    """
    if not isinstance(base, dict) or not isinstance(override, dict):
        return copy.deepcopy(override)

    # 查找当前层的 schema 定义
    current_def = None
    if schema and field_path:
        current_def = get_field_def(schema, field_path)

    result = copy.deepcopy(base)

    for key, value in override.items():
        if value is _DELETED:
            result.pop(key, None)
            continue
        child_path = field_path + [key] if field_path is not None else None
        child_def = get_field_def(schema, child_path) if schema and child_path else None

        # 确定合并策略
        merge_strategy = _resolve_merge_strategy(child_def, result.get(key), value, key)

        if key in result:
            result[key] = _apply_merge_strategy(
                merge_strategy, result[key], value, schema, child_path
            )
        else:
            result[key] = copy.deepcopy(value)

    # 未知 key 警告：检查 override 中的 key 是否在 schema 中有定义
    if current_def and isinstance(current_def, dict):
        # 收集 schema 中已知的字段名
        known_keys = set()

        # current_def 可能是 _entry/_fields 层（直接包含字段定义），
        # 也可能是某个 object 字段的定义（通过 fields 包含子字段）
        if "fields" in current_def:
            known_keys = set(current_def["fields"].keys())
        else:
            # 检查是否是字段列表层：每个 value 都是 field_def
            meta_keys = {"type", "merge", "fields", "element", "match_key",
                         "_template", "_use_template", "_templates"}
            field_candidates = {k for k in current_def if k not in meta_keys}
            if field_candidates and all(
                isinstance(current_def[k], dict) and ("type" in current_def[k] or "_use_template" in current_def[k])
                for k in field_candidates
            ):
                known_keys = field_candidates

        if known_keys:
            for key in override:
                if key not in known_keys:
                    # 全局 DSL 模式兜底：匹配 DSL pattern 的 key 不发警告
                    if classify_dsl_key(key):
                        continue
                    msg = f"未知字段 '{key}'，schema 中未定义"
                    diag.warn("merge", msg)

    return result


def _resolve_merge_strategy(child_def: dict | None, base_val, override_val, key: str) -> str:
    """确定字段的合并策略"""
    if child_def:
        strategy = child_def.get("merge", "replace")

        # 类型校验
        schema_type = child_def.get("type")
        if schema_type and override_val is not None:
            if not check_type_match(schema_type, override_val):
                from .type_utils import get_type_str
                actual = get_type_str(override_val)
                msg = f"字段 '{key}' 类型不匹配: schema 期望 {schema_type}，实际为 {actual}"
                diag.warn("merge", msg)

        return strategy

    # 无 schema 时的默认策略：根据数据类型推断
    if isinstance(base_val, dict) and isinstance(override_val, dict):
        return "merge"
    return "replace"


def _apply_merge_strategy(strategy: str, base_val, override_val,
                          schema: dict | None, child_path: list[str] | None) -> object:
    """根据合并策略执行合并"""
    if strategy == "replace":
        return copy.deepcopy(override_val)

    elif strategy == "merge":
        if isinstance(base_val, dict) and isinstance(override_val, dict):
            return deep_merge(base_val, override_val, schema, child_path)
        return copy.deepcopy(override_val)

    elif strategy == "append":
        if isinstance(base_val, list) and isinstance(override_val, list):
            return _append_array(base_val, override_val, schema, child_path)
        return copy.deepcopy(override_val)

    elif strategy == "smart_match":
        if isinstance(base_val, list) and isinstance(override_val, list):
            return _merge_settlement_array(base_val, override_val, schema, child_path)
        return copy.deepcopy(override_val)

    elif strategy == "coerce":
        return _coerce_and_merge_array(base_val, override_val)

    else:
        return copy.deepcopy(override_val)


def compute_mod_delta(base_data: dict, mod_data: dict,
                      file_type: str, allow_deletions: bool = False) -> dict:
    """
    计算 mod 相对于游戏本体的实际差异。

    只提取 mod 真正修改的部分，忽略与本体完全相同的内容。
    对 dictionary 类型文件按条目级 + 字段级递归 diff，
    对 entity/config 类型文件按字段级递归 diff。
    """
    if not base_data:
        return mod_data  # 本体无此文件，全部是新增

    if file_type == "dictionary":
        delta = {}
        for key, mod_val in mod_data.items():
            if key not in base_data:
                delta[key] = mod_val  # 新增条目
            else:
                sub = _recursive_delta(base_data[key], mod_val, allow_deletions)
                if sub is not None:
                    delta[key] = sub  # 有变化的条目（只含变化字段）
        if allow_deletions:
            for key in base_data:
                if key not in mod_data:
                    delta[key] = _DELETED
        return delta
    else:
        # entity/config：递归提取变化字段
        result = _recursive_delta(base_data, mod_data, allow_deletions)
        return result if result is not None else {}


def _object_array_delta(base_arr: list[dict], mod_arr: list[dict],
                        match_key: str, allow_deletions: bool = False) -> list[dict] | None:
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
        pairs, unmatched = _resolve_duplicates(
            mod_indexed, base_items, base_indices
        )

        for _, mod_item, base_idx in pairs:
            elem_delta = _recursive_delta(base_items[base_idx], mod_item, allow_deletions)
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


def _recursive_delta(base, mod, allow_deletions=False):
    """递归比较，返回 mod 相对于 base 的变化部分。None 表示无差异。"""
    if isinstance(base, dict) and isinstance(mod, dict):
        delta = {}
        for key, mod_val in mod.items():
            if key not in base:
                delta[key] = copy.deepcopy(mod_val)
            else:
                sub = _recursive_delta(base[key], mod_val, allow_deletions)
                if sub is not None:
                    delta[key] = sub
        if allow_deletions:
            for key in base:
                if key not in mod:
                    delta[key] = _DELETED
        return delta if delta else None

    if isinstance(base, list) and isinstance(mod, list):
        # 对象数组：按 key 匹配做元素级 delta
        if (base and mod
                and all(isinstance(x, dict) for x in base)
                and all(isinstance(x, dict) for x in mod)):
            match_key = find_array_match_key(base)
            if match_key:
                return _object_array_delta(base, mod, match_key, allow_deletions)

        # 无法匹配或非对象数组：原子比较
        if base == mod:
            return None
        return copy.deepcopy(mod)

    # 标量与数组兼容：mod 作者可能将 [value, extra] 简化为 value
    # 注意：只处理"标量简化数组"方向，反向（标量→数组）是真实修改不能跳过
    if isinstance(base, list) and not isinstance(mod, (list, dict)):
        if mod in base:
            return None  # 标量已在数组中，视为无变化

    # 标量比较
    if base == mod:
        return None
    return copy.deepcopy(mod)



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

    for mod_id, mod_name, mod_data in mod_data_list:
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
                current = json.loads(override_file.read_text(encoding="utf-8"))

    result.merged_data = current
    return result



def merge_all_files(
    game_config_path: Path,
    mod_configs: list[tuple[str, str, Path]],
    output_path: Path,
    schema_dir: Path | None = None,
    allow_deletions: bool = False,
    cancel_check=None,
    overrides_dir: Path | None = None,
) -> dict[str, MergeResult]:
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
    """
    diag.snapshot("merge")  # 清空上次的合并警告

    # 加载 schemas
    schemas = load_schemas(schema_dir) if schema_dir else {}

    all_files = collect_mod_files(mod_configs)

    results = {}
    for rel_path, mod_file_list in all_files.items():
        if cancel_check:
            cancel_check()
        # 加载游戏本体文件
        base_file = game_config_path / rel_path
        if base_file.exists():
            base_data = load_json(base_file)
        else:
            diag.info("merge", f"{rel_path}: 游戏本体中不存在此文件，视为 Mod 新增")
            base_data = {}

        # 确定文件类型（用于 delta 计算）
        file_type = classify_json(base_data) if base_data else "config"

        # 加载各 mod 的数据，计算 delta（只保留实际修改的部分）
        mod_data_list = []
        for mod_id, mod_name, mod_file in mod_file_list:
            mod_data = load_json(mod_file)

            # tag.json name 匹配验证（需在 delta 计算前用原始数据验证）
            if rel_path == "tag.json" and base_data:
                _validate_tag_names(base_data, [(mod_id, mod_name, mod_data)])

            delta = compute_mod_delta(base_data, mod_data, file_type, allow_deletions)
            if delta:
                mod_data_list.append((mod_id, mod_name, delta))

        if not mod_data_list:
            continue

        # 查找 schema
        schema = resolve_schema(rel_path, schemas) if schemas else None

        # 合并
        merge_result = merge_file(base_data, mod_data_list, rel_path,
                                   schema=schema, overrides_dir=overrides_dir)
        results[rel_path] = merge_result

        # 输出
        out_file = output_path / rel_path
        dump_json(merge_result.merged_data, out_file)

    return results


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
