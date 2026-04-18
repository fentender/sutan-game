"""
ID 重分配模块 - 检测并解决多个 Mod 之间的 ID 冲突

在合并前扫描所有 mod，找出被多个 mod 定义的相同 ID，
为冲突的 ID 分配新值，并在 store 中原地替换所有引用。
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

from .diagnostics import diag
from .json_parser import DupList
from .types import CancelCheck

# 各类型 ID 的分配范围（从起始值向上递增）
ID_ALLOC_START: dict[str, int | None] = {
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
FILE_BASED_TYPES: dict[str, str] = {
    "rite": "rite",
    "event": "event",
    "loot": "loot",
    "rite_template": "rite_template",
}

# dictionary 类型实体及其对应的文件名
DICT_BASED_TYPES: dict[str, str] = {
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
        for mapping in [
            self.cards, self.rite, self.event, self.over,
            self.loot, self.rite_template, self.rite_template_mappings,
        ]:
            for old_str, new_str in mapping.items():
                lookup[int(old_str)] = int(new_str)
        for old_int, new_int in self.tag_ids.items():
            lookup[old_int] = new_int
        return lookup

    def build_str_lookup(self) -> dict[str, str]:
        """构建字符串 ID 的查找表，用于替换 DSL 表达式和字符串中的 ID"""
        lookup: dict[str, str] = {}
        for mapping in [
            self.cards, self.rite, self.event, self.over,
            self.loot, self.rite_template, self.rite_template_mappings,
        ]:
            for old_str, new_str in mapping.items():
                lookup[old_str] = new_str
        for old_int, new_int in self.tag_ids.items():
            lookup[str(old_int)] = str(new_int)
        return lookup


# ==================== ID 收集 ====================

def collect_base_ids() -> dict[str, set[str]]:
    """收集游戏本体的所有 ID（从 JsonStore 读取）"""
    from .json_store import JsonStore
    store = JsonStore.instance()

    base_ids: dict[str, set[str]] = {
        "cards": set(), "tag": set(), "tag_id": set(),
        "rite": set(), "event": set(), "over": set(),
        "loot": set(), "rite_template": set(),
        "rite_template_mappings": set(),
    }

    # dictionary 类型
    for entity_type, filename in DICT_BASED_TYPES.items():
        data = store.get_base(filename)
        if data:
            base_ids[entity_type] = set(data.keys())
            if entity_type == "tag":
                for _code, tag_data in data.items():
                    if isinstance(tag_data, dict) and "id" in tag_data:
                        base_ids["tag_id"].add(str(tag_data["id"]))

    # 文件名即 ID 的类型（从 store 的 base rel_paths 中过滤）
    for entity_type, dirname in FILE_BASED_TYPES.items():
        prefix = dirname + "/"
        for rel_path in store.base_rel_paths():
            if rel_path.startswith(prefix) and rel_path.endswith(".json"):
                stem = rel_path[len(prefix):-5]
                if "/" not in stem:  # 只取直接子目录下的文件
                    base_ids[entity_type].add(stem)

    return base_ids


@dataclass
class ModIdInfo:
    """单个 mod 的 ID 信息"""
    # dictionary 类型：{id_str: data_dict}
    cards: dict[str, dict[str, object]] = field(default_factory=dict)
    tag: dict[str, dict[str, object]] = field(default_factory=dict)      # {code: tag_data}
    over: dict[str, dict[str, object]] = field(default_factory=dict)
    rite_template_mappings: dict[str, dict[str, object]] = field(default_factory=dict)
    # 文件类型：{id_str: rel_path}
    rite: dict[str, str] = field(default_factory=dict)
    event: dict[str, str] = field(default_factory=dict)
    loot: dict[str, str] = field(default_factory=dict)
    rite_template: dict[str, str] = field(default_factory=dict)


def collect_mod_ids(mod_id: str) -> ModIdInfo:
    """收集单个 mod 的所有 ID 定义（从 JsonStore 读取）"""
    from .json_store import JsonStore
    store = JsonStore.instance()

    info = ModIdInfo()

    # dictionary 类型
    if store.has_mod(mod_id, "cards.json"):
        data = store.get_mod(mod_id, "cards.json")
        info.cards = {k: v for k, v in data.items() if isinstance(v, dict)}

    if store.has_mod(mod_id, "tag.json"):
        data = store.get_mod(mod_id, "tag.json")
        info.tag = {k: v for k, v in data.items() if isinstance(v, dict)}

    if store.has_mod(mod_id, "over.json"):
        data = store.get_mod(mod_id, "over.json")
        info.over = {k: v for k, v in data.items() if isinstance(v, dict)}

    if store.has_mod(mod_id, "rite_template_mappings.json"):
        data = store.get_mod(mod_id, "rite_template_mappings.json")
        info.rite_template_mappings = {
            k: v for k, v in data.items() if isinstance(v, dict)
        }

    # 文件类型：从 store 的文件列表中过滤
    for entity_type, dirname in FILE_BASED_TYPES.items():
        mapping: dict[str, str] = {}
        prefix = dirname + "/"
        for rel_path in store.mod_files(mod_id):
            if rel_path.startswith(prefix) and rel_path.endswith(".json"):
                stem = rel_path[len(prefix):-5]
                if "/" not in stem:
                    mapping[stem] = rel_path
        setattr(info, entity_type, mapping)

    return info


# ==================== 冲突检测 ====================

def _detect_dict_conflicts(
    entity_type: str,
    base_ids: set[str],
    mod_ids_list: list[dict[str, object]],
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
    mod_tag_list: list[dict[str, dict[str, object]]],
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
                code_to_names.setdefault(code, set()).add(str(name) if name is not None else "")

    # 相同 code 但 name 完全一致 → 不冲突
    code_conflicts: dict[str, list[int]] = {}
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

    id_conflicts: dict[str, list[int]] = {}
    for id_str, entries in id_to_mods.items():
        mod_indices = list({mod_idx for mod_idx, _ in entries})
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
        mod_dicts: list[dict[str, object]] = [getattr(m, entity_type) for m in mod_ids_list]
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
    mod_tags: list[dict[str, dict[str, object]]] = [m.tag for m in mod_ids_list]
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
                for _code, data in mod_info.tag.items():
                    if isinstance(data, dict) and "id" in data:
                        used["tag_id"].add(str(data["id"]))
            elif k == "tag":
                used["tag"].update(mod_info.tag.keys())
            elif hasattr(mod_info, k):
                attr = getattr(mod_info, k)
                if isinstance(attr, dict):
                    used[k].update(attr.keys())

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

        start_val = ID_ALLOC_START.get(alloc_key, 9000000)
        start = start_val if start_val is not None else 9000000

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

    def replacer(match: re.Match[str]) -> str:
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


def replace_in_value(
    value: object,
    int_lookup: dict[int, int],
    str_lookup: dict[str, str],
) -> object:
    """递归替换 JSON value 中的 ID 引用"""
    if isinstance(value, dict):
        return {
            _replace_in_key(k, str_lookup): replace_in_value(v, int_lookup, str_lookup)
            for k, v in value.items()
        }
    if isinstance(value, DupList):
        # 保留 DupList 类型（同名重复键的值集合）
        return DupList(replace_in_value(item, int_lookup, str_lookup) for item in value)
    if isinstance(value, list):
        return [replace_in_value(item, int_lookup, str_lookup) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return _replace_int_id(value, int_lookup)
    if isinstance(value, str):
        return _replace_ids_in_string(value, str_lookup)
    return value


def apply_remap_to_store(mod_id: str, remap: RemapTable) -> None:
    """将 remap 应用到 store 中该 mod 的所有 JSON 数据（原地更新）。

    遍历该 mod 在 store 中的所有文件，替换 ID 引用并更新 store。
    文件名即 ID 的类型（如 rite/XXXXX.json）会同时更新 rel_path。
    """
    from .json_store import JsonStore
    store = JsonStore.instance()

    int_lookup = remap.build_int_lookup()
    str_lookup = remap.build_str_lookup()

    for rel_path in list(store.mod_files(mod_id)):
        data = store.get_mod(mod_id, rel_path)

        # 计算可能的新 rel_path（文件名即 ID 的情况）
        new_rel = _compute_new_rel_path(rel_path, remap)

        # 处理 dictionary 文件的顶层 key 替换
        data = _remap_dict_keys(rel_path, data, remap)

        # 递归替换所有 value 中的 ID 引用
        replaced = replace_in_value(data, int_lookup, str_lookup)
        if isinstance(replaced, dict):
            data = replaced

        # 如果 rel_path 变了，先删旧的
        if new_rel != rel_path:
            store.remove_mod_file(mod_id, rel_path)

        store.set_mod(mod_id, new_rel, data)


def _compute_new_rel_path(rel_str: str, remap: RemapTable) -> str:
    """计算替换后的文件相对路径（处理文件名即 ID 的情况）"""
    parts = rel_str.split("/")

    # rite/XXXXX.json, event/XXXXX.json, loot/XXXXX.json, rite_template/XXXXX.json
    if len(parts) == 2 and parts[1].endswith(".json"):
        dirname = parts[0]
        stem = parts[1][:-5]  # 去掉 .json
        type_map: dict[str, dict[str, str]] = {
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


def _split_stem_ext(filename: str) -> tuple[str, str]:
    """分离文件名和扩展名，如 '2020000.PNG' -> ('2020000', '.PNG')"""
    dot_idx = filename.rfind(".")
    if dot_idx < 0:
        return filename, ""
    return filename[:dot_idx], filename[dot_idx:]


def compute_resource_rename(rel_str: str, remap: RemapTable) -> str:
    """
    计算资源文件重映射后的目标路径。

    处理以下资源文件：
    - image/cards/{card_id}.ext 和 image/cards/{card_id}_{suffix}.ext
    - image/head/{card_id}.ext 和 image/head/{card_id}_{suffix}.ext
    - image/tag/tag_{tag_id}.ext

    不匹配时原样返回。
    """
    parts = rel_str.split("/")

    # image/cards/ 和 image/head/ 中的 card ID 图片
    if len(parts) == 3 and parts[0] == "image" and parts[1] in ("cards", "head"):
        stem, ext = _split_stem_ext(parts[2])
        for old_id, new_id in remap.cards.items():
            if stem == old_id:
                return f"image/{parts[1]}/{new_id}{ext}"
            if stem.startswith(old_id + "_"):
                new_stem = new_id + stem[len(old_id):]
                return f"image/{parts[1]}/{new_stem}{ext}"

    # image/tag/tag_{id}.ext 中的 tag ID 图片
    if len(parts) == 3 and parts[0] == "image" and parts[1] == "tag":
        stem, ext = _split_stem_ext(parts[2])
        if stem.startswith("tag_"):
            try:
                tag_id = int(stem[4:])
            except ValueError:
                return rel_str
            if tag_id in remap.tag_ids:
                return f"image/tag/tag_{remap.tag_ids[tag_id]}{ext}"

    return rel_str


def _remap_dict_keys(rel_str: str, data: dict[str, object], remap: RemapTable) -> dict[str, object]:
    """替换 dictionary 文件的顶层 key"""
    if not isinstance(data, dict):
        return data

    # cards.json
    if rel_str == "cards.json" and remap.cards:
        new_data: dict[str, object] = {}
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
                if "id" in value and isinstance(value["id"], int) and value["id"] in remap.tag_ids:
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
    mod_configs: list[tuple[str, str, Path]],
    cancel_check: CancelCheck | None = None,
) -> tuple[list[str], dict[str, RemapTable]]:
    """
    检测 ID 冲突并重分配，直接更新 store 中的数据。

    参数:
        mod_configs: [(mod_id, mod_name, mod_config_path), ...] 按优先级排序
        cancel_check: 可选的取消检查回调

    返回:
        (remap_messages, remap_tables)
        - remap_messages: 日志消息列表
        - remap_tables: {mod_id: RemapTable} 各 mod 的重映射表
    """
    messages: list[str] = []

    if not mod_configs:
        return messages, {}

    # 1. 收集本体 ID
    base_ids = collect_base_ids()
    if cancel_check:
        cancel_check()

    # 2. 收集各 mod 的 ID
    mod_ids_list: list[ModIdInfo] = []
    for mod_id, _, _ in mod_configs:
        mod_ids_list.append(collect_mod_ids(mod_id))
    if cancel_check:
        cancel_check()

    # 3. 检测冲突
    conflicts = detect_conflicts(base_ids, mod_ids_list)
    if not conflicts:
        return messages, {}

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

    # 5. 为每个有冲突的 mod 构建 remap table 并应用到 store
    remap_tables: dict[str, RemapTable] = {}
    mods_needing_remap: set[int] = set()
    for (mod_idx, _, _) in remap:
        mods_needing_remap.add(mod_idx)

    for mod_idx in sorted(mods_needing_remap):
        mod_id, mod_name, _ = mod_configs[mod_idx]
        table = build_remap_table(remap, mod_idx, mod_ids_list[mod_idx])

        if table.is_empty():
            continue

        # 日志输出每个重分配
        for old_id, new_id in table.cards.items():
            name = ""
            card_data = mod_ids_list[mod_idx].cards.get(old_id, {})
            if isinstance(card_data, dict):
                name_val = card_data.get("name", "")
                name = str(name_val) if name_val is not None else ""
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

        for old_tag_id, new_tag_id in table.tag_ids.items():
            msg = f"ID 重分配: Mod [{mod_name}] tag id {old_tag_id} → {new_tag_id}"
            messages.append(msg)
            diag.info("remap", msg)

        # 直接更新 store 中的数据
        apply_remap_to_store(mod_id, table)
        remap_tables[mod_id] = table

        if cancel_check:
            cancel_check()

    return messages, remap_tables
