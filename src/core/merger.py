"""
核心合并算法 - 字典合并、实体合并、数组智能匹配
"""
import json
import logging
import copy
from dataclasses import dataclass, field
from pathlib import Path

from .json_parser import load_json, dump_json

log = logging.getLogger(__name__)

# 需要智能合并的数组字段名（rite 文件中使用）
SMART_MERGE_ARRAY_KEYS = {'settlement', 'settlement_prior', 'settlement_extre'}

# 需要整文件替换而非合并的文件
WHOLE_FILE_REPLACE = {'sfx_config.json'}


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

    # 级别3: 按位置匹配（如果 mod 和 base 数组长度相同，按索引对应）
    # 这里不实现，由调用方在无法匹配时决定策略

    return None


def _extract_action_keys(action: dict) -> set[str]:
    """提取 action 中的关键标识（rite ID, event_on ID, prompt.id, option.id 等）"""
    keys = set()
    if not isinstance(action, dict):
        return keys

    # rite 指令
    if 'rite' in action:
        keys.add(f"rite:{action['rite']}")
    # event_on 指令
    if 'event_on' in action:
        val = action['event_on']
        if isinstance(val, list):
            for v in val:
                keys.add(f"event_on:{v}")
        else:
            keys.add(f"event_on:{val}")
    # prompt.id
    if isinstance(action.get('prompt'), dict) and 'id' in action['prompt']:
        keys.add(f"prompt:{action['prompt']['id']}")
    # option.id
    if isinstance(action.get('option'), dict) and 'id' in action['option']:
        keys.add(f"option:{action['option']['id']}")
    # confirm.id
    if isinstance(action.get('confirm'), dict) and 'id' in action['confirm']:
        keys.add(f"confirm:{action['confirm']['id']}")

    return keys


# ==================== 通用数组合并 ====================

def _merge_settlement_array(base_arr: list, mod_arr: list) -> list:
    """
    智能合并 settlement 类数组。
    自动识别 rite 风格和 event 风格，使用对应的匹配策略。
    保持 base 顺序不变，匹配的条目原地合并，新增条目追加到末尾。
    """
    result = copy.deepcopy(base_arr)
    matched = set()

    # 选择匹配函数
    if _is_event_settlement(mod_arr) or _is_event_settlement(base_arr):
        find_fn = _find_matching_event_item
    else:
        find_fn = _find_matching_rite_item

    for mod_item in mod_arr:
        if not isinstance(mod_item, dict):
            continue
        idx = find_fn(result, mod_item, matched)
        if idx is not None:
            result[idx] = deep_merge(result[idx], mod_item)
            matched.add(idx)
        else:
            result.append(copy.deepcopy(mod_item))

    return result


def deep_merge(base: object, override: object) -> object:
    """
    递归深度合并。
    - dict + dict: 递归合并
    - list + list: 根据字段名判断是否智能合并
    - 其他: override 直接替换
    """
    if not isinstance(base, dict) or not isinstance(override, dict):
        return copy.deepcopy(override)

    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result:
            if isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = deep_merge(result[key], value)
            elif (isinstance(result[key], list) and isinstance(value, list)
                  and key in SMART_MERGE_ARRAY_KEYS):
                result[key] = _merge_settlement_array(result[key], value)
            else:
                result[key] = copy.deepcopy(value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def classify_json(data: dict) -> str:
    """
    分类 JSON 文件类型。
    返回: "dictionary" | "entity" | "config"
    """
    if not isinstance(data, dict):
        return "config"

    # 实体型：顶层有 id 字段
    if 'id' in data:
        return "entity"

    # 字典型：所有 key 的 value 都是 dict 且包含 id
    keys = list(data.keys())
    if keys and all(isinstance(data[k], dict) for k in keys[:5]):
        if any('id' in data[k] for k in keys[:5]):
            return "dictionary"

    return "config"


def merge_file(
    base_data: dict,
    mod_data_list: list[tuple[str, str, dict]],
    rel_path: str = ""
) -> MergeResult:
    """
    合并单个文件。

    参数:
        base_data: 游戏本体的 JSON 数据
        mod_data_list: [(mod_id, mod_name, mod_json_data), ...] 按优先级排序
        rel_path: 文件相对路径（用于判断特殊文件）

    返回:
        MergeResult
    """
    result = MergeResult()
    file_name = Path(rel_path).name if rel_path else ""

    # sfx_config.json 等特殊文件：整文件替换，最后一个 mod 的数据直接生效
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
    file_type = classify_json(base_data)

    for _, mod_name, mod_data in mod_data_list:
        if file_type == "dictionary":
            # 字典型：按 key 合并
            for key, value in mod_data.items():
                if key in current:
                    current[key] = deep_merge(current[key], value)
                else:
                    current[key] = copy.deepcopy(value)
                    result.new_entries.append(("", mod_name, f"新增 key: {key}"))
        else:
            # 实体型和配置型：整体深度合并
            current = deep_merge(current, mod_data)  # type: ignore[assignment]

    result.merged_data = current
    return result


# 合并警告收集器
merge_warnings: list[str] = []


def merge_all_files(
    game_config_path: Path,
    mod_configs: list[tuple[str, str, Path]],
    output_path: Path
) -> dict[str, MergeResult]:
    """
    合并所有文件。

    参数:
        game_config_path: 游戏本体 config 目录
        mod_configs: [(mod_id, mod_name, mod_config_path), ...] 按优先级排序
        output_path: 输出目录

    返回:
        {相对路径: MergeResult}
    """
    merge_warnings.clear()

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

        # 加载各 mod 的数据
        mod_data_list = []
        for mod_id, mod_name, mod_file in mod_file_list:
            mod_data = load_json(mod_file)
            mod_data_list.append((mod_id, mod_name, mod_data))

        # tag.json name 匹配验证
        if rel_path == "tag.json" and base_data:
            _validate_tag_names(base_data, mod_data_list)

        # 合并
        merge_result = merge_file(base_data, mod_data_list, rel_path)
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
