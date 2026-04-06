"""
冲突检测与覆盖链报告
"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .json_parser import load_json
from .mod_scanner import collect_mod_files
from .diagnostics import diag
from .schema_generator import SEP
from .schema_loader import load_schemas, resolve_schema, get_field_def, get_schema_root_key
from .merger import (
    find_matching_rite_item, find_matching_event_item,
    find_matching_tag_item, is_event_settlement,
    extract_action_keys,
)

log = logging.getLogger(__name__)


@dataclass
class FieldOverride:
    """单个字段的覆盖信息"""
    field_path: str
    base_value: object = None
    # [(mod_name, value), ...]
    mod_values: list[tuple[str, object]] = field(default_factory=list)
    final_value: object = None

    @property
    def is_conflict(self) -> bool:
        """多个 mod 修改了同一字段"""
        return len(self.mod_values) > 1


@dataclass
class FileOverrideInfo:
    """单个文件的覆盖信息"""
    rel_path: str
    # 参与覆盖的 mod 列表（按优先级顺序）
    mod_chain: list[str] = field(default_factory=list)
    # 字段级覆盖详情
    field_overrides: list[FieldOverride] = field(default_factory=list)
    # 新增条目
    new_entries: list[tuple[str, str]] = field(default_factory=list)  # (mod_name, description)

    @property
    def has_conflict(self) -> bool:
        return any(f.is_conflict for f in self.field_overrides)


# ==================== 数组匹配辅助 ====================

def _get_match_fn(match_strategy, base_arr, mod_arr):
    """根据匹配策略返回匹配函数"""
    if match_strategy == "event":
        return find_matching_event_item
    elif match_strategy == "tag":
        return find_matching_tag_item
    elif match_strategy == "rite":
        return find_matching_rite_item
    # fallback：自动检测
    elif is_event_settlement(mod_arr) or is_event_settlement(base_arr):
        return find_matching_event_item
    else:
        return find_matching_rite_item


def _make_element_label(item, match_strategy):
    """为数组元素生成可读标识"""
    if not isinstance(item, dict):
        return str(item)
    if match_strategy == "tag":
        return item.get("tag", "?")
    if "guid" in item:
        return f"guid={item['guid']}"
    if "result_title" in item:
        title = item["result_title"]
        return title[:20] if len(title) > 20 else title
    if "action" in item:
        keys = extract_action_keys(item.get("action", {}))
        if keys:
            return next(iter(keys))
    return f"#{hash(json.dumps(item, sort_keys=True, ensure_ascii=False)) % 10000}"


# ==================== 差异收集 ====================

def _collect_smart_match_diffs(base_arr, mod_arr, path,
                               match_strategy, schema, field_path):
    """可匹配数组的原子化比较：按策略匹配元素后递归比较子字段"""
    diffs = []
    find_fn = _get_match_fn(match_strategy, base_arr, mod_arr)
    matched_base = set()

    for mod_item in mod_arr:
        if not isinstance(mod_item, dict):
            continue
        idx = find_fn(base_arr, mod_item, matched_base)
        if idx is not None:
            matched_base.add(idx)
            elem_label = _make_element_label(mod_item, match_strategy)
            elem_path = f"{path}[{elem_label}]"
            # 递归比较子字段（像 object 一样）
            diffs.extend(_collect_field_diffs(
                base_arr[idx], mod_item, elem_path,
                schema, field_path
            ))
        else:
            elem_label = _make_element_label(mod_item, match_strategy)
            diffs.append((f"{path}[{elem_label}]", None, mod_item))

    # base 中未匹配的 = 删除
    for i, base_item in enumerate(base_arr):
        if i not in matched_base:
            elem_label = _make_element_label(base_item, match_strategy)
            diffs.append((f"{path}[{elem_label}]", base_item, None))

    return diffs


def _collect_unmatched_array_diffs(base_arr, mod_arr, path):
    """不可匹配数组：每个 mod 元素与 base 所有元素整体比较"""
    diffs = []

    def _serialize(x):
        if isinstance(x, (dict, list)):
            return json.dumps(x, sort_keys=True, ensure_ascii=False)
        return x

    base_strs = [_serialize(x) for x in base_arr]
    mod_strs = [_serialize(x) for x in mod_arr]

    # 新增：mod 中有但 base 中没有的
    for i, ms in enumerate(mod_strs):
        if ms not in base_strs:
            diffs.append((f"{path}[+]", None, mod_arr[i]))

    # 删除：base 中有但 mod 中没有的
    for i, bs in enumerate(base_strs):
        if bs not in mod_strs:
            diffs.append((f"{path}[-]", base_arr[i], None))

    return diffs


def _collect_field_diffs(
    base: object, mod_data: object, prefix: str = "",
    schema: dict | None = None, field_path: list[str] | None = None
) -> list[tuple[str, object, object]]:
    """
    收集 mod 相对于 base 的字段差异。
    返回: [(field_path, base_value, mod_value), ...]

    参数:
        schema: schema 规则（用于判断数组的匹配策略）
        field_path: 当前在 schema 中的路径
    """
    diffs = []

    if isinstance(base, dict) and isinstance(mod_data, dict):
        all_keys = set(list(base.keys()) + list(mod_data.keys()))
        for key in all_keys:
            path = f"{prefix}{SEP}{key}" if prefix else key
            child_path = field_path + [key] if field_path else None

            if key in mod_data and key not in base:
                diffs.append((path, None, mod_data[key]))
            elif key in mod_data and key in base:
                if isinstance(base[key], dict) and isinstance(mod_data[key], dict):
                    diffs.extend(_collect_field_diffs(
                        base[key], mod_data[key], path,
                        schema, child_path
                    ))
                elif isinstance(base[key], list) and isinstance(mod_data[key], list):
                    # 从 schema 判断数组合并策略
                    child_def = get_field_def(schema, child_path) if schema and child_path else None
                    merge_strategy = child_def.get("merge") if child_def else None
                    match_strategy = child_def.get("match_strategy") if child_def else None

                    if merge_strategy == "smart_match":
                        diffs.extend(_collect_smart_match_diffs(
                            base[key], mod_data[key], path,
                            match_strategy, schema, child_path
                        ))
                    else:
                        diffs.extend(_collect_unmatched_array_diffs(
                            base[key], mod_data[key], path
                        ))
                elif base[key] != mod_data[key]:
                    diffs.append((path, base[key], mod_data[key]))
            elif key in base and key not in mod_data:
                # 删除：mod 中缺失此字段
                diffs.append((path, base[key], None))
    return diffs


def analyze_file_overrides(
    rel_path: str,
    base_data: dict,
    mod_data_list: list[tuple[str, str, dict]],
    schema: dict | None = None,
    field_path: list[str] | None = None,
) -> FileOverrideInfo:
    """
    分析单个文件的覆盖情况。

    参数:
        rel_path: 文件相对路径
        base_data: 游戏本体数据
        mod_data_list: [(mod_id, mod_name, mod_data), ...] 按优先级排序
        schema: 该文件对应的 schema 规则
        field_path: schema 根路径
    """
    info = FileOverrideInfo(rel_path=rel_path)
    info.mod_chain = [name for _, name, _ in mod_data_list]

    # 收集每个 mod 相对于 base 的差异
    field_map: dict[str, FieldOverride] = {}

    for _, mod_name, mod_data in mod_data_list:
        diffs = _collect_field_diffs(base_data, mod_data,
                                     schema=schema, field_path=field_path)
        for fp, base_val, mod_val in diffs:
            if base_val is None:
                info.new_entries.append((mod_name, f"新增: {fp}"))
                continue
            if mod_val is None:
                info.new_entries.append((mod_name, f"删除: {fp}"))
                continue
            if fp not in field_map:
                field_map[fp] = FieldOverride(
                    field_path=fp,
                    base_value=base_val
                )
            field_map[fp].mod_values.append((mod_name, mod_val))

    # 最终值 = 最后一个 mod 的值
    for fo in field_map.values():
        fo.final_value = fo.mod_values[-1][1] if fo.mod_values else fo.base_value

    info.field_overrides = list(field_map.values())
    return info


def analyze_all_overrides(
    game_config_path: Path,
    mod_configs: list[tuple[str, str, Path]],
    schema_dir: Path | None = None,
) -> list[FileOverrideInfo]:
    """
    分析所有文件的覆盖情况。

    参数:
        game_config_path: 游戏本体 config 目录
        mod_configs: [(mod_id, mod_name, mod_config_path), ...] 按优先级排序
        schema_dir: schema 规则文件目录

    返回:
        FileOverrideInfo 列表
    """
    schemas = load_schemas(schema_dir) if schema_dir else {}
    all_files = collect_mod_files(mod_configs)

    results = []
    for rel_path, mod_file_list in sorted(all_files.items()):
        base_file = game_config_path / rel_path
        if base_file.exists():
            try:
                base_data = load_json(base_file)
            except json.JSONDecodeError as e:
                msg = f"{base_file}: JSON 解析失败，已跳过 ({e.msg})"
                log.warning(msg)
                diag.warn("parse", msg)
                continue
        else:
            diag.warn("parse", f"{rel_path}: 游戏本体中不存在此文件，视为 Mod 新增")
            base_data = {}

        mod_data_list = []
        for mod_id, mod_name, mod_file in mod_file_list:
            try:
                mod_data_list.append((mod_id, mod_name, load_json(mod_file)))
            except json.JSONDecodeError as e:
                msg = f"{mod_file}: JSON 解析失败，已跳过 ({e.msg})"
                log.warning(msg)
                diag.warn("parse", msg)

        # 查找 schema
        schema = resolve_schema(rel_path, schemas) if schemas else None
        root_key = get_schema_root_key(schema) if schema else None
        schema_path = [root_key] if root_key else None

        info = analyze_file_overrides(rel_path, base_data, mod_data_list,
                                       schema=schema, field_path=schema_path)
        results.append(info)

    return results
