"""
ID 重分配模块 - 检测并解决多个 Mod 之间的 ID 冲突

在合并前扫描所有 mod，找出被多个 mod 定义的相同 ID，
为冲突的 ID 分配新值，并在 mod 数据中替换所有引用。
"""
import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .json_parser import load_json, dump_json
from .diagnostics import diag

log = logging.getLogger(__name__)

# 各类型 ID 的分配范围（从起始值向上递增）
ID_ALLOC_START = {
    "cards": 2900000,
    "tag_id": 3900000,
    "tag_code": None,         # tag code 通过加后缀生成
    "rite": 5090000,
    "event": 5390000,
    "over": 900,
    "loot": 6900000,
    "rite_template": 8090000,
    "rite_template_mappings": 8091000,
}

# 以文件名为 ID 的实体类型及其对应的目录名
FILE_BASED_TYPES = {
    "rite": "rite",
    "event": "event",
    "loot": "loot",
    "rite_template": "rite_template",
}

# dictionary 类型实体及其对应的文件名
DICT_BASED_TYPES = {
    "cards": "cards.json",
    "tag": "tag.json",
    "over": "over.json",
    "rite_template_mappings": "rite_template_mappings.json",
}


@dataclass
class RemapTable:
    """单个 mod 的 ID 替换映射表"""
    cards: dict[str, str] = field(default_factory=dict)
    tag_codes: dict[str, str] = field(default_factory=dict)
    tag_ids: dict[int, int] = field(default_factory=dict)
    rite: dict[str, str] = field(default_factory=dict)
    event: dict[str, str] = field(default_factory=dict)
    over: dict[str, str] = field(default_factory=dict)
    loot: dict[str, str] = field(default_factory=dict)
    rite_template: dict[str, str] = field(default_factory=dict)
    rite_template_mappings: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any([
            self.cards, self.tag_codes, self.tag_ids,
            self.rite, self.event, self.over,
            self.loot, self.rite_template, self.rite_template_mappings,
        ])

    def build_int_lookup(self) -> dict[int, int]:
        """构建整数 ID 的查找表（旧→新），用于快速替换 JSON 中的数字值"""
        lookup: dict[int, int] = {}
        for mapping, attr in [
            (self.cards, "cards"), (self.rite, "rite"),
            (self.event, "event"), (self.over, "over"),
            (self.loot, "loot"), (self.rite_template, "rite_template"),
            (self.rite_template_mappings, "rite_template_mappings"),
        ]:
            for old, new in mapping.items():
                lookup[int(old)] = int(new)
        for old, new in self.tag_ids.items():
            lookup[old] = new
        return lookup

    def build_str_lookup(self) -> dict[str, str]:
        """构建字符串 ID 的查找表，用于替换 DSL 表达式和字符串中的 ID"""
        lookup: dict[str, str] = {}
        for mapping in [
            self.cards, self.rite, self.event, self.over,
            self.loot, self.rite_template, self.rite_template_mappings,
        ]:
            for old, new in mapping.items():
                lookup[old] = new
        for old, new in self.tag_ids.items():
            lookup[str(old)] = str(new)
        return lookup


# ==================== ID 收集 ====================

def collect_base_ids(game_config_path: Path) -> dict[str, set[str]]:
    """收集游戏本体的所有 ID"""
    base_ids: dict[str, set[str]] = {
        "cards": set(), "tag": set(), "tag_id": set(),
        "rite": set(), "event": set(), "over": set(),
        "loot": set(), "rite_template": set(),
        "rite_template_mappings": set(),
    }

    # dictionary 类型
    for entity_type, filename in DICT_BASED_TYPES.items():
        filepath = game_config_path / filename
        if filepath.exists():
            try:
                data = load_json(filepath)
                base_ids[entity_type] = set(data.keys())
                if entity_type == "tag":
                    # 同时收集 tag 的数字 id
                    for code, tag_data in data.items():
                        if isinstance(tag_data, dict) and "id" in tag_data:
                            base_ids["tag_id"].add(str(tag_data["id"]))
            except json.JSONDecodeError:
                pass

    # 文件名即 ID 的类型
    for entity_type, dirname in FILE_BASED_TYPES.items():
        dirpath = game_config_path / dirname
        if dirpath.exists():
            for f in dirpath.iterdir():
                if f.is_file() and f.suffix.lower() == ".json":
                    base_ids[entity_type].add(f.stem)

    return base_ids


@dataclass
class ModIdInfo:
    """单个 mod 的 ID 信息"""
    # dictionary 类型：{id_str: data_dict}
    cards: dict[str, dict] = field(default_factory=dict)
    tag: dict[str, dict] = field(default_factory=dict)      # {code: tag_data}
    over: dict[str, dict] = field(default_factory=dict)
    rite_template_mappings: dict[str, dict] = field(default_factory=dict)
    # 文件类型：{id_str: file_path}
    rite: dict[str, Path] = field(default_factory=dict)
    event: dict[str, Path] = field(default_factory=dict)
    loot: dict[str, Path] = field(default_factory=dict)
    rite_template: dict[str, Path] = field(default_factory=dict)


def collect_mod_ids(mod_config_path: Path) -> ModIdInfo:
    """收集单个 mod 的所有 ID 定义"""
    info = ModIdInfo()

    # dictionary 类型
    cards_file = mod_config_path / "cards.json"
    if cards_file.exists():
        try:
            data = load_json(cards_file)
            info.cards = {k: v for k, v in data.items() if isinstance(v, dict)}
        except json.JSONDecodeError:
            pass

    tag_file = mod_config_path / "tag.json"
    if tag_file.exists():
        try:
            data = load_json(tag_file)
            info.tag = {k: v for k, v in data.items() if isinstance(v, dict)}
        except json.JSONDecodeError:
            pass

    over_file = mod_config_path / "over.json"
    if over_file.exists():
        try:
            data = load_json(over_file)
            info.over = {k: v for k, v in data.items() if isinstance(v, dict)}
        except json.JSONDecodeError:
            pass

    mappings_file = mod_config_path / "rite_template_mappings.json"
    if mappings_file.exists():
        try:
            data = load_json(mappings_file)
            info.rite_template_mappings = {
                k: v for k, v in data.items() if isinstance(v, dict)
            }
        except json.JSONDecodeError:
            pass

    # 文件类型
    for entity_type, dirname in FILE_BASED_TYPES.items():
        dirpath = mod_config_path / dirname
        if dirpath.exists():
            mapping = {}
            for f in dirpath.iterdir():
                if f.is_file() and f.suffix.lower() == ".json":
                    mapping[f.stem] = f
            setattr(info, entity_type, mapping)

    return info


# ==================== 冲突检测 ====================

def _detect_dict_conflicts(
    entity_type: str,
    base_ids: set[str],
    mod_ids_list: list[dict],
) -> dict[str, list[int]]:
    """检测 dictionary 类型实体的 ID 冲突"""
    # 收集每个非本体 ID 被哪些 mod 定义
    id_to_mods: dict[str, list[int]] = {}
    for mod_idx, mod_ids in enumerate(mod_ids_list):
        for id_str in mod_ids:
            if id_str not in base_ids:
                id_to_mods.setdefault(id_str, []).append(mod_idx)
    return {k: v for k, v in id_to_mods.items() if len(v) > 1}


def _detect_tag_conflicts(
    base_ids: set[str],
    base_tag_ids: set[str],
    mod_tag_list: list[dict[str, dict]],
) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """
    检测 tag 冲突，返回 (code_conflicts, id_conflicts)。
    code 冲突：相同 code 但不同 name（同 code 同 name 视为同一 tag）。
    id 冲突：不同 code 但相同数字 id。
    """
    # code 冲突检测
    code_to_mods: dict[str, list[int]] = {}
    code_to_names: dict[str, set[str]] = {}
    for mod_idx, mod_tags in enumerate(mod_tag_list):
        for code, tag_data in mod_tags.items():
            if code not in base_ids:
                code_to_mods.setdefault(code, []).append(mod_idx)
                name = tag_data.get("name", "") if isinstance(tag_data, dict) else ""
                code_to_names.setdefault(code, set()).add(name)

    # 相同 code 但 name 完全一致 → 不冲突
    code_conflicts = {}
    for code, mods in code_to_mods.items():
        if len(mods) > 1 and len(code_to_names.get(code, set())) > 1:
            code_conflicts[code] = mods

    # id 数字冲突检测
    id_to_mods: dict[str, list[tuple[int, str]]] = {}  # id_str -> [(mod_idx, code)]
    for mod_idx, mod_tags in enumerate(mod_tag_list):
        for code, tag_data in mod_tags.items():
            if isinstance(tag_data, dict) and "id" in tag_data:
                id_str = str(tag_data["id"])
                if id_str not in base_tag_ids:
                    id_to_mods.setdefault(id_str, []).append((mod_idx, code))

    id_conflicts = {}
    for id_str, entries in id_to_mods.items():
        # 不同 mod 的不同 code 使用了相同 id
        unique_mod_codes = set((mod_idx, code) for mod_idx, code in entries)
        mod_indices = list(set(mod_idx for mod_idx, _ in entries))
        if len(mod_indices) > 1:
            id_conflicts[id_str] = mod_indices

    return code_conflicts, id_conflicts


def detect_conflicts(
    base_ids: dict[str, set[str]],
    mod_ids_list: list[ModIdInfo],
) -> dict[str, dict[str, list[int]]]:
    """
    检测所有类型的 ID 冲突。
    返回 {entity_type: {id_str: [mod_indices]}}
    """
    conflicts: dict[str, dict[str, list[int]]] = {}

    # dictionary 类型（cards, over, rite_template_mappings）
    for entity_type in ("cards", "over", "rite_template_mappings"):
        mod_dicts = [getattr(m, entity_type) for m in mod_ids_list]
        result = _detect_dict_conflicts(entity_type, base_ids[entity_type], mod_dicts)
        if result:
            conflicts[entity_type] = result

    # 文件类型（rite, event, loot, rite_template）
    for entity_type in FILE_BASED_TYPES:
        mod_dicts = [getattr(m, entity_type) for m in mod_ids_list]
        result = _detect_dict_conflicts(entity_type, base_ids[entity_type], mod_dicts)
        if result:
            conflicts[entity_type] = result

    # tag 特殊处理
    mod_tags = [m.tag for m in mod_ids_list]
    code_conflicts, id_conflicts = _detect_tag_conflicts(
        base_ids["tag"], base_ids.get("tag_id", set()), mod_tags
    )
    if code_conflicts:
        conflicts["tag_code"] = code_conflicts
    if id_conflicts:
        conflicts["tag_id"] = id_conflicts

    return conflicts


# ==================== ID 分配 ====================

def _collect_all_used_ids(
    base_ids: dict[str, set[str]],
    mod_ids_list: list[ModIdInfo],
) -> dict[str, set[str]]:
    """收集所有已使用的 ID（本体 + 所有 mod）"""
    used: dict[str, set[str]] = {k: set(v) for k, v in base_ids.items()}

    for mod_info in mod_ids_list:
        for k in used:
            if k == "tag_id":
                # 从 tag 数据中提取数字 id
                for code, data in mod_info.tag.items():
                    if isinstance(data, dict) and "id" in data:
                        used["tag_id"].add(str(data["id"]))
            elif k == "tag":
                used["tag"].update(mod_info.tag.keys())
            elif hasattr(mod_info, k):
                used[k].update(getattr(mod_info, k).keys())

    return used


def _next_available_id(start: int, used: set[str]) -> int:
    """从 start 开始找到下一个未使用的 ID"""
    candidate = start
    while str(candidate) in used:
        candidate += 1
    return candidate


def allocate_new_ids(
    conflicts: dict[str, dict[str, list[int]]],
    all_used: dict[str, set[str]],
    mod_count: int,
) -> dict[tuple[int, str, str], str]:
    """
    为冲突 ID 分配新值。
    冲突中优先级最高的 mod（索引最大）保留原 ID，其余重分配。
    返回 {(mod_index, entity_type, old_id): new_id}
    """
    remap: dict[tuple[int, str, str], str] = {}
    # 跟踪已分配的新 ID，避免分配重复
    newly_allocated: dict[str, set[str]] = {k: set() for k in all_used}

    for entity_type, id_conflicts in conflicts.items():
        # 确定分配类型 key
        alloc_key = entity_type
        if alloc_key == "tag_code":
            continue  # tag code 用后缀方式处理，不走数字分配

        start = ID_ALLOC_START.get(alloc_key, 9000000)

        for old_id, mod_indices in id_conflicts.items():
            # 优先级最高的 mod（索引最大）保留原 ID
            keeper = max(mod_indices)
            for mod_idx in sorted(mod_indices):
                if mod_idx == keeper:
                    continue
                # 分配新 ID
                combined_used = all_used.get(alloc_key, set()) | newly_allocated.get(alloc_key, set())
                new_id_int = _next_available_id(start, combined_used)
                new_id = str(new_id_int)
                remap[(mod_idx, entity_type, old_id)] = new_id
                newly_allocated.setdefault(alloc_key, set()).add(new_id)
                start = new_id_int + 1  # 下次从更大的值开始

    # tag code 冲突：通过加后缀解决
    if "tag_code" in conflicts:
        for old_code, mod_indices in conflicts["tag_code"].items():
            keeper = max(mod_indices)
            suffix_counter = 1
            for mod_idx in sorted(mod_indices):
                if mod_idx == keeper:
                    continue
                new_code = f"{old_code}_{suffix_counter}"
                combined_used = all_used.get("tag", set()) | newly_allocated.get("tag", set())
                while new_code in combined_used:
                    suffix_counter += 1
                    new_code = f"{old_code}_{suffix_counter}"
                remap[(mod_idx, "tag_code", old_code)] = new_code
                newly_allocated.setdefault("tag", set()).add(new_code)
                suffix_counter += 1

    return remap


def build_remap_table(
    remap: dict[tuple[int, str, str], str],
    mod_index: int,
    mod_ids: ModIdInfo,
) -> RemapTable:
    """为单个 mod 构建 RemapTable"""
    table = RemapTable()
    for (idx, entity_type, old_id), new_id in remap.items():
        if idx != mod_index:
            continue
        if entity_type == "cards":
            table.cards[old_id] = new_id
        elif entity_type == "tag_code":
            table.tag_codes[old_id] = new_id
        elif entity_type == "tag_id":
            table.tag_ids[int(old_id)] = int(new_id)
        elif entity_type == "rite":
            table.rite[old_id] = new_id
        elif entity_type == "event":
            table.event[old_id] = new_id
        elif entity_type == "over":
            table.over[old_id] = new_id
        elif entity_type == "loot":
            table.loot[old_id] = new_id
        elif entity_type == "rite_template":
            table.rite_template[old_id] = new_id
        elif entity_type == "rite_template_mappings":
            table.rite_template_mappings[old_id] = new_id
    return table


# ==================== ID 替换 ====================

# 匹配字符串中的 7 位数字 ID（有词边界）
_ID7_PATTERN = re.compile(r'(?<!\d)(\d{7})(?!\d)')
# 匹配字符串中 1-3 位数字（over ID），需要更精确的上下文
_OVER_ID_PATTERN = re.compile(r'(?<!\d)(\d{1,3})(?!\d)')


def _replace_ids_in_string(s: str, str_lookup: dict[str, str]) -> str:
    """替换字符串中所有匹配的 7 位数字 ID"""
    if not str_lookup:
        return s

    def replacer(match):
        id_str = match.group(1)
        return str_lookup.get(id_str, id_str)

    return _ID7_PATTERN.sub(replacer, s)


def _replace_int_id(value: int, int_lookup: dict[int, int]) -> int:
    """替换整数 ID"""
    return int_lookup.get(value, value)


def _replace_in_key(key: str, str_lookup: dict[str, str]) -> str:
    """替换 JSON key（DSL 表达式）中的 ID"""
    if not str_lookup:
        return key
    return _replace_ids_in_string(key, str_lookup)


def replace_in_value(value, int_lookup: dict[int, int], str_lookup: dict[str, str]):
    """递归替换 JSON value 中的 ID 引用"""
    if isinstance(value, dict):
        return {
            _replace_in_key(k, str_lookup): replace_in_value(v, int_lookup, str_lookup)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [replace_in_value(item, int_lookup, str_lookup) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return _replace_int_id(value, int_lookup)
    if isinstance(value, str):
        return _replace_ids_in_string(value, str_lookup)
    return value


def apply_remap_to_mod(
    mod_config_path: Path,
    remap: RemapTable,
    output_path: Path,
):
    """
    将 remap 应用到 mod 的所有文件，输出到 output_path。
    即使文件本身 ID 不冲突，其中引用的 ID 也可能需要替换。
    """
    int_lookup = remap.build_int_lookup()
    str_lookup = remap.build_str_lookup()

    if not mod_config_path.exists():
        return

    # 遍历 mod 的所有文件
    for src_file in mod_config_path.rglob("*"):
        if not src_file.is_file():
            continue

        rel = src_file.relative_to(mod_config_path)
        rel_str = str(rel).replace("\\", "/")

        if src_file.suffix.lower() != ".json":
            # 非 JSON 文件直接复制
            dst = output_path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst)
            continue

        # JSON 文件需要处理
        try:
            data = load_json(src_file)
        except json.JSONDecodeError:
            # 解析失败直接复制
            dst = output_path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst)
            continue

        # 确定输出文件名（可能需要重命名）
        new_rel = _compute_new_rel_path(rel_str, remap)
        dst = output_path / new_rel

        # 处理 dictionary 文件的顶层 key 替换
        data = _remap_dict_keys(rel_str, data, remap)

        # 递归替换所有 value 中的 ID 引用
        data = replace_in_value(data, int_lookup, str_lookup)

        dst.parent.mkdir(parents=True, exist_ok=True)
        dump_json(data, dst)


def _compute_new_rel_path(rel_str: str, remap: RemapTable) -> str:
    """计算替换后的文件相对路径（处理文件名即 ID 的情况）"""
    parts = rel_str.split("/")

    # rite/XXXXX.json, event/XXXXX.json, loot/XXXXX.json, rite_template/XXXXX.json
    if len(parts) == 2 and parts[1].endswith(".json"):
        dirname = parts[0]
        stem = parts[1][:-5]  # 去掉 .json
        type_map = {
            "rite": remap.rite,
            "event": remap.event,
            "loot": remap.loot,
            "rite_template": remap.rite_template,
        }
        mapping = type_map.get(dirname)
        if mapping and stem in mapping:
            return f"{dirname}/{mapping[stem]}.json"

    # after_story/XXXXX.json（card ID）
    if len(parts) == 2 and parts[0] == "after_story" and parts[1].endswith(".json"):
        stem = parts[1][:-5]
        if stem in remap.cards:
            return f"after_story/{remap.cards[stem]}.json"

    return rel_str


def _remap_dict_keys(rel_str: str, data: dict, remap: RemapTable) -> dict:
    """替换 dictionary 文件的顶层 key"""
    if not isinstance(data, dict):
        return data

    # cards.json
    if rel_str == "cards.json" and remap.cards:
        new_data = {}
        for key, value in data.items():
            new_key = remap.cards.get(key, key)
            if isinstance(value, dict) and "id" in value:
                # 同时更新内部的 id 字段
                if key in remap.cards:
                    value = dict(value)
                    value["id"] = int(new_key)
            new_data[new_key] = value
        return new_data

    # tag.json
    if rel_str == "tag.json" and (remap.tag_codes or remap.tag_ids):
        new_data = {}
        for key, value in data.items():
            new_key = remap.tag_codes.get(key, key)
            if isinstance(value, dict):
                value = dict(value)
                if "code" in value and key in remap.tag_codes:
                    value["code"] = new_key
                if "id" in value and value["id"] in remap.tag_ids:
                    value["id"] = remap.tag_ids[value["id"]]
            new_data[new_key] = value
        return new_data

    # over.json
    if rel_str == "over.json" and remap.over:
        new_data = {}
        for key, value in data.items():
            new_key = remap.over.get(key, key)
            new_data[new_key] = value
        return new_data

    # rite_template_mappings.json
    if rel_str == "rite_template_mappings.json" and remap.rite_template_mappings:
        new_data = {}
        for key, value in data.items():
            new_key = remap.rite_template_mappings.get(key, key)
            if isinstance(value, dict) and "id" in value and key in remap.rite_template_mappings:
                value = dict(value)
                value["id"] = int(new_key)
            new_data[new_key] = value
        return new_data

    return data


# ==================== 主入口 ====================

def remap_mod_configs(
    game_config_path: Path,
    mod_configs: list[tuple[str, str, Path]],
    temp_dir: Path,
    cancel_check=None,
) -> tuple[list[tuple[str, str, Path]], list[str]]:
    """
    检测 ID 冲突并重分配。

    参数:
        game_config_path: 游戏本体 config 目录
        mod_configs: [(mod_id, mod_name, mod_config_path), ...] 按优先级排序
        temp_dir: 临时目录（存放重分配后的 mod 数据）
        cancel_check: 可选的取消检查回调

    返回:
        (new_mod_configs, remap_messages)
        - new_mod_configs: 更新后的 mod_configs（有冲突的 mod 指向临时目录）
        - remap_messages: 日志消息列表
    """
    messages: list[str] = []

    if not mod_configs:
        return mod_configs, messages

    # 1. 收集本体 ID
    base_ids = collect_base_ids(game_config_path)
    if cancel_check:
        cancel_check()

    # 2. 收集各 mod 的 ID
    mod_ids_list: list[ModIdInfo] = []
    for _, _, config_path in mod_configs:
        mod_ids_list.append(collect_mod_ids(config_path))
    if cancel_check:
        cancel_check()

    # 3. 检测冲突
    conflicts = detect_conflicts(base_ids, mod_ids_list)
    if not conflicts:
        return mod_configs, messages

    # 汇总冲突信息
    type_counts = {t: len(ids) for t, ids in conflicts.items()}
    total = sum(type_counts.values())
    summary_parts = [f"{t}: {c}" for t, c in sorted(type_counts.items())]
    summary = f"ID 冲突检测: 发现 {total} 个冲突 ({', '.join(summary_parts)})"
    messages.append(summary)
    diag.info("remap", summary)

    # 4. 分配新 ID
    all_used = _collect_all_used_ids(base_ids, mod_ids_list)
    remap = allocate_new_ids(conflicts, all_used, len(mod_configs))
    if cancel_check:
        cancel_check()

    # 5. 为每个有冲突的 mod 构建 remap table 并应用
    new_mod_configs = list(mod_configs)
    mods_needing_remap: set[int] = set()
    for (mod_idx, _, _) in remap:
        mods_needing_remap.add(mod_idx)

    for mod_idx in sorted(mods_needing_remap):
        mod_id, mod_name, config_path = mod_configs[mod_idx]
        table = build_remap_table(remap, mod_idx, mod_ids_list[mod_idx])

        if table.is_empty():
            continue

        # 日志输出每个重分配
        for old_id, new_id in table.cards.items():
            name = ""
            card_data = mod_ids_list[mod_idx].cards.get(old_id, {})
            if isinstance(card_data, dict):
                name = card_data.get("name", "")
            suffix = f" ({name})" if name else ""
            msg = f"ID 重分配: Mod [{mod_name}] card {old_id} → {new_id}{suffix}"
            messages.append(msg)
            diag.info("remap", msg)

        for entity_type in ("rite", "event", "loot", "over",
                            "rite_template", "rite_template_mappings"):
            mapping = getattr(table, entity_type)
            for old_id, new_id in mapping.items():
                msg = f"ID 重分配: Mod [{mod_name}] {entity_type} {old_id} → {new_id}"
                messages.append(msg)
                diag.info("remap", msg)

        for old_code, new_code in table.tag_codes.items():
            msg = f"ID 重分配: Mod [{mod_name}] tag code {old_code} → {new_code}"
            messages.append(msg)
            diag.info("remap", msg)

        for old_id, new_id in table.tag_ids.items():
            msg = f"ID 重分配: Mod [{mod_name}] tag id {old_id} → {new_id}"
            messages.append(msg)
            diag.info("remap", msg)

        # 写入临时目录
        mod_temp = temp_dir / mod_id
        apply_remap_to_mod(config_path, table, mod_temp)

        # 更新 mod_configs 指向临时目录
        new_mod_configs[mod_idx] = (mod_id, mod_name, mod_temp)

        if cancel_check:
            cancel_check()

    return new_mod_configs, messages
