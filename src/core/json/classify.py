"""
公共类型工具函数
"""
from typing import cast

from ..infra.types import *


def get_type_str(value: object) -> str:
    """获取 Python 值的类型字符串。

    DupList 是同名重复键的值集合，语义上每个元素等价于原始字段值，
    因此返回首个元素的类型而非 "array"。
    """
    if value is None:
        return "null"
    if isinstance(value, DupList):
        return get_type_str(value[0]) if value else "null"
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


def classify_json(data: JsonObject) -> str:
    """
    分类 JSON 文件类型。
    返回: "dictionary" | "entity" | "config"
    """
    if 'id' in data:
        return "entity"

    values = list(data.values())
    if values and all(isinstance(v, dict) for v in values):
        dicts = cast(list[JsonObject], values)
        if any('id' in d for d in dicts):
            return "dictionary"

    return "config"
