"""Schema 规则层 — 加载、生成、DSL 模式识别"""

from .dsl import classify_dsl_key as classify_dsl_key
from .generator import generate_all as generate_all
from .loader import (
    get_schema_root_key as get_schema_root_key,
    is_known_field as is_known_field,
    load_schemas as load_schemas,
    resolve_schema as resolve_schema,
)
