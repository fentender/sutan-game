"""
公共类型工具函数
"""


def get_type_str(value) -> str:
    """获取 Python 值的类型字符串"""
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


def classify_json(data) -> str:
    """
    分类 JSON 文件类型。
    返回: "dictionary" | "entity" | "config"
    """
    if not isinstance(data, dict):
        return "config"

    if 'id' in data:
        return "entity"

    keys = list(data.keys())
    if keys and all(isinstance(data[k], dict) for k in keys):
        if any('id' in data[k] for k in keys):
            return "dictionary"

    return "config"
