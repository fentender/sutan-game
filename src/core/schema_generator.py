"""
从游戏本体 config 目录自动生成 schema 规则文件。

核心入口:
    generate_all(config_dir, output_dir, progress_callback=None)
    ensure_schemas(config_dir, schema_dir) — 启动时自动检查并生成
"""
import json
import os
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from .json_parser import load_json
from .type_utils import get_type_str, classify_json
from .dsl_patterns import classify_dsl_key
from .diagnostics import diag

# 动态 key 阈值：同名字段聚合后子 key 数量超过此值判定为动态字典
DYNAMIC_KEY_THRESHOLD = 100

# 路径分隔符（内部使用，避免和 JSON key 中的点号冲突）
SEP = '\x01'
ARR_MARKER = '[]'

from ..config import SCHEMA_DIR

# 字段模板等价映射（硬编码）
# exact: 精确匹配字段名 → 规范名
# regex: 正则匹配字段名 → 规范名
_template_exact = {
    "any": "condition",
    "all": "condition",
    "result": "action",
    "choose": "action",
    "success": "action",
    "failed": "action",
}
_template_regex = [
    (re.compile(r"^case:op\d+$"), "action"),
    (re.compile(r"^s\d+$"), "slot"),
    (re.compile(r"^-?\d+$"), "music_entry"),
]


def _canonical_field_name(field_name):
    """获取字段的规范模板名（用于聚合等价字段）"""
    if field_name in _template_exact:
        return _template_exact[field_name]
    for regex, canonical in _template_regex:
        if regex.match(field_name):
            return canonical
    return field_name


# 全局字段信息收集器（按规范字段名聚合，用于最终报告）
_global_field_info = {}  # canonical_name → {child_keys, child_key_counts, child_key_types, count, paths}


def _ensure_global_entry(canonical):
    """确保 _global_field_info 中存在指定条目"""
    if canonical not in _global_field_info:
        _global_field_info[canonical] = {
            "child_keys": set(),
            "child_key_counts": {},
            "child_key_types": {},
            "elem_child_key_counts": {},
            "elem_child_key_types": {},
            "count": 0,
            "paths": set(),
        }
    g = _global_field_info[canonical]
    if "elem_child_key_counts" not in g:
        g["elem_child_key_counts"] = {}
        g["elem_child_key_types"] = {}
    return g


def _accumulate_global_info(info):
    """将单次 collect_field_info 结果累积到全局字段信息中（按规范字段名聚合）"""
    for path, entry in info.items():
        if not entry.get("child_keys"):
            continue
        field_name = path.split(SEP)[-1]
        canonical = _canonical_field_name(field_name)
        g = _ensure_global_entry(canonical)
        g["child_keys"].update(entry["child_keys"])
        g["count"] += entry["count"]
        g["paths"].add(path.replace(SEP, " → "))
        for ck, cnt in entry.get("child_key_counts", {}).items():
            g["child_key_counts"][ck] = g["child_key_counts"].get(ck, 0) + cnt
        for ck, types in entry.get("child_key_types", {}).items():
            if ck not in g["child_key_types"]:
                g["child_key_types"][ck] = {}
            for t, tc in types.items():
                g["child_key_types"][ck][t] = g["child_key_types"][ck].get(t, 0) + tc

    # 收集 array<object> 字段的元素子字段信息
    for path, entry in info.items():
        parts = path.split(SEP)
        for i in range(len(parts)):
            if parts[i] != ARR_MARKER or i == 0:
                continue
            # parts[i-1] 是 array 字段名，检查 parts[i+1] 是否为直接子字段
            if i + 1 != len(parts) - 1:
                continue
            arr_field = parts[i - 1]
            child_key = parts[i + 1]
            canonical = _canonical_field_name(arr_field)
            g = _ensure_global_entry(canonical)
            g["elem_child_key_counts"][child_key] = (
                g["elem_child_key_counts"].get(child_key, 0) + entry["count"]
            )
            if child_key not in g["elem_child_key_types"]:
                g["elem_child_key_types"][child_key] = {}
            for t in entry["types"]:
                g["elem_child_key_types"][child_key][t] = (
                    g["elem_child_key_types"][child_key].get(t, 0) + entry["count"]
                )


# ==================== 类型分析 ====================


def analyze_value_type(v):
    """分析单个值的详细类型（包括数组元素类型）"""
    base = get_type_str(v)
    if base == "array" and v:
        elem_types = {get_type_str(item) for item in v}
        if len(elem_types) == 1:
            et = elem_types.pop()
            return f"array<{et}>"
        # 多类型数组：记录所有元素类型，按字母排序保证稳定性
        return f"array<{','.join(sorted(elem_types))}>"
    return base


def collect_field_info(obj, info, prefix="", _visited=None):
    """
    递归分析对象结构，收集每个路径的类型信息和值样本。

    info[path] = {
        "types": set(),          # 观察到的类型集合
        "child_keys": set(),     # 如果是 object，记录子 key 名
        "array_elem_types": set(), # 如果是 array，记录元素类型
        "has_guid": bool,        # array<object> 元素是否含 guid
        "has_condition": bool,   # array<object> 元素是否含 condition
        "has_action": bool,      # array<object> 元素是否含 action
        "sample_values": list,   # 样本值（标量）
    }
    """
    if not isinstance(obj, dict):
        raise TypeError(f"collect_field_info 期望 dict，收到 {type(obj).__name__}")

    if _visited is None:
        _visited = set()
    obj_id = id(obj)
    if obj_id in _visited:
        return
    _visited.add(obj_id)

    for k, v in obj.items():
        path = f"{prefix}{SEP}{k}" if prefix else k

        if path not in info:
            info[path] = {
                "types": set(),
                "child_keys": set(),
                "array_elem_types": set(),
                "has_guid": False,
                "has_condition": False,
                "has_action": False,
                "has_result_title": False,
                "has_tag": False,
                "has_id": False,
                "has_key": False,
                "sample_values": [],
                "count": 0,
                "child_key_counts": {},
                "child_key_types": {},  # {key_name: {type_str: count}}
            }

        entry = info[path]
        entry["count"] += 1
        vtype = get_type_str(v)
        detailed_type = analyze_value_type(v)
        entry["types"].add(detailed_type)

        if vtype in ("int", "float", "string", "bool", "null"):
            entry["sample_values"].append(v)

        if vtype == "object":
            entry["child_keys"].update(v.keys())
            for ck, cv in v.items():
                entry["child_key_counts"][ck] = entry["child_key_counts"].get(ck, 0) + 1
                cv_type = analyze_value_type(cv)
                if ck not in entry["child_key_types"]:
                    entry["child_key_types"][ck] = {}
                entry["child_key_types"][ck][cv_type] = entry["child_key_types"][ck].get(cv_type, 0) + 1
            collect_field_info(v, info, path, _visited)

        elif vtype == "array" and v:
            for item in v:
                item_type = get_type_str(item)
                entry["array_elem_types"].add(item_type)
                if isinstance(item, dict):
                    if "id" in item:
                        entry["has_id"] = True
                    if "guid" in item:
                        entry["has_guid"] = True
                    if "tag" in item:
                        entry["has_tag"] = True
                    if "key" in item:
                        entry["has_key"] = True
                    if "condition" in item:
                        entry["has_condition"] = True
                    if "action" in item:
                        entry["has_action"] = True
                    if "result_title" in item:
                        entry["has_result_title"] = True
                    arr_path = f"{path}{SEP}{ARR_MARKER}"
                    collect_field_info(item, info, arr_path, _visited)


# ==================== Schema 推断 ====================

def _collapse_int_float(types):
    """将类型集合中的 int 合并到 float（包括 array 内部的 int）"""
    if "int" not in types or "float" not in types:
        return types
    types = set(types)
    types.discard("int")
    to_remove = set()
    to_add = set()
    for t in types:
        if not t.startswith("array<") or "int" not in t:
            continue
        inner = t[6:-1].split(",")
        merged = sorted(set("float" if x == "int" else x for x in inner))
        new_t = f"array<{','.join(merged)}>"
        if new_t != t:
            to_remove.add(t)
            to_add.add(new_t)
    types -= to_remove
    types |= to_add
    return types


def infer_type(types_set):
    """从观察到的类型集合推断 schema 类型"""
    types = _collapse_int_float(set(types_set))

    # null 不影响类型推断，但标记可选
    types.discard("null")
    if not types:
        return "null"

    if len(types) == 1:
        return types.pop()

    return sorted(types)


_SCALAR_TYPES = {"int", "float", "string", "bool"}


def _validate_type_combination(field_name, type_list):
    """验证多类型组合是否合理，不合理时通过 diagnostics 报告"""
    types = set(type_list)
    has_scalar = bool(types & _SCALAR_TYPES)
    has_object = "object" in types
    has_array = any(t.startswith("array") for t in types)

    # 标量+对象+数组三类并存
    if has_scalar and has_object and has_array:
        diag.warn("schema", f"字段 '{field_name}' 类型组合异常（标量+对象+数组）: {type_list}")
    # 标量+对象（无 null 缓冲）
    elif has_scalar and has_object and "null" not in type_list:
        diag.warn("schema", f"字段 '{field_name}' 类型组合可疑（标量+对象）: {type_list}")
    # 对象+数组
    elif has_object and has_array:
        diag.warn("schema", f"字段 '{field_name}' 类型组合可疑（对象+数组）: {type_list}")


def _detect_match_key(field_info) -> list[str] | None:
    """检测 array<object> 元素中是否存在可用于匹配的唯一标识字段。
    按 COMMON_MATCH_KEYS 优先级顺序返回第一个命中的字段。"""
    from .merger import COMMON_MATCH_KEYS
    for key in COMMON_MATCH_KEYS:
        if field_info.get(f"has_{key}"):
            return [key]
    return None


def infer_merge_strategy(field_name, type_info, field_info):
    """根据字段名、类型、结构信息推断合并策略"""
    # 联合类型
    if isinstance(type_info, list):
        # 联合类型含 array<object> 且有 match key → smart_match
        if "array<object>" in type_info and _detect_match_key(field_info):
            return "smart_match"
        # 联合类型含 array<object> 但无 match key → append
        if "array<object>" in type_info or "array" in type_info:
            return "append"
        _validate_type_combination(field_name, type_info)
        return "coerce"

    # 标量 → replace
    if type_info in ("int", "float", "string", "bool", "null"):
        return "replace"

    # object → merge
    if type_info == "object":
        return "merge"

    # array<object>：有唯一标识字段则 smart_match，否则 append
    if type_info == "array<object>":
        if _detect_match_key(field_info):
            return "smart_match"
        return "append"

    # 其他 array → append
    if isinstance(type_info, str) and type_info.startswith("array"):
        return "append"

    return "replace"


def infer_match_key(field_info) -> list[str] | None:
    """推断 smart_match 的匹配字段"""
    return _detect_match_key(field_info)


# 模板注册表：canonical_name → template_def（在 Pass 1 完成后填充）
_templates_registry = {}


def _infer_type_from_counts(type_counts):
    """从 {type_str: count} 推断 schema 类型"""
    if not type_counts:
        return "null"
    types = _collapse_int_float(set(type_counts.keys()))
    types.discard("null")
    if not types:
        return "null"
    if len(types) == 1:
        return types.pop()
    return sorted(types)


def _detect_match_key_from_global(canonical) -> list[str] | None:
    """从 _global_field_info 检测 array<object> 元素中的唯一标识字段"""
    if canonical not in _global_field_info:
        return None
    fi = _global_field_info[canonical]
    elem_counts = fi.get("elem_child_key_counts", {})
    if not elem_counts:
        return None
    from .merger import COMMON_MATCH_KEYS
    for key in COMMON_MATCH_KEYS:
        if key in elem_counts:
            return [key]
    return None


def _build_element_from_global(arr_canonical):
    """从全局字段信息构建数组元素定义"""
    if arr_canonical not in _global_field_info:
        return None
    fi = _global_field_info[arr_canonical]
    elem_counts = fi.get("elem_child_key_counts", {})
    elem_types = fi.get("elem_child_key_types", {})
    if not elem_counts:
        return None

    element = {}
    for key in sorted(elem_counts):
        fd = _build_field_from_counts(key, elem_types.get(key, {}))
        if fd is not None:
            element[key] = fd
    return element or None


def _build_field_from_counts(key, type_counts, self_name=None):
    """从 {type_str: count} 构建单个字段定义（供模板 fields 和 element 复用）"""
    if classify_dsl_key(key):
        return None

    type_val = _infer_type_from_counts(type_counts)

    # 检查模板引用（允许同名引用，但防止自引用）
    canonical = _canonical_field_name(key)
    if canonical in _templates_registry and canonical != self_name:
        return {"_use_template": canonical}

    # 推断合并策略
    if isinstance(type_val, list):
        # 联合类型含 array<object>：从全局信息检测 match_key
        if "array<object>" in type_val or "array" in type_val:
            mk = _detect_match_key_from_global(_canonical_field_name(key))
            merge = "smart_match" if mk else "append"
        else:
            merge = "coerce"
    elif type_val == "object":
        merge = "merge"
    elif type_val == "array<object>":
        mk = _detect_match_key_from_global(_canonical_field_name(key))
        merge = "smart_match" if mk else "append"
    elif isinstance(type_val, str) and type_val.startswith("array"):
        merge = "append"
    else:
        merge = "replace"

    field_def = {"type": type_val, "merge": merge}
    if merge == "smart_match":
        mk = _detect_match_key_from_global(_canonical_field_name(key))
        if mk:
            field_def["match_key"] = mk

    # array<object> 字段递归构建 element
    if (type_val == "array<object>"
            or (isinstance(type_val, list) and "array<object>" in type_val)):
        elem = _build_element_from_global(canonical)
        if elem:
            field_def["element"] = elem

    return field_def


def _build_template_from_field_info(fi, self_name=None):
    """从全局字段信息构建命名模板定义。
    self_name: 当前正在构建的模板名，防止自引用。
    """
    key_counts = fi["child_key_counts"]
    key_types = fi["child_key_types"]

    fields = {}
    for key in sorted(key_counts, key=lambda k: -key_counts[k]):
        fd = _build_field_from_counts(key, key_types.get(key, {}), self_name=self_name)
        if fd is not None:
            fields[key] = fd

    result = {
        "type": "object",
        "merge": "merge",
        "fields": fields,
    }
    if not fields:
        result["_note"] = "所有子 key 均为 DSL 模式，由全局 DSL 规则处理"
    return result


def _build_templates():
    """从 _global_field_info 自动发现并构建所有模板。
    判定规则：一个字段在多个不同路径中被复用（paths >= 2）且有子 key，即注册为模板。
    两遍构建：第一遍注册所有模板名（占位），第二遍构建定义（此时所有模板名都可见，解决顺序依赖）。
    """
    global _templates_registry
    _templates_registry = {}
    # 第一遍：注册所有模板名（占位）
    template_names = []
    for name, fi in _global_field_info.items():
        if len(fi["child_keys"]) > 0 and len(fi["paths"]) >= 2:
            template_names.append(name)
            _templates_registry[name] = None
    # 第二遍：构建定义
    for name in template_names:
        _templates_registry[name] = _build_template_from_field_info(
            _global_field_info[name], self_name=name)
    return _templates_registry


def _build_dsl_rules():
    """从所有已注册模板的 DSL key 聚合每个 DSL 组的类型信息，生成 _dsl_rules"""
    group_types = {}  # group_name → {type_str: count}
    for name in _templates_registry:
        fi = _global_field_info[name]
        for key in fi["child_key_counts"]:
            group = classify_dsl_key(key)
            if not group:
                continue
            types = fi["child_key_types"].get(key, {})
            if group not in group_types:
                group_types[group] = {}
            for t, tc in types.items():
                group_types[group][t] = group_types[group].get(t, 0) + tc

    dsl_rules = {}
    for group_name, types in sorted(group_types.items()):
        type_val = _infer_type_from_counts(types)

        # object 类型的 DSL 组：检查是否映射到已知模板
        if type_val == "object" or (isinstance(type_val, list) and "object" in type_val):
            # case:opN → action 模板
            if group_name == "case":
                dsl_rules[group_name] = {"_use_template": "action"}
                continue

        # 普通 DSL 组
        if isinstance(type_val, list):
            merge = "coerce"
        elif isinstance(type_val, str) and type_val.startswith("array"):
            merge = "append"
        else:
            merge = "replace"
        dsl_rules[group_name] = {"type": type_val, "merge": merge}

    return dsl_rules



def build_field_def(path, info, all_info):
    """构建单个字段的 schema 定义"""
    field_info = info
    types = field_info["types"]
    field_name = path.split(SEP)[-1]
    type_val = infer_type(types)
    merge = infer_merge_strategy(field_name, type_val, field_info)

    result = {"type": type_val, "merge": merge}

    # object 处理
    if type_val == "object" or (isinstance(type_val, list) and "object" in type_val):
        canonical = _canonical_field_name(field_name)
        if canonical in _templates_registry:
            result["_use_template"] = canonical
        else:
            # 固定 key object → 递归构建 fields
            fields = {}
            for child_key in sorted(field_info["child_keys"]):
                child_path = f"{path}{SEP}{child_key}"
                if child_path in all_info:
                    fields[child_key] = build_field_def(child_path, all_info[child_path], all_info)
                else:
                    diag.warn("schema", f"子路径 {child_path.replace(SEP, ' → ')} 未在分析数据中找到")
            if fields:
                # 检测子 key 是否为同类结构：
                # 全部 value 都是 object 且子字段名集合存在包含关系 → 用 _template 替代
                obj_fields = {k: v for k, v in fields.items()
                              if isinstance(v, dict) and v.get("type") == "object" and "fields" in v}
                if len(obj_fields) > 1 and len(obj_fields) == len(fields):
                    # 取子字段名最多的集合作为基准
                    sub_key_sets = {k: frozenset(v["fields"].keys()) for k, v in obj_fields.items()}
                    largest = max(sub_key_sets.values(), key=len)
                    # 所有子 key 的字段集合都是最大集合的子集 → 同类
                    all_subset = all(s <= largest for s in sub_key_sets.values())
                    if all_subset:
                        # 选 schema 最丰富的（序列化最长的）作为模板
                        best_key = max(obj_fields, key=lambda k: len(
                            json.dumps(obj_fields[k], sort_keys=True)))
                        result["_template"] = obj_fields[best_key]
                    else:
                        result["fields"] = fields
                else:
                    result["fields"] = fields

    # array<object> 处理（包括联合类型中含 array<object> 或 array 的情况）
    has_array_object = (
        type_val == "array<object>"
        or (isinstance(type_val, list) and ("array<object>" in type_val or "array" in type_val))
        or (merge == "smart_match")
    )
    if has_array_object:
        arr_path = f"{path}{SEP}{ARR_MARKER}"
        # 收集数组元素的字段
        element = {}
        for key in sorted(all_info.keys()):
            if key.startswith(arr_path + SEP):
                remainder = key[len(arr_path) + 1:]
                if SEP not in remainder:
                    element[remainder] = build_field_def(key, all_info[key], all_info)
        if element:
            result["element"] = element
        else:
            diag.warn("schema", f"数组元素无字段信息: {arr_path.replace(SEP, ' → ')}")

        if merge == "smart_match":
            mk = infer_match_key(field_info)
            if mk:
                result["match_key"] = mk
            else:
                # 无 match_key：降级为 append
                result["merge"] = "append"

    return result



# ==================== 分析入口 ====================

def _collect_file_info(filepath):
    """收集单个根目录文件的字段信息（不构建 schema）。
    返回 (file_type, info, data) 或 None。
    """
    data = load_json(filepath)
    if data is None:
        return None
    file_type = classify_json(data)
    info = {}

    if file_type == "dictionary":
        for _, entry_val in data.items():
            if isinstance(entry_val, dict):
                collect_field_info(entry_val, info)

        # 检查是否扁平字典（无 id、所有 value 非 dict）
        top_keys = set(data.keys())
        if len(top_keys) > DYNAMIC_KEY_THRESHOLD:
            sample_vals = [data[k] for k in list(data.keys())[:10]]
            if all(not isinstance(v, dict) for v in sample_vals):
                file_type = "flat_dict"
                info = {}
    else:
        collect_field_info(data, info)

    return file_type, info, data


def _collect_dir_info(dirpath):
    """收集子目录所有文件的字段信息（不构建 schema）。
    返回 (file_type, info, file_count, total) 或 None。
    """
    files = sorted([f for f in os.listdir(dirpath) if f.endswith(".json")])
    total = len(files)

    def _load(fname):
        return load_json(os.path.join(dirpath, fname))

    with ThreadPoolExecutor(max_workers=min(16, max(1, total // 10))) as pool:
        results = list(pool.map(_load, files))

    info = {}
    file_count = 0
    file_type = None

    for data in results:
        if data is None:
            continue
        file_count += 1
        if file_type is None:
            file_type = classify_json(data)
        if file_type in ("entity", "config"):
            collect_field_info(data, info)
        elif file_type == "dictionary":
            for _, entry_val in data.items():
                if isinstance(entry_val, dict):
                    collect_field_info(entry_val, info)

    if not info:
        return None
    return file_type, info, file_count, total


def _build_single_file_schema(filepath, file_type, info):
    """从已收集的字段信息构建单个文件的 schema"""
    if file_type == "flat_dict":
        return {
            "_meta": {
                "schema_version": 1,
                "file_type": "config",
                "description": Path(filepath).stem,
                "source": Path(filepath).name,
                "top_level_dsl": True,
            },
        }

    if file_type == "dictionary":
        entry_schema = {}
        for key in sorted(info.keys()):
            if SEP not in key:
                entry_schema[key] = build_field_def(key, info[key], info)
        return {
            "_meta": {
                "schema_version": 1,
                "file_type": "dictionary",
                "description": Path(filepath).stem,
                "source": Path(filepath).name,
            },
            "_entry": entry_schema,
        }

    if file_type == "entity":
        fields_schema = {}
        for key in sorted(info.keys()):
            if SEP not in key:
                fields_schema[key] = build_field_def(key, info[key], info)
        return {
            "_meta": {
                "schema_version": 1,
                "file_type": "entity",
                "description": Path(filepath).stem,
                "source": Path(filepath).name,
            },
            "_fields": fields_schema,
        }

    # config
    top_child_keys = {k for k in info if SEP not in k}
    if len(top_child_keys) > DYNAMIC_KEY_THRESHOLD:
        return {
            "_meta": {
                "schema_version": 1,
                "file_type": "config",
                "description": Path(filepath).stem,
                "source": Path(filepath).name,
                "top_level_dsl": True,
            },
        }

    fields_schema = {}
    for key in sorted(info.keys()):
        if SEP not in key:
            fields_schema[key] = build_field_def(key, info[key], info)
    return {
        "_meta": {
            "schema_version": 1,
            "file_type": "config",
            "description": Path(filepath).stem,
            "source": Path(filepath).name,
        },
        "_fields": fields_schema,
    }


def _build_dir_schema(dirpath, file_type, info, file_count, total):
    """从已收集的字段信息构建子目录的 schema"""
    dirname = Path(dirpath).name
    schema_key = "_fields" if file_type in ("entity", "config") else "_entry"

    fields_schema = {}
    for key in sorted(info.keys()):
        if SEP not in key:
            fields_schema[key] = build_field_def(key, info[key], info)

    return {
        "_meta": {
            "schema_version": 1,
            "file_type": file_type or "entity",
            "description": dirname,
            "source": f"{dirname}/",
            "file_count": total,
            "analyzed": file_count,
        },
        schema_key: fields_schema,
    }


# ==================== 主入口 ====================

def generate_all(config_dir, output_dir, progress_callback=None):
    """生成所有 schema 文件（两遍处理：先收集字段信息，再构建 schema）

    progress_callback: 可选回调 (current: int, total: int, name: str) -> None
    """
    global _global_field_info, _templates_registry
    _global_field_info = {}
    _templates_registry = {}

    config_path = Path(config_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # ==================== Pass 1: 收集所有字段信息 ====================
    diag.info("schema", "=== Pass 1: 收集字段信息 ===")

    root_files = sorted([
        f for f in os.listdir(config_path)
        if f.endswith(".json") and os.path.isfile(config_path / f)
    ])
    subdirs = sorted([
        d for d in os.listdir(config_path)
        if os.path.isdir(config_path / d)
    ])

    # 收集根目录文件信息
    file_infos = {}  # fname → (file_type, info, data)
    for fname in root_files:
        filepath = config_path / fname
        result = _collect_file_info(str(filepath))
        if result:
            file_infos[fname] = result
            _accumulate_global_info(result[1])  # result[1] = info
            diag.info("schema", f"  收集: {fname}")

    # 收集子目录信息
    dir_infos = {}  # dirname → (file_type, info, file_count, total)
    for subdir in subdirs:
        dirpath = config_path / subdir
        result = _collect_dir_info(str(dirpath))
        if result:
            dir_infos[subdir] = result
            _accumulate_global_info(result[1])  # result[1] = info
            diag.info("schema", f"  收集: {subdir}/")

    # 构建模板注册表和 DSL 规则
    _build_templates()
    dsl_rules = _build_dsl_rules()
    diag.info("schema", f"自动发现的模板: {sorted(_templates_registry.keys())}")
    for name, tpl in _templates_registry.items():
        n_fields = len(tpl.get("fields", {}))
        diag.info("schema", f"  {name}: {n_fields} 个固定 key")
    diag.info("schema", f"DSL 规则: {len(dsl_rules)} 组")

    # 写入全局模板文件
    global_schema = {
        "_templates": _templates_registry,
        "_dsl_rules": dsl_rules,
    }
    global_file = output_path / "_global.schema.json"
    global_file.write_text(
        json.dumps(global_schema, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    diag.info("schema", "全局模板 → _global.schema.json")

    # ==================== Pass 2: 构建 schema ====================
    diag.info("schema", "=== Pass 2: 构建 schema ===")

    generated = []
    # 计算总数用于进度回调
    total_items = len([f for f in root_files if f in file_infos]) + \
                  len([d for d in subdirs if d in dir_infos])
    current_item = 0

    for fname in root_files:
        if fname not in file_infos:
            continue
        filepath = config_path / fname
        file_type, info, _data = file_infos[fname]
        schema = _build_single_file_schema(str(filepath), file_type, info)
        if schema:
            out_name = f"{Path(fname).stem}.schema.json"
            out_file = output_path / out_name
            out_file.write_text(
                json.dumps(schema, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            diag.info("schema", f"构建: {fname} → {out_name}")
            generated.append(out_name)
        current_item += 1
        if progress_callback:
            progress_callback(current_item, total_items, fname)

    for subdir in subdirs:
        if subdir not in dir_infos:
            continue
        dirpath = config_path / subdir
        file_type, info, file_count, total = dir_infos[subdir]
        schema = _build_dir_schema(str(dirpath), file_type, info, file_count, total)
        if schema:
            out_name = f"{subdir}.schema.json"
            out_file = output_path / out_name
            out_file.write_text(
                json.dumps(schema, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            diag.info("schema", f"构建: {subdir}/ → {out_name}")
            generated.append(out_name)
        current_item += 1
        if progress_callback:
            progress_callback(current_item, total_items, f"{subdir}/")

    diag.info("schema", f"共生成 {len(generated)} 个 schema 文件 + 1 个全局模板到 {output_path}")

    return generated


def ensure_schemas(config_dir, schema_dir) -> bool:
    """检查 schemas/ 是否已初始化，若为空则执行生成。返回是否执行了生成。"""
    schema_dir = Path(schema_dir)
    if schema_dir.exists() and any(schema_dir.glob("*.schema.json")):
        return False
    generate_all(str(config_dir), str(schema_dir))
    return True
