"""
冲突检测与覆盖链报告
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from .json_parser import load_json


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


def _collect_field_diffs(
    base: object, mod_data: object, prefix: str = ""
) -> list[tuple[str, object, object]]:
    """
    收集 mod 相对于 base 的字段差异。
    返回: [(field_path, base_value, mod_value), ...]
    """
    diffs = []

    if isinstance(base, dict) and isinstance(mod_data, dict):
        all_keys = set(list(base.keys()) + list(mod_data.keys()))
        for key in all_keys:
            path = f"{prefix}.{key}" if prefix else key
            if key in mod_data and key not in base:
                diffs.append((path, None, mod_data[key]))
            elif key in mod_data and key in base:
                if isinstance(base[key], dict) and isinstance(mod_data[key], dict):
                    diffs.extend(_collect_field_diffs(base[key], mod_data[key], path))
                elif isinstance(base[key], list) and isinstance(mod_data[key], list):
                    # 数组差异简化处理：只记录长度变化和内容是否不同
                    base_str = json.dumps(base[key], sort_keys=True, ensure_ascii=False)
                    mod_str = json.dumps(mod_data[key], sort_keys=True, ensure_ascii=False)
                    if base_str != mod_str:
                        diffs.append((path, f"[{len(base[key])}项]", f"[{len(mod_data[key])}项]"))
                elif base[key] != mod_data[key]:
                    diffs.append((path, base[key], mod_data[key]))
    return diffs


def analyze_file_overrides(
    rel_path: str,
    base_data: dict,
    mod_data_list: list[tuple[str, str, dict]]
) -> FileOverrideInfo:
    """
    分析单个文件的覆盖情况。

    参数:
        rel_path: 文件相对路径
        base_data: 游戏本体数据
        mod_data_list: [(mod_id, mod_name, mod_data), ...] 按优先级排序
    """
    info = FileOverrideInfo(rel_path=rel_path)
    info.mod_chain = [name for _, name, _ in mod_data_list]

    # 收集每个 mod 相对于 base 的差异
    field_map: dict[str, FieldOverride] = {}

    for _, mod_name, mod_data in mod_data_list:
        diffs = _collect_field_diffs(base_data, mod_data)
        for field_path, base_val, mod_val in diffs:
            if base_val is None:
                info.new_entries.append((mod_name, f"新增: {field_path}"))
                continue
            if field_path not in field_map:
                field_map[field_path] = FieldOverride(
                    field_path=field_path,
                    base_value=base_val
                )
            field_map[field_path].mod_values.append((mod_name, mod_val))

    # 最终值 = 最后一个 mod 的值
    for fo in field_map.values():
        fo.final_value = fo.mod_values[-1][1] if fo.mod_values else fo.base_value

    info.field_overrides = list(field_map.values())
    return info


def analyze_all_overrides(
    game_config_path: Path,
    mod_configs: list[tuple[str, str, Path]]
) -> list[FileOverrideInfo]:
    """
    分析所有文件的覆盖情况。

    参数:
        game_config_path: 游戏本体 config 目录
        mod_configs: [(mod_id, mod_name, mod_config_path), ...] 按优先级排序

    返回:
        FileOverrideInfo 列表
    """
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

    results = []
    for rel_path, mod_file_list in sorted(all_files.items()):
        base_file = game_config_path / rel_path
        base_data = load_json(base_file) if base_file.exists() else {}

        mod_data_list = []
        for mod_id, mod_name, mod_file in mod_file_list:
            mod_data_list.append((mod_id, mod_name, load_json(mod_file)))

        info = analyze_file_overrides(rel_path, base_data, mod_data_list)
        results.append(info)

    return results
