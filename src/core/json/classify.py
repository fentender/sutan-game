"""
公共类型工具函数
"""

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
