"""
Schema 加载器 - 从 schemas/ 目录加载规则文件，提供字段定义查询
"""
import json
import logging
from pathlib import Path

from .dsl_patterns import classify_dsl_key
from .type_utils import get_type_str

log = logging.getLogger(__name__)

# 缓存的 schemas：{pattern: schema_dict}
_schemas: dict[str, dict] = {}

# 全局模板和 DSL 规则（从 _global.schema.json 加载）
_global_templates: dict[str, dict] = {}
_global_dsl_rules: dict[str, dict] = {}


def load_schemas(schema_dir: Path | str) -> dict[str, dict]:
    """
    加载 schema 目录下的所有 .schema.json 文件。

    返回: {pattern: schema_dict}
        pattern 为匹配规则：
        - "cards.json" → 精确匹配根文件
        - "rite/" → 匹配 rite/ 目录下所有文件
    """
    global _schemas, _global_templates, _global_dsl_rules
    schema_dir = Path(schema_dir)
    schemas = {}

    if not schema_dir.exists():
        log.warning(f"schema 目录不存在: {schema_dir}")
        return schemas

    # 加载全局模板文件
    global_file = schema_dir / "_global.schema.json"
    if global_file.exists():
        global_data = json.loads(global_file.read_text(encoding="utf-8"))
        _global_templates = global_data.get("_templates", {})
        _global_dsl_rules = global_data.get("_dsl_rules", {})
        log.info(f"已加载全局模板 {len(_global_templates)} 个, DSL 规则 {len(_global_dsl_rules)} 组")

    for schema_file in schema_dir.glob("*.schema.json"):
        if schema_file.name == "_global.schema.json":
            continue
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


def _resolve_template(template_name: str) -> dict | None:
    """从全局模板中解析命名模板"""
    return _global_templates.get(template_name)


def get_field_def(schema: dict, field_path: list[str]) -> dict | None:
    """
    在 schema 树中查找字段定义。

    field_path 是从根到目标字段的路径列表。
    首个元素通常是 "_entry" 或 "_fields"（由调用方决定）。

    导航逻辑：
    - 遇到 "_entry" / "_fields" → 直接取对应的 dict
    - 遇到有 "fields" 的节点 → 进入 fields
    - 遇到 "_use_template" → 解析命名模板后继续导航
    - 遇到有 "_template" 的节点 → 回退到模板继续导航
    - 遇到有 "element" 的节点 → 进入 element（用于数组元素的字段）
    - key 匹配 DSL 模式 → 返回默认 replace 定义
    """
    if not field_path or not schema:
        return None

    current = schema
    for segment in field_path:
        if current is None:
            return None

        # 处理 _use_template 引用：先解析为实际模板定义
        if isinstance(current, dict) and "_use_template" in current:
            resolved = _resolve_template(current["_use_template"])
            if resolved is None:
                return None
            current = resolved

        # 顶层导航：_entry / _fields
        if segment in ("_entry", "_fields"):
            current = current.get(segment)
            continue

        # 在当前层级查找 segment
        if isinstance(current, dict):
            # 优先在 fields 子结构中查找
            if "fields" in current and isinstance(current["fields"], dict):
                if segment in current["fields"]:
                    current = current["fields"][segment]
                    continue

            # 同类子结构模板（如 cards_slot 的 s1-s18 共享同一模板）
            if "_template" in current:
                current = current["_template"]
                continue

            # 在 element 子结构中查找（数组元素字段）
            if "element" in current and isinstance(current["element"], dict):
                if segment in current["element"]:
                    current = current["element"][segment]
                    continue

            # 全局 DSL 模式兜底：key 匹配 DSL pattern → 从 _dsl_rules 读取规则
            group = classify_dsl_key(segment)
            if group:
                rule = _global_dsl_rules.get(group)
                if rule:
                    if "_use_template" in rule:
                        resolved = _resolve_template(rule["_use_template"])
                        if resolved:
                            return resolved
                    return rule
                return {"type": None, "merge": "replace"}

            # 回退：直接在当前层级查找
            if segment in current:
                current = current[segment]
                continue

            # 找不到
            return None

    # 最终结果也可能是 _use_template 引用
    if isinstance(current, dict) and "_use_template" in current:
        resolved = _resolve_template(current["_use_template"])
        if resolved is not None:
            return resolved

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

    actual_type = get_type_str(actual_value)
    if isinstance(schema_type, list):
        return any(_type_compatible(st, actual_type, actual_value) for st in schema_type)
    return _type_compatible(schema_type, actual_type, actual_value)




def _type_compatible(schema_type: str, actual_type: str, actual_value) -> bool:
    """检查单个 schema 类型与实际类型是否兼容"""
    if schema_type == actual_type:
        return True

    # int 兼容 float
    if schema_type == "float" and actual_type == "int":
        return True

    # array<X> 或 array<X,Y> 匹配裸 array
    if schema_type.startswith("array") and actual_type == "array":
        return True

    # 多类型数组兼容性：actual 的元素类型应为 schema 声明类型的子集
    if schema_type.startswith("array<") and actual_type.startswith("array<"):
        schema_inner = set(schema_type[6:-1].split(","))
        actual_inner = set(actual_type[6:-1].split(","))
        for at in actual_inner:
            if at in schema_inner:
                continue
            # int 兼容 float
            if at == "int" and "float" in schema_inner:
                continue
            return False
        return True

    return False
