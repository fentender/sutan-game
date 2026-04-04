"""
从游戏本体 config 目录自动生成 schema 规则文件。

用法:
    python scripts/generate_schemas.py [--config-dir PATH] [--output-dir PATH] [--sample-limit N]

默认:
    --config-dir  D:/SteamLibrary/steamapps/common/Sultan's Game/Sultan's Game_Data/StreamingAssets/config
    --output-dir  schemas/
    --sample-limit 80
"""
import json
import os
import sys
import argparse
from pathlib import Path

# 添加项目根目录到 sys.path 以复用 json_parser
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.json_parser import load_json

DEFAULT_CONFIG_DIR = (
    r"D:\SteamLibrary\steamapps\common\Sultan's Game"
    r"\Sultan's Game_Data\StreamingAssets\config"
)

# 动态 key 阈值：子 key 数量超过此值判定为动态字典
DYNAMIC_KEY_THRESHOLD = 30

# 路径分隔符（内部使用，避免和 JSON key 中的点号冲突）
SEP = '\x01'
ARR_MARKER = '[]'

# 已知的 DSL 动态 key 字段名（无论子 key 数量多少，都标记为 dynamic_keys）
KNOWN_DYNAMIC_FIELDS = {
    'condition', 'result', 'action', 'effect',
    'tag', 'cards_slot', 'no_show', 'choose',
}

# 已知的 smart_match 数组字段名
KNOWN_SMART_MATCH_FIELDS = {
    'settlement', 'settlement_prior', 'settlement_extre',
    'waiting_round_end_action',
}


# ==================== 类型分析 ====================

def get_type_str(v):
    """获取 Python 值的类型字符串"""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def analyze_value_type(v):
    """分析单个值的详细类型（包括数组元素类型）"""
    base = get_type_str(v)
    if base == "array" and v:
        elem_types = {get_type_str(item) for item in v}
        if len(elem_types) == 1:
            et = elem_types.pop()
            return f"array<{et}>"
        return "array"
    return base


def collect_field_info(obj, info, prefix="", max_depth=7):
    """
    递归分析对象结构，收集每个路径的类型信息和值样本。

    info[path] = {
        "types": set(),          # 观察到的类型集合
        "child_keys": set(),     # 如果是 object，记录子 key 名
        "array_elem_types": set(), # 如果是 array，记录元素类型
        "has_guid": bool,        # array<object> 元素是否含 guid
        "has_condition": bool,   # array<object> 元素是否含 condition
        "has_action": bool,      # array<object> 元素是否含 action
        "sample_values": list,   # 最多保存 3 个样本值（标量）
    }
    """
    if not isinstance(obj, dict):
        return

    for k, v in obj.items():
        path = f"{prefix}{SEP}{k}" if prefix else k
        depth = path.count(SEP)

        if path not in info:
            info[path] = {
                "types": set(),
                "child_keys": set(),
                "array_elem_types": set(),
                "has_guid": False,
                "has_condition": False,
                "has_action": False,
                "has_result_title": False,
                "sample_values": [],
                "count": 0,
            }

        entry = info[path]
        entry["count"] += 1
        vtype = get_type_str(v)
        detailed_type = analyze_value_type(v)
        entry["types"].add(detailed_type)

        if vtype in ("int", "float", "string", "bool", "null"):
            if len(entry["sample_values"]) < 3:
                entry["sample_values"].append(v)

        if depth >= max_depth:
            continue

        if vtype == "object":
            entry["child_keys"].update(v.keys())
            collect_field_info(v, info, path, max_depth)

        elif vtype == "array" and v:
            for item in v:
                item_type = get_type_str(item)
                entry["array_elem_types"].add(item_type)
                if isinstance(item, dict):
                    if "guid" in item:
                        entry["has_guid"] = True
                    if "condition" in item:
                        entry["has_condition"] = True
                    if "action" in item:
                        entry["has_action"] = True
                    if "result_title" in item:
                        entry["has_result_title"] = True
                    arr_path = f"{path}{SEP}{ARR_MARKER}"
                    collect_field_info(item, info, arr_path, max_depth)


# ==================== Schema 推断 ====================

def infer_type(types_set):
    """从观察到的类型集合推断 schema 类型"""
    types = set(types_set)

    # int + float → float
    if "int" in types and "float" in types:
        types.discard("int")
        types = {t.replace("array<int>", "array<float>") if "array<int>" in types else t for t in types}

    # null 不影响类型推断，但标记可选
    types.discard("null")
    if not types:
        return "null"

    if len(types) == 1:
        return types.pop()

    return sorted(types)


def infer_merge_strategy(field_name, type_info, field_info):
    """根据字段名、类型、结构信息推断合并策略"""
    # 已知的 smart_match 字段（即使类型是联合类型也用 smart_match）
    if field_name in KNOWN_SMART_MATCH_FIELDS:
        return "smart_match"

    # 联合类型 → coerce（dynamic_value 的按类型分派由 merge_by_type 处理）
    if isinstance(type_info, list):
        return "coerce"

    # 标量 → replace
    if type_info in ("int", "float", "string", "bool", "null"):
        return "replace"

    # object → merge
    if type_info == "object":
        return "merge"

    # array<object> 特殊处理
    if type_info == "array<object>":
        if field_info.get("has_guid") or field_info.get("has_condition") or field_info.get("has_result_title"):
            return "smart_match"
        if field_info.get("has_action") and not field_info.get("has_condition"):
            return "smart_match"
        return "append"

    # 其他 array → append
    if isinstance(type_info, str) and type_info.startswith("array"):
        return "append"

    return "replace"


def infer_match_strategy(field_info):
    """推断 smart_match 的匹配策略"""
    if field_info.get("has_condition") or field_info.get("has_result_title"):
        return "rite"
    if field_info.get("has_action") and not field_info.get("has_condition"):
        return "event"
    return "rite"  # 默认


def is_dynamic_keys(field_info, field_name=""):
    """判断 object 是否为动态 key"""
    if field_name in KNOWN_DYNAMIC_FIELDS:
        return True
    return len(field_info.get("child_keys", set())) > DYNAMIC_KEY_THRESHOLD


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
        if is_dynamic_keys(field_info, field_name):
            result["dynamic_keys"] = True
            # 分离已知结构子字段 vs 纯动态值
            known_fields = {}
            all_dv_types = set()  # 所有子 key 的类型（含已知字段），用于完整兜底
            for child_key in sorted(field_info["child_keys"]):
                child_path = f"{path}{SEP}{child_key}"
                if child_path not in all_info:
                    continue
                child_info = all_info[child_path]
                child_types = child_info["types"]
                all_dv_types.update(child_types)
                # 跳过出现次数太少的子 key（占比 < 10%），
                # 它们是特定文件的动态内容（如 case:op3），不是固定结构
                child_count = child_info.get("count", 0)
                parent_count = field_info.get("count", 1)
                if child_count < max(2, parent_count * 0.1):
                    continue
                # 值是 object 且有固定子 key → 记为已知字段
                if (child_types == {"object"}
                        and child_info["child_keys"]
                        and not is_dynamic_keys(child_info, child_key)):
                    known_fields[child_key] = build_field_def(
                        child_path, child_info, all_info
                    )
            if known_fields:
                result["fields"] = known_fields
            if all_dv_types:
                dv_type = infer_type(all_dv_types)
                # 默认策略用标量的（replace），按类型分派 object 用 merge
                dv = {"type": dv_type, "merge": "replace"}
                if isinstance(dv_type, list) and "object" in dv_type:
                    dv["merge_by_type"] = {"object": "merge"}
                elif dv_type == "object":
                    dv["merge"] = "merge"
                result["dynamic_value"] = dv
        else:
            # 固定 key object → 递归构建 fields
            fields = {}
            for child_key in sorted(field_info["child_keys"]):
                child_path = f"{path}{SEP}{child_key}"
                if child_path in all_info:
                    fields[child_key] = build_field_def(child_path, all_info[child_path], all_info)
            if fields:
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

        if merge == "smart_match":
            result["match_strategy"] = infer_match_strategy(field_info)

    return result


# ==================== 文件分类 ====================

def classify_json(data):
    """分类 JSON 文件类型，与 merger.py 保持一致"""
    if not isinstance(data, dict):
        return "config"
    if "id" in data:
        return "entity"
    keys = list(data.keys())
    if keys and all(isinstance(data[k], dict) for k in keys[:5]):
        if any("id" in data[k] for k in keys[:5]):
            return "dictionary"
    return "config"


# ==================== 分析入口 ====================

def analyze_single_file(filepath):
    """分析单个根目录文件，返回 schema dict"""
    data = load_json(filepath)
    file_type = classify_json(data)
    info = {}

    if file_type == "dictionary":
        # 采样条目分析
        count = 0
        for key, entry_val in data.items():
            if isinstance(entry_val, dict):
                collect_field_info(entry_val, info)
                count += 1
                if count >= 100:
                    break

        # 检查整个文件是否是动态 key（无 id 的扁平字典）
        top_keys = set(data.keys())
        if len(top_keys) > DYNAMIC_KEY_THRESHOLD:
            # 检查是否所有 value 都是简单值（非 dict）
            sample_vals = [data[k] for k in list(data.keys())[:10]]
            all_simple = all(not isinstance(v, dict) for v in sample_vals)
            if all_simple:
                file_type = "config"
                info = {}
                # 当作动态 key config 处理
                dv_types = {analyze_value_type(v) for v in data.values()}
                dv_type = infer_type(dv_types)
                return {
                    "_meta": {
                        "schema_version": 1,
                        "file_type": "config",
                        "description": Path(filepath).stem,
                        "source": Path(filepath).name,
                        "dynamic_keys": True,
                    },
                    "_dynamic_value": {
                        "type": dv_type,
                        "merge": infer_merge_strategy("", dv_type, {}),
                    },
                }

        # 构建 _entry schema
        entry_schema = {}
        for key in sorted(info.keys()):
            if SEP not in key:  # 只取顶层字段
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

    elif file_type == "entity":
        collect_field_info(data, info)
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

    else:  # config
        collect_field_info(data, info)

        # 检查是否动态 key config
        top_child_keys = {k for k in info if SEP not in k}
        if len(top_child_keys) > DYNAMIC_KEY_THRESHOLD:
            # 检查值类型是否统一
            all_types = set()
            for k in top_child_keys:
                all_types.update(info[k]["types"])
            dv_type = infer_type(all_types)
            return {
                "_meta": {
                    "schema_version": 1,
                    "file_type": "config",
                    "description": Path(filepath).stem,
                    "source": Path(filepath).name,
                    "dynamic_keys": True,
                },
                "_dynamic_value": {
                    "type": dv_type,
                    "merge": infer_merge_strategy("", dv_type, {}),
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


def analyze_directory(dirpath):
    """分析子目录所有文件，返回 schema dict。多线程并行读取加速。"""
    from concurrent.futures import ThreadPoolExecutor

    files = sorted([f for f in os.listdir(dirpath) if f.endswith(".json")])
    total = len(files)

    # 多线程并行读取 JSON 文件
    def _load(fname):
        filepath = os.path.join(dirpath, fname)
        return load_json(filepath)

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

        if file_type == "entity" or file_type == "config":
            collect_field_info(data, info)
        elif file_type == "dictionary":
            for key, entry_val in data.items():
                if isinstance(entry_val, dict):
                    collect_field_info(entry_val, info)

    if not info:
        return None

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

def generate_all(config_dir, output_dir):
    """生成所有 schema 文件"""
    config_path = Path(config_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    generated = []

    # 根目录文件
    root_files = sorted([
        f for f in os.listdir(config_path)
        if f.endswith(".json") and os.path.isfile(config_path / f)
    ])

    for fname in root_files:
        filepath = config_path / fname
        print(f"分析: {fname} ...", end=" ")
        schema = analyze_single_file(str(filepath))
        if schema:
            out_name = f"{Path(fname).stem}.schema.json"
            out_file = output_path / out_name
            out_file.write_text(
                json.dumps(schema, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"→ {out_name}")
            generated.append(out_name)
        else:
            print("跳过（无法分析）")

    # 子目录
    subdirs = sorted([
        d for d in os.listdir(config_path)
        if os.path.isdir(config_path / d)
    ])

    for subdir in subdirs:
        dirpath = config_path / subdir
        print(f"分析: {subdir}/ ...", end=" ")
        schema = analyze_directory(str(dirpath))
        if schema:
            out_name = f"{subdir}.schema.json"
            out_file = output_path / out_name
            out_file.write_text(
                json.dumps(schema, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"→ {out_name}")
            generated.append(out_name)
        else:
            print("跳过（无文件）")

    print(f"\n共生成 {len(generated)} 个 schema 文件到 {output_path}")
    return generated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从游戏本体生成 JSON schema 规则文件")
    parser.add_argument(
        "--config-dir", default=DEFAULT_CONFIG_DIR,
        help="游戏 config 目录路径",
    )
    parser.add_argument(
        "--output-dir", default=str(PROJECT_ROOT / "schemas"),
        help="schema 输出目录",
    )
    args = parser.parse_args()
    generate_all(args.config_dir, args.output_dir)
