"""
核心合并算法 - 基于 schema 规则的字典合并、实体合并、数组智能匹配
"""
import json
import logging
import copy
from dataclasses import dataclass, field
from pathlib import Path

from .json_parser import load_json, dump_json
from .schema_loader import (
    load_schemas, resolve_schema, get_field_def,
    get_schema_root_key, check_type_match,
)

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


def _is_rite_settlement(items: list[dict]) -> bool:
    """判断数组是否是 rite 风格的 settlement（含 condition / result_title 等字段）"""
    if not items:
        return False
    sample = items[0]
    return isinstance(sample, dict) and ('condition' in sample or 'result_title' in sample)


def _is_event_settlement(items: list[dict]) -> bool:
    """判断数组是否是 event 风格的 settlement（含 tips_text / action 等字段）"""
    if not items:
        return False
    sample = items[0]
    return isinstance(sample, dict) and 'action' in sample and 'condition' not in sample


# ==================== Rite 风格匹配 ====================

def _find_matching_rite_item(base_arr: list[dict], mod_item: dict, matched: set[int]) -> int | None:
    """
    在 rite 的 base 数组中查找与 mod_item 匹配的条目。
    四级优先级依次尝试，第一个命中即返回。
    """
    mod_cond = mod_item.get('condition', {})

    # 级别1: guid 精确匹配
    mod_guid = mod_item.get('guid')
    if mod_guid:
        for i, base_item in enumerate(base_arr):
            if i not in matched and base_item.get('guid') == mod_guid:
                return i

    # 级别2: condition 中的槽位引用匹配（s1.is, s2.is, s3.is 等）
    slot_keys = [k for k in mod_cond if '.is' in k and k.split('.')[0].startswith('s')]
    if slot_keys:
        for i, base_item in enumerate(base_arr):
            if i in matched:
                continue
            base_cond = base_item.get('condition', {})
            if all(base_cond.get(k) == mod_cond[k] for k in slot_keys):
                return i

    # 级别3: condition 完整内容序列化匹配
    if mod_cond:
        mod_cond_str = json.dumps(mod_cond, sort_keys=True, ensure_ascii=False)
        for i, base_item in enumerate(base_arr):
            if i in matched:
                continue
            base_cond_str = json.dumps(
                base_item.get('condition', {}), sort_keys=True, ensure_ascii=False
            )
            if base_cond_str == mod_cond_str:
                return i

    # 级别4: result_title + result_text 组合匹配
    mod_title = mod_item.get('result_title', '')
    mod_text = mod_item.get('result_text', '')
    if mod_title or mod_text:
        for i, base_item in enumerate(base_arr):
            if i in matched:
                continue
            if (base_item.get('result_title', '') == mod_title and
                    base_item.get('result_text', '') == mod_text):
                return i

    return None


# ==================== Event 风格匹配 ====================

def _find_matching_event_item(base_arr: list[dict], mod_item: dict, matched: set[int]) -> int | None:
    """
    在 event 的 base 数组中查找与 mod_item 匹配的条目。
    event settlement 结构: {tips_resource, tips_text, action}
    """
    mod_action = mod_item.get('action', {})

    # 级别1: action 内容中的关键指令匹配（rite, event_on, prompt.id, option.id 等）
    mod_keys = _extract_action_keys(mod_action)
    if mod_keys:
        for i, base_item in enumerate(base_arr):
            if i in matched:
                continue
            base_keys = _extract_action_keys(base_item.get('action', {}))
            if base_keys and base_keys == mod_keys:
                return i

    # 级别2: action 完整内容序列化匹配
    if mod_action:
        mod_str = json.dumps(mod_action, sort_keys=True, ensure_ascii=False)
        for i, base_item in enumerate(base_arr):
            if i in matched:
                continue
            base_str = json.dumps(base_item.get('action', {}), sort_keys=True, ensure_ascii=False)
            if base_str == mod_str:
                return i

    return None


def _extract_action_keys(action: dict) -> set[str]:
    """提取 action 中的关键标识（rite ID, event_on ID, prompt.id, option.id 等）"""
    keys = set()
    if not isinstance(action, dict):
        return keys

    if 'rite' in action:
        keys.add(f"rite:{action['rite']}")
    if 'event_on' in action:
        val = action['event_on']
        if isinstance(val, list):
            for v in val:
                keys.add(f"event_on:{v}")
        else:
            keys.add(f"event_on:{val}")
    if isinstance(action.get('prompt'), dict) and 'id' in action['prompt']:
        keys.add(f"prompt:{action['prompt']['id']}")
    if isinstance(action.get('option'), dict) and 'id' in action['option']:
        keys.add(f"option:{action['option']['id']}")
    if isinstance(action.get('confirm'), dict) and 'id' in action['confirm']:
        keys.add(f"confirm:{action['confirm']['id']}")

    return keys


# ==================== 通用数组合并 ====================

def _merge_settlement_array(base_arr: list, mod_arr: list,
                            schema: dict | None,
                            element_path: list[str] | None) -> list:
    """
    智能合并 settlement 类数组。
    自动识别 rite 风格和 event 风格，使用对应的匹配策略。
    保持 base 顺序不变，匹配的条目原地合并，新增条目追加到末尾。
    """
    result = copy.deepcopy(base_arr)
    matched = set()

    # 优先从 schema 判断匹配策略
    match_strategy = None
    if schema and element_path:
        field_def = get_field_def(schema, element_path)  # 获取数组字段本身的定义
        if field_def:
            match_strategy = field_def.get("match_strategy")

    if match_strategy == "event":
        find_fn = _find_matching_event_item
    elif match_strategy == "rite":
        find_fn = _find_matching_rite_item
    elif _is_event_settlement(mod_arr) or _is_event_settlement(base_arr):
        find_fn = _find_matching_event_item
    else:
        find_fn = _find_matching_rite_item

    for mod_item in mod_arr:
        if not isinstance(mod_item, dict):
            continue
        idx = find_fn(result, mod_item, matched)
        if idx is not None:
            result[idx] = deep_merge(result[idx], mod_item, schema, element_path)
            matched.add(idx)
        else:
            result.append(copy.deepcopy(mod_item))

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
_COMMON_MATCH_KEYS = ('id', 'tag', 'guid', 'key')


def _find_array_match_key(arr: list) -> str | None:
    """在对象数组中找到可用于匹配的唯一标识字段"""
    if not arr or not all(isinstance(x, dict) for x in arr):
        return None
    for key in _COMMON_MATCH_KEYS:
        values = []
        for item in arr:
            if key not in item:
                break
            values.append(item[key])
        else:
            if len(set(str(v) for v in values)) == len(values):
                return key
    return None


def _append_array(base_arr: list, override_arr: list,
                  schema: dict | None = None,
                  child_path: list[str] | None = None) -> list:
    """数组追加去重。对象数组支持按 key 字段匹配合并。"""
    result = copy.deepcopy(base_arr)

    # 对象数组：尝试按 key 字段匹配并合并
    if (base_arr and override_arr
            and all(isinstance(x, dict) for x in base_arr)
            and all(isinstance(x, dict) for x in override_arr)):
        match_key = _find_array_match_key(base_arr)
        if match_key:
            key_index: dict = {}
            for i, item in enumerate(result):
                kv = item.get(match_key)
                if kv is not None:
                    key_index[kv] = i

            for item in override_arr:
                kv = item.get(match_key)
                idx = key_index.get(kv) if kv is not None else None
                if idx is not None:
                    # 按 key 匹配到 → 深度合并更新
                    result[idx] = deep_merge(result[idx], item, schema, child_path)
                else:
                    result.append(copy.deepcopy(item))
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
        base: 基础数据
        override: 覆盖数据
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
        child_path = field_path + [key] if field_path else None
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
        is_dynamic = current_def.get("dynamic_keys", False)

        if not is_dynamic:
            # current_def 可能是 _entry/_fields 层（直接包含字段定义），
            # 也可能是某个 object 字段的定义（通过 fields 包含子字段）
            if "fields" in current_def:
                known_keys = set(current_def["fields"].keys())
            else:
                # 检查是否是字段列表层：每个 value 都是 field_def
                meta_keys = {"type", "merge", "dynamic_keys", "dynamic_value", "fields", "element", "match_strategy"}
                field_candidates = {k for k in current_def if k not in meta_keys}
                if field_candidates and all(
                    isinstance(current_def[k], dict) and "type" in current_def[k]
                    for k in field_candidates
                ):
                    known_keys = field_candidates

        if known_keys:
            for key in override:
                if key not in known_keys:
                    msg = f"未知字段 '{key}'，schema 中未定义"
                    merge_warnings.append(msg)

    return result


def _resolve_merge_strategy(child_def: dict | None, base_val, override_val, key: str) -> str:
    """确定字段的合并策略"""
    if child_def:
        strategy = child_def.get("merge", "replace")

        # 类型校验
        schema_type = child_def.get("type")
        if schema_type and override_val is not None:
            if not check_type_match(schema_type, override_val):
                from .schema_loader import _get_actual_type
                actual = _get_actual_type(override_val)
                msg = f"字段 '{key}' 类型不匹配: schema 期望 {schema_type}，实际为 {actual}"
                merge_warnings.append(msg)

        # 按类型分派：dynamic_value 可以为不同类型指定不同策略
        merge_by_type = child_def.get("merge_by_type")
        if merge_by_type:
            from .schema_loader import _get_actual_type
            actual = _get_actual_type(override_val)
            if actual in merge_by_type:
                strategy = merge_by_type[actual]

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
            # element_path = child_path + ["[]的某个元素"]，这里传 child_path 让内部导航 element
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
                sub = _recursive_delta(base_data[key], mod_val)
                if sub is not None:
                    delta[key] = sub  # 有变化的条目（只含变化字段）
        if allow_deletions:
            for key in base_data:
                if key not in mod_data:
                    delta[key] = _DELETED
        return delta
    else:
        # entity/config：递归提取变化字段
        result = _recursive_delta(base_data, mod_data)
        return result if result is not None else {}


def _recursive_delta(base, mod):
    """递归比较，返回 mod 相对于 base 的变化部分。None 表示无差异。"""
    if isinstance(base, dict) and isinstance(mod, dict):
        delta = {}
        for key, mod_val in mod.items():
            if key not in base:
                delta[key] = copy.deepcopy(mod_val)
            else:
                sub = _recursive_delta(base[key], mod_val)
                if sub is not None:
                    delta[key] = sub
        return delta if delta else None

    if isinstance(base, list) and isinstance(mod, list):
        # 数组原子比较（不递归进元素，交给 smart_match/append 处理）
        if base == mod:
            return None
        return copy.deepcopy(mod)

    # 标量与数组兼容：mod 作者可能将 [value, extra] 简化为 value
    if isinstance(base, list) and not isinstance(mod, (list, dict)):
        if mod in base:
            return None  # 标量已在数组中，视为无变化
    if isinstance(mod, list) and not isinstance(base, (list, dict)):
        if base in mod:
            return None  # 原标量已在新数组中，视为无变化

    # 标量比较
    if base == mod:
        return None
    return copy.deepcopy(mod)


def classify_json(data: dict) -> str:
    """
    分类 JSON 文件类型。
    返回: "dictionary" | "entity" | "config"
    """
    if not isinstance(data, dict):
        return "config"

    if 'id' in data:
        return "entity"

    keys = list(data.keys())
    if keys and all(isinstance(data[k], dict) for k in keys[:5]):
        if any('id' in data[k] for k in keys[:5]):
            return "dictionary"

    return "config"


def merge_file(
    base_data: dict,
    mod_data_list: list[tuple[str, str, dict]],
    rel_path: str = "",
    schema: dict | None = None,
) -> MergeResult:
    """
    合并单个文件。

    参数:
        base_data: 游戏本体的 JSON 数据
        mod_data_list: [(mod_id, mod_name, mod_json_data), ...] 按优先级排序
        rel_path: 文件相对路径（用于判断特殊文件）
        schema: 该文件对应的 schema 规则
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

    result.merged_data = current
    return result


# 合并警告收集器
merge_warnings: list[str] = []


def merge_all_files(
    game_config_path: Path,
    mod_configs: list[tuple[str, str, Path]],
    output_path: Path,
    schema_dir: Path | None = None,
    allow_deletions: bool = False,
) -> dict[str, MergeResult]:
    """
    合并所有文件。

    参数:
        game_config_path: 游戏本体 config 目录
        mod_configs: [(mod_id, mod_name, mod_config_path), ...] 按优先级排序
        output_path: 输出目录
        schema_dir: schema 规则文件目录
        allow_deletions: 是否允许删减（mod 中缺少的条目从结果中删除）
    """
    merge_warnings.clear()

    # 加载 schemas
    schemas = load_schemas(schema_dir) if schema_dir else {}

    # 收集所有 mod 涉及的文件
    all_files: dict[str, list[tuple[str, str, Path]]] = {}
    for mod_id, mod_name, mod_config_path in mod_configs:
        if not mod_config_path.exists():
            continue
        for json_file in mod_config_path.rglob("*.json"):
            rel = str(json_file.relative_to(mod_config_path)).replace("\\", "/")
            if rel not in all_files:
                all_files[rel] = []
            all_files[rel].append((mod_id, mod_name, json_file))

    results = {}
    for rel_path, mod_file_list in all_files.items():
        # 加载游戏本体文件
        base_file = game_config_path / rel_path
        if base_file.exists():
            base_data = load_json(base_file)
        else:
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
        merge_result = merge_file(base_data, mod_data_list, rel_path, schema=schema)
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
                    merge_warnings.append(msg)
