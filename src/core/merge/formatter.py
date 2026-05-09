"""
Diff 格式化核心逻辑 — 行级 diff 计算

不依赖任何 GUI 模块，供 diff_dialog 调用。
"""

# ==================== 行级 diff 计算 ====================


def _normalize_for_diff(line: str) -> str:
    """去除行尾逗号，避免 JSON 数组元素追加/删除时的纯格式差异被识别为内容修改"""
    stripped = line.rstrip()
    if stripped.endswith(','):
        return stripped[:-1]
    return stripped


def _intern_lines(lines: list[str], table: dict[str, int]) -> list[int]:
    """将字符串行列表映射为整数 ID 列表，共享 table 跨多次调用复用。
    对行做尾逗号标准化，使 JSON 格式差异不影响 diff 结果。"""
    ids: list[int] = []
    for line in lines:
        key = _normalize_for_diff(line)
        if key not in table:
            table[key] = len(table)
        ids.append(table[key])
    return ids


def _fast_opcodes(a_ids: list[int], b_ids: list[int]) -> list[tuple[str, int, int, int, int]]:
    """使用 rapidfuzz C++ 后端计算 diff opcodes（行哈希整数序列输入）。
    Indel 只产出 equal/delete/insert，此函数将相邻 delete+insert 合并为 replace
    以保持与 difflib 兼容的语义。"""
    from rapidfuzz.distance import Indel

    raw = Indel.opcodes(a_ids, b_ids)

    opcodes: list[tuple[str, int, int, int, int]] = []
    i = 0
    n = len(raw)
    while i < n:
        op = raw[i]
        tag = op.tag
        if tag == "delete" and i + 1 < n and raw[i + 1].tag == "insert":
            nxt = raw[i + 1]
            opcodes.append(("replace", op.src_start, op.src_end,
                            nxt.dest_start, nxt.dest_end))
            i += 2
        elif tag == "insert" and i + 1 < n and raw[i + 1].tag == "delete":
            nxt = raw[i + 1]
            opcodes.append(("replace", nxt.src_start, nxt.src_end,
                            op.dest_start, op.dest_end))
            i += 2
        else:
            opcodes.append((tag, op.src_start, op.src_end,
                            op.dest_start, op.dest_end))
            i += 1
    return opcodes


def diff_opcodes(a_lines: list[str], b_lines: list[str]) -> list[tuple[str, int, int, int, int]]:
    """行哈希 + rapidfuzz C++ 后端 diff — 31000 行文件仅需 ~1ms"""
    table: dict[str, int] = {}
    a_ids = _intern_lines(a_lines, table)
    b_ids = _intern_lines(b_lines, table)
    return _fast_opcodes(a_ids, b_ids)


def build_padded_texts(
    left_lines: list[str],
    right_lines: list[str],
    opcodes: list[tuple[str, int, int, int, int]],
) -> tuple[
    list[str], list[str],
    list[int | None], list[int | None],
    dict[int, int], dict[int, int],
]:
    """根据 opcodes 在行数少的一侧插入空行，使两侧总行数一致。

    返回:
        padded_left, padded_right: 填充后的行列表
        left_map, right_map: padded_index → 原始行号(0-based)|None
        left_o2p, right_o2p: 原始行号 → padded_index
    """
    padded_left: list[str] = []
    padded_right: list[str] = []
    left_map: list[int | None] = []
    right_map: list[int | None] = []
    left_o2p: dict[int, int] = {}
    right_o2p: dict[int, int] = {}

    for tag, i1, i2, j1, j2 in opcodes:
        left_count = i2 - i1
        right_count = j2 - j1

        if tag == "equal":
            for k in range(left_count):
                idx = len(padded_left)
                left_o2p[i1 + k] = idx
                right_o2p[j1 + k] = idx
                padded_left.append(left_lines[i1 + k])
                padded_right.append(right_lines[j1 + k])
                left_map.append(i1 + k)
                right_map.append(j1 + k)

        elif tag == "insert":
            for k in range(right_count):
                idx = len(padded_left)
                right_o2p[j1 + k] = idx
                padded_left.append("")
                padded_right.append(right_lines[j1 + k])
                left_map.append(None)
                right_map.append(j1 + k)

        elif tag == "delete":
            for k in range(left_count):
                idx = len(padded_left)
                left_o2p[i1 + k] = idx
                padded_left.append(left_lines[i1 + k])
                padded_right.append("")
                left_map.append(i1 + k)
                right_map.append(None)

        elif tag == "replace":
            max_count = max(left_count, right_count)
            for k in range(max_count):
                idx = len(padded_left)
                if k < left_count:
                    left_o2p[i1 + k] = idx
                    padded_left.append(left_lines[i1 + k])
                    left_map.append(i1 + k)
                else:
                    padded_left.append("")
                    left_map.append(None)
                if k < right_count:
                    right_o2p[j1 + k] = idx
                    padded_right.append(right_lines[j1 + k])
                    right_map.append(j1 + k)
                else:
                    padded_right.append("")
                    right_map.append(None)

    assert len(padded_left) == len(padded_right)
    return padded_left, padded_right, left_map, right_map, left_o2p, right_o2p
