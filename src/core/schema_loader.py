"""
Schema 加载器 - 从 schemas/ 目录加载规则文件，提供字段定义查询
"""
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

# 缓存的 schemas：{pattern: schema_dict}
_schemas: dict[str, dict] = {}


def load_schemas(schema_dir: Path | str) -> dict[str, dict]:
    """
    加载 schema 目录下的所有 .schema.json 文件。

    返回: {pattern: schema_dict}
        pattern 为匹配规则：
        - "cards.json" → 精确匹配根文件
        - "rite/" → 匹配 rite/ 目录下所有文件
    """
    global _schemas
    schema_dir = Path(schema_dir)
    schemas = {}

    if not schema_dir.exists():
        log.warning(f"schema 目录不存在: {schema_dir}")
        return schemas

    for schema_file in schema_dir.glob("*.schema.json"):
        raw = schema_file.read_text(encoding="utf-8")
        schema = json.loads(raw)
        meta = schema.get("_meta", {})
        source = meta.get("source", "")

        if source:
            schemas[source] = schema
        else:
            # 从文件名推断：cards.schema.json → cards.json
            stem = schema_file.stem.replace(".schema", "")
            schemas[f"{stem}.json"] = schema

    _schemas = schemas
    log.info(f"已加载 {len(schemas)} 个 schema 文件")
    return schemas


def resolve_schema(rel_path: str, schemas: dict[str, dict] | None = None) -> dict | None:
    """
    根据文件相对路径匹配 schema。

    匹配规则：
    1. 精确匹配（如 "cards.json"）
    2. 目录匹配（如 "rite/5000001.json" → "rite/"）
    """
    if schemas is None:
        schemas = _schemas

    # 统一路径分隔符
    rel_path = rel_path.replace("\\", "/")

    # 精确匹配
    if rel_path in schemas:
        return schemas[rel_path]

    # 目录匹配
    if "/" in rel_path:
        dir_part = rel_path.split("/")[0] + "/"
        if dir_part in schemas:
            return schemas[dir_part]

    return None


def get_field_def(schema: dict, field_path: list[str]) -> dict | None:
    """
    在 schema 树中查找字段定义。

    field_path 是从根到目标字段的路径列表。
    首个元素通常是 "_entry" 或 "_fields"（由调用方决定）。

    导航逻辑：
    - 遇到 "_entry" / "_fields" → 直接取对应的 dict
    - 遇到有 "fields" 的节点 → 进入 fields
    - 遇到 dynamic_keys → 返回 dynamic_value（如果目标是具体 key）
    - 遇到有 "element" 的节点 → 进入 element（用于数组元素的字段）
    """
    if not field_path or not schema:
        return None

    current = schema
    for i, segment in enumerate(field_path):
        if current is None:
            return None

        # 顶层导航：_entry / _fields / _dynamic_value
        if segment in ("_entry", "_fields"):
            current = current.get(segment)
            continue

        if segment == "_dynamic_value":
            current = current.get("_dynamic_value")
            continue

        # 在当前层级查找 segment
        if isinstance(current, dict):
            # 优先在 fields 子结构中查找（避免与 schema 元数据 key 冲突）
            if "fields" in current and isinstance(current["fields"], dict):
                if segment in current["fields"]:
                    current = current["fields"][segment]
                    continue

            # 在 element 子结构中查找（数组元素字段）
            if "element" in current and isinstance(current["element"], dict):
                if segment in current["element"]:
                    current = current["element"][segment]
                    continue

            # 动态 key 处理：当前层标记了 dynamic_keys
            if current.get("dynamic_keys"):
                dv = current.get("dynamic_value")
                if dv:
                    # 如果这是最后一个 segment，返回 dynamic_value 定义
                    if i == len(field_path) - 1:
                        return dv
                    # 否则继续在 dynamic_value 中导航
                    current = dv
                    continue

            # 回退：直接在当前层级查找
            if segment in current:
                current = current[segment]
                continue

            # 找不到
            return None

    return current


def get_schema_root_key(schema: dict) -> str:
    """根据 schema 的 file_type 确定根级字段 key"""
    file_type = schema.get("_meta", {}).get("file_type", "config")
    if file_type == "dictionary":
        return "_entry"
    return "_fields"


def check_type_match(schema_type, actual_value) -> bool:
    """
    检查实际值的类型是否匹配 schema 定义的类型。

    schema_type 可以是 string 或 list[string]。
    """
    if schema_type is None:
        return True

    actual_type = _get_actual_type(actual_value)
    if isinstance(schema_type, list):
        return any(_type_compatible(st, actual_type, actual_value) for st in schema_type)
    return _type_compatible(schema_type, actual_type, actual_value)


def _get_actual_type(value) -> str:
    """获取值的类型字符串"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_compatible(schema_type: str, actual_type: str, actual_value) -> bool:
    """检查单个 schema 类型与实际类型是否兼容"""
    if schema_type == actual_type:
        return True

    # int 兼容 float
    if schema_type == "float" and actual_type == "int":
        return True

    # array<X> 匹配实际的 array
    if schema_type.startswith("array") and actual_type == "array":
        return True

    return False
