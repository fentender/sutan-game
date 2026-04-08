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
from .array_match import find_matching_item, resolve_duplicates, get_key_vals
from .profiler import profile

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

def _make_element_label(item, match_keys):
    """为数组元素生成可读标识"""
    if not isinstance(item, dict):
        return str(item)
    parts = []
    for key in match_keys:
        if key in item:
            val_str = str(item[key])
            if len(val_str) > 20:
                val_str = val_str[:20] + "..."
            parts.append(f"{key}={val_str}")
    if parts:
        return ", ".join(parts)
    return f"#{hash(json.dumps(item, sort_keys=True, ensure_ascii=False)) % 10000}"


# ==================== 差异收集 ====================

def _collect_smart_match_diffs(base_arr, mod_arr, path,
                               match_keys, schema, field_path):
    """可匹配数组的原子化比较：按 match_keys 精确匹配元素后递归比较子字段。
    同 key 多对多时用字符串相似度做全局最优配对。"""
    diffs = []
    matched_base = set()

    # 按 match_key 值分组 mod 元素
    mod_groups: dict[tuple | None, list[tuple[int, dict]]] = {}
    for i, mod_item in enumerate(mod_arr):
        if not isinstance(mod_item, dict):
            continue
        kv = get_key_vals(mod_item, match_keys)
        mod_groups.setdefault(kv, []).append((i, mod_item))

    # 按 match_key 值分组 base 元素
    base_groups: dict[tuple, list[int]] = {}
    for i, base_item in enumerate(base_arr):
        if isinstance(base_item, dict):
            kv = get_key_vals(base_item, match_keys)
            if kv is not None:
                base_groups.setdefault(kv, []).append(i)

    # 逐组配对
    for kv, mod_items in mod_groups.items():
        if kv is None:
            for _, mod_item in mod_items:
                elem_label = _make_element_label(mod_item, match_keys)
                diffs.append((f"{path}[{elem_label}]", None, mod_item))
            continue

        base_candidates = [i for i in base_groups.get(kv, []) if i not in matched_base]

        if not base_candidates:
            # 全部新增
            for _, mod_item in mod_items:
                elem_label = _make_element_label(mod_item, match_keys)
                diffs.append((f"{path}[{elem_label}]", None, mod_item))
            continue

        # 全局最优配对
        pairs, unmatched = resolve_duplicates(mod_items, base_arr, base_candidates)

        for _, mod_item, base_idx in pairs:
            matched_base.add(base_idx)
            elem_label = _make_element_label(mod_item, match_keys)
            elem_path = f"{path}[{elem_label}]"
            diffs.extend(_collect_field_diffs(
                base_arr[base_idx], mod_item, elem_path,
                schema, field_path,
            ))

        for _, mod_item in unmatched:
            elem_label = _make_element_label(mod_item, match_keys)
            diffs.append((f"{path}[{elem_label}]", None, mod_item))

    # base 中未匹配的 = 删除
    for i, base_item in enumerate(base_arr):
        if i not in matched_base:
            elem_label = _make_element_label(base_item, match_keys)
            diffs.append((f"{path}[{elem_label}]", base_item, None))

    return diffs


def _is_obj_array(arr) -> bool:
    """判断是否是非空对象数组"""
    return isinstance(arr, list) and bool(arr) and all(isinstance(x, dict) for x in arr)


def _collect_index_match_diffs(base_arr, mod_arr, path, schema, field_path):
    """按序号位置对应的 array<object> 深度比较。
    mod[i] ↔ base[i]，超出部分为新增/删除。"""
    diffs = []
    min_len = min(len(base_arr), len(mod_arr))

    for i in range(min_len):
        elem_path = f"{path}[{i}]"
        if isinstance(base_arr[i], dict) and isinstance(mod_arr[i], dict):
            diffs.extend(_collect_field_diffs(
                base_arr[i], mod_arr[i], elem_path, schema, field_path
            ))
        elif base_arr[i] != mod_arr[i]:
            diffs.append((elem_path, base_arr[i], mod_arr[i]))

    # mod 多出的 = 新增
    for i in range(min_len, len(mod_arr)):
        diffs.append((f"{path}[{i}]", None, mod_arr[i]))

    # base 多出的 = 删除
    for i in range(min_len, len(base_arr)):
        diffs.append((f"{path}[{i}]", base_arr[i], None))

    return diffs


@profile
def _collect_field_diffs(
    base: object, mod_data: object, prefix: str = "",
    schema: dict | None = None, field_path: list[str] | None = None,
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
        all_keys = base.keys() | mod_data.keys()
        for key in all_keys:
            path = f"{prefix}{SEP}{key}" if prefix else key
            child_path = field_path + [key] if field_path else None

            if key in mod_data and key not in base:
                diffs.append((path, None, mod_data[key]))
            elif key in mod_data and key in base:
                if isinstance(base[key], dict) and isinstance(mod_data[key], dict):
                    diffs.extend(_collect_field_diffs(
                        base[key], mod_data[key], path,
                        schema, child_path,
                    ))
                elif isinstance(base[key], list) and isinstance(mod_data[key], list):
                    # 从 schema 判断数组合并策略
                    child_def = get_field_def(schema, child_path) if schema and child_path else None
                    merge_strategy = child_def.get("merge") if child_def else None
                    match_keys = child_def.get("match_key") if child_def else None

                    if merge_strategy == "smart_match" and match_keys:
                        diffs.extend(_collect_smart_match_diffs(
                            base[key], mod_data[key], path,
                            match_keys, schema, child_path,
                        ))
                    elif _is_obj_array(base[key]) and _is_obj_array(mod_data[key]):
                        # 对象数组：按序号位置深度比较
                        diffs.extend(_collect_index_match_diffs(
                            base[key], mod_data[key], path,
                            schema, child_path,
                        ))
                    elif base[key] != mod_data[key]:
                        diffs.append((path, base[key], mod_data[key]))
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


@profile
def analyze_all_overrides(
    game_config_path: Path,
    mod_configs: list[tuple[str, str, Path]],
    schema_dir: Path | None = None,
    cancel_check=None,
) -> list[FileOverrideInfo]:
    """
    分析所有文件的覆盖情况。

    参数:
        game_config_path: 游戏本体 config 目录
        mod_configs: [(mod_id, mod_name, mod_config_path), ...] 按优先级排序
        schema_dir: schema 规则文件目录
        cancel_check: 可选的取消检查回调，调用时若已取消则抛出异常
    返回:
        FileOverrideInfo 列表
    """
    schemas = load_schemas(schema_dir) if schema_dir else {}
    all_files = collect_mod_files(mod_configs)

    results = []
    for rel_path, mod_file_list in sorted(all_files.items()):
        if cancel_check:
            cancel_check()
        base_file = game_config_path / rel_path
        if base_file.exists():
            try:
                base_data = load_json(base_file, readonly=True)
            except json.JSONDecodeError as e:
                msg = f"{base_file}: JSON 解析失败，已跳过 ({e.msg})"
                log.warning(msg)
                diag.warn("parse", msg)
                continue
        else:
            diag.info("parse", f"{rel_path}: 游戏本体中不存在此文件，视为 Mod 新增")
            base_data = {}

        mod_data_list = []
        for mod_id, mod_name, mod_file in mod_file_list:
            try:
                mod_data_list.append((mod_id, mod_name, load_json(mod_file, readonly=True)))
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
