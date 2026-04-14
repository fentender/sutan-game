"""
临时脚本：统计游戏本体 config 目录中所有 JSON 文件的数组字段。
同名 key 的数组聚合统计，动态 key 用 dsl_patterns 聚合。
结果写入 array_report.txt（UTF-8）。
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.core.json_parser import load_json, DupList
from src.core.dsl_patterns import classify_dsl_key
from src.config import DEFAULT_CONFIG_SUBPATH


def _type_name(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, DupList):
        return "DupList"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    return type(v).__name__


def _short_repr(v, max_len=80):
    if isinstance(v, str):
        s = json.dumps(v, ensure_ascii=False)
    elif isinstance(v, dict):
        keys = list(v.keys())[:5]
        extra = f", ... (+{len(v) - 5})" if len(v) > 5 else ""
        s = "{" + ", ".join(json.dumps(k, ensure_ascii=False) for k in keys) + extra + "}"
    elif isinstance(v, (list, DupList)):
        s = json.dumps(v, ensure_ascii=False, default=str)
    else:
        s = json.dumps(v, ensure_ascii=False, default=str)
    if len(s) > max_len:
        s = s[:max_len - 3] + "..."
    return s


class ArrayFieldInfo:
    def __init__(self):
        self.path_files: dict[str, set[str]] = defaultdict(set)
        self.elem_type_count: dict[str, int] = defaultdict(int)
        self.obj_keys: dict[str, dict[str, str]] = defaultdict(dict)
        self.count = 0
        self.examples: list[str] = []


registry: dict[str, ArrayFieldInfo] = defaultdict(ArrayFieldInfo)


def _key_to_path_token(key: str, is_dict_top: bool) -> str:
    """将 key 转换为路径 token。
    - 字典型顶层 ID → *
    - 纯数字 ID key → *（任意层级）
    - DSL 动态 key → {组名}
    - 普通 key → 原样
    """
    if is_dict_top:
        return "*"
    if key.isdigit():
        return "*"
    dsl_group = classify_dsl_key(key)
    if dsl_group:
        return "{" + dsl_group + "}"
    return key


def _normalize_path(parts: list[str]) -> str:
    result = []
    for p in parts:
        if p.startswith("[") and p.endswith("]"):
            if result:
                result[-1] = result[-1] + "[]"
            else:
                result.append("[]")
        else:
            result.append(p)
    return ".".join(result)


def _walk_and_collect(data, path_parts: list[str], rel_file: str, is_dict_top: bool):
    if isinstance(data, dict):
        for key, value in data.items():
            token = _key_to_path_token(key, is_dict_top)
            child_parts = path_parts + [token]

            if isinstance(value, list) and not isinstance(value, DupList):
                # 注册时 field_name 用 token（DSL key 聚合后的名字）
                _register_array(token, child_parts, value, rel_file)
                for idx, elem in enumerate(value):
                    _walk_and_collect(elem, child_parts + [f"[{idx}]"], rel_file, False)
            elif isinstance(value, DupList):
                for idx, elem in enumerate(value):
                    _walk_and_collect(elem, child_parts + [f"[{idx}]"], rel_file, False)
            elif isinstance(value, dict):
                _walk_and_collect(value, child_parts, rel_file, False)
    elif isinstance(data, list):
        for idx, elem in enumerate(data):
            _walk_and_collect(elem, path_parts + [f"[{idx}]"], rel_file, False)


def _register_array(field_name: str, path_parts: list[str], arr: list, rel_file: str):
    info = registry[field_name]
    info.count += 1

    pattern = _normalize_path(path_parts)
    info.path_files[pattern].add(rel_file)

    for elem in arr:
        t = _type_name(elem)
        info.elem_type_count[t] += 1

        if isinstance(elem, dict):
            for k, v in elem.items():
                vtype = _type_name(v)
                if vtype not in info.obj_keys[k]:
                    info.obj_keys[k][vtype] = _short_repr(v)
        else:
            if len(info.examples) < 5:
                info.examples.append(_short_repr(elem, 60))


def _classify_top(data) -> bool:
    if not isinstance(data, dict):
        return False
    if len(data) < 2:
        return False
    dict_count = sum(1 for v in data.values() if isinstance(v, dict))
    return dict_count > len(data) * 0.5


def main():
    config_file = Path(__file__).parent / "user_config.json"
    with open(config_file, encoding="utf-8") as f:
        user_cfg = json.load(f)
    game_path = Path(user_cfg["game_path"])
    config_dir = game_path / DEFAULT_CONFIG_SUBPATH

    if not config_dir.exists():
        print(f"错误：config 目录不存在 → {config_dir}")
        sys.exit(1)

    json_files = sorted(config_dir.rglob("*.json"))

    output_path = Path(__file__).parent / "array_report.txt"
    out = open(output_path, "w", encoding="utf-8")

    def p(text=""):
        out.write(text + "\n")

    p(f"扫描目录: {config_dir}")
    p(f"找到 JSON 文件: {len(json_files)} 个")
    p()

    errors = []
    for fp in json_files:
        rel = fp.relative_to(config_dir).as_posix()
        try:
            data = load_json(fp, readonly=True)
        except Exception as e:
            errors.append((rel, str(e)))
            continue

        is_dict_top = _classify_top(data)
        _walk_and_collect(data, [], rel, is_dict_top)

    p("=" * 70)
    p("  数组字段统计报告")
    p("=" * 70)
    p()

    for field_name in sorted(registry.keys()):
        info = registry[field_name]

        path_summary = {}
        for pattern, files in info.path_files.items():
            dirs = set()
            for f in files:
                parts = f.split("/")
                if len(parts) > 1:
                    dirs.add(parts[0] + "/*.json")
                else:
                    dirs.add(f)
            dir_key = ", ".join(sorted(dirs))
            if dir_key not in path_summary:
                path_summary[dir_key] = []
            path_summary[dir_key].append(pattern)

        p(f"字段名: {field_name}")

        for file_group, patterns in path_summary.items():
            unique_patterns = sorted(set(patterns))
            for pat in unique_patterns:
                files_for_pattern = info.path_files[pat]
                p(f"  路径: {file_group} → {pat}  ({len(files_for_pattern)} 个文件)")

        p(f"  总出现次数: {info.count}")

        total_elems = sum(info.elem_type_count.values())
        if total_elems > 0:
            type_parts = []
            for t, c in sorted(info.elem_type_count.items(), key=lambda x: -x[1]):
                pct = c * 100 / total_elems
                type_parts.append(f"{t} {c}次 ({pct:.0f}%)")
            p(f"  元素类型: {', '.join(type_parts)}")
        else:
            p(f"  元素类型: (空数组)")

        if info.obj_keys:
            p(f"  对象 key:")
            max_key_len = max(len(k) for k in info.obj_keys)
            for k in sorted(info.obj_keys.keys()):
                types_examples = info.obj_keys[k]
                type_str = ", ".join(sorted(types_examples.keys()))
                example = list(types_examples.values())[0]
                p(f"    {k:<{max_key_len}}  : {type_str:<12} 例: {example}")

        if info.examples:
            unique = list(dict.fromkeys(info.examples))[:3]
            p(f"  示例值: {' / '.join(unique)}")

        p()

    if errors:
        p("-" * 70)
        p("解析失败的文件:")
        for rel, err in errors:
            p(f"  {rel}: {err}")
        p()

    p(f"共统计 {len(registry)} 个不同的数组字段名")
    out.close()
    print(f"完成。报告已写入: {output_path}")


if __name__ == "__main__":
    main()
