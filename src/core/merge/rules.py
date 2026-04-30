"""
智能合并删除规则 - 判定 SMART 模式下哪些 DELETED 操作允许执行

核心原则：保守策略——默认禁止删除，仅在能确认是 Mod 有意修改的上下文中允许。
"""

# 路径中包含这些段名时，内部字段允许删除
_ALLOW_DELETE_CONTEXTS: frozenset[str] = frozenset({
    "condition",
    "action",
    "result",
})

# 这些字段名本身允许删除（无论所处上下文）
_ALLOW_DELETE_FIELDS: frozenset[str] = frozenset({
    "result_title",
    "result_text",
})


def smart_allow_deletion(field_path: list[str], is_array_element: bool) -> bool:
    """判定 SMART 模式下某个 DELETED 操作是否允许执行。

    参数:
        field_path: 从文件根到当前字段的路径段列表（apply_delta 递归构建）
        is_array_element: 是否是数组中的元素（而非字典字段）

    返回:
        True 表示允许删除，False 表示禁止
    """
    # 数组元素一律禁止删除——防止版本落后的 Mod 误删游戏新增的元素
    if is_array_element:
        return False

    # 路径中包含 condition/action/result → Mod 有意修改逻辑，允许
    for segment in field_path:
        if segment in _ALLOW_DELETE_CONTEXTS:
            return True

    # 特定字段名允许删除
    if field_path and field_path[-1] in _ALLOW_DELETE_FIELDS:
        return True

    # 默认禁止
    return False
