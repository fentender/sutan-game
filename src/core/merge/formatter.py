"""
Diff 格式化核心逻辑 — 行级 diff 计算 + DiffDict 结构化序列化

不依赖任何 GUI 模块，供 diff_dialog 和测试调用。
"""
import json
from typing import Literal

from ..infra.types import ArrayFieldDiff, ChangeKind, DiffEntry, DictFieldDiff, FieldDiff
from ..json.parser import _serialize

# json.dumps 缓存（key 字符串 → JSON 编码后的字符串）
_json_key_cache: dict[str, str] = {}


def _dumps_key(key: str) -> str:
    """缓存版 json.dumps，用于 dict key 序列化"""
    cached = _json_key_cache.get(key)
    if cached is not None:
        return cached
    result = json.dumps(key, ensure_ascii=False)
    _json_key_cache[key] = result
    return result

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

    # 合并相邻 delete+insert 为 replace
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


# ==================== DiffDict 序列化（纯文本，无高亮注解） ====================


def _serialize_entry(entry: DiffEntry, indent: int, level: int) -> str:
    """将 DiffEntry 序列化为 JSON 文本（无 ChangeKind 追踪）"""
    assert isinstance(entry, (FieldDiff, DictFieldDiff, ArrayFieldDiff))
    if isinstance(entry, FieldDiff):
        return _serialize(entry.value, indent, sort_keys=True, _level=level)
    if isinstance(entry, DictFieldDiff):
        return _serialize_dictdiff_plain(entry, indent, level)
    return _serialize_arraydiff_plain(entry, indent, level)


def _iter_active_elements(afd: ArrayFieldDiff) -> list[DiffEntry]:
    """返回数组中按 order 排列的非 DELETED 有效元素"""
    id_to_diff = dict(zip(afd.indices, afd.diffs, strict=True))
    result: list[DiffEntry] = []
    for eid in afd.order:
        if eid == 0 or eid == -1:
            continue
        diff = id_to_diff.get(eid)
        if diff is None:
            continue
        if isinstance(diff, FieldDiff) and diff.kind.is_deleted:
            continue
        result.append(diff)
    return result


def _serialize_dictdiff_plain(dd: DictFieldDiff, indent: int, level: int) -> str:
    """将 DiffDict 序列化为纯 JSON 文本（跳过 DELETED，不含注解）"""
    ind = ' ' * indent
    current_ind = ind * level
    next_ind = ind * (level + 1)

    parts: list[str] = []
    for key in sorted(dd.items.keys()):
        entry = dd.items[key]
        if isinstance(entry, FieldDiff) and entry.kind.is_deleted:
            continue
        key_str = _dumps_key(key)
        if isinstance(entry, ArrayFieldDiff) and entry.is_duplist:
            for diff in _iter_active_elements(entry):
                parts.append(f'{next_ind}{key_str}: {_serialize_entry(diff, indent, level + 1)}')
        else:
            parts.append(f'{next_ind}{key_str}: {_serialize_entry(entry, indent, level + 1)}')

    if not parts:
        return '{}'
    return '{\n' + ',\n'.join(parts) + '\n' + current_ind + '}'


def _serialize_arraydiff_plain(afd: ArrayFieldDiff, indent: int, level: int) -> str:
    """将 ArrayFieldDiff 序列化为纯 JSON 数组文本"""
    ind = ' ' * indent
    current_ind = ind * level
    next_ind = ind * (level + 1)

    parts = [next_ind + _serialize_entry(diff, indent, level + 1)
             for diff in _iter_active_elements(afd)]

    if not parts:
        return '[]'
    return '[\n' + ',\n'.join(parts) + '\n' + current_ind + ']'


# ==================== 结构化 Diff 格式化 ====================


def format_delta_json(
    delta: DictFieldDiff,
    highlight_version: int,
) -> tuple[list[str], list[str], list[ChangeKind | None], list[ChangeKind | None]]:
    """将全状态 DiffDict 序列化为预对齐的左右文本 + 每行高亮类型。

    根据 highlight_version 过滤：只有 version == highlight_version 的字段参与高亮，
    其他字段视为 ORIGIN（输出到两侧，无高亮）。

    返回:
        left_lines: 变更前文本（按行）
        right_lines: 变更后文本（按行）
        left_line_kinds: 每行的 ChangeKind（None=填充行）
        right_line_kinds: 每行的 ChangeKind（None=填充行）

    len(left_lines) == len(right_lines)——预对齐。
    """
    left_lines: list[str] = []
    right_lines: list[str] = []
    left_kinds: list[ChangeKind | None] = []
    right_kinds: list[ChangeKind | None] = []

    _format_diffdict(
        delta, highlight_version, 0, 4,
        left_lines, right_lines, left_kinds, right_kinds,
        is_root=True,
    )

    return left_lines, right_lines, left_kinds, right_kinds


def _emit(
    text: str,
    left_lines: list[str], right_lines: list[str],
    left_kinds: list[ChangeKind | None], right_kinds: list[ChangeKind | None],
    kind: ChangeKind | None = ChangeKind.ORIGIN,
    side: Literal["both", "left", "right"] = "both",
) -> None:
    show_left = side != "right"
    show_right = side != "left"
    lk = kind if show_left else None
    rk = kind if show_right else None
    lines = text.split('\n') if '\n' in text else [text]
    for line in lines:
        left_lines.append(line if show_left else '')
        right_lines.append(line if show_right else '')
        left_kinds.append(lk)
        right_kinds.append(rk)


def _emit_changed(
    old_text: str, new_text: str,
    left_lines: list[str], right_lines: list[str],
    left_kinds: list[ChangeKind | None], right_kinds: list[ChangeKind | None],
    kind: ChangeKind,
) -> None:
    """将旧值输出到左侧、新值输出到右侧，短侧补填充行"""
    old_split = old_text.split('\n')
    new_split = new_text.split('\n')
    max_len = max(len(old_split), len(new_split))
    for i in range(max_len):
        if i < len(old_split):
            left_lines.append(old_split[i])
            left_kinds.append(kind)
        else:
            left_lines.append('')
            left_kinds.append(None)
        if i < len(new_split):
            right_lines.append(new_split[i])
            right_kinds.append(kind)
        else:
            right_lines.append('')
            right_kinds.append(None)


def _get_field_kind(
    entry: DiffEntry,
    highlight_version: int,
) -> tuple[ChangeKind, bool]:
    """获取字段的 ChangeKind 和是否匹配当前版本。

    返回: (kind, is_current_version)
    - DiffDict/ArrayFieldDiff 类型的条目视为 ORIGIN（内部子字段递归处理）
    """
    if isinstance(entry, FieldDiff):
        if entry.version == highlight_version and not entry.kind.is_origin:
            return entry.kind, True
        return ChangeKind.ORIGIN, False
    return ChangeKind.ORIGIN, False


def _collect_dict_entries(dd: DictFieldDiff, highlight_version: int) -> list[tuple[str, DiffEntry]]:
    entries: list[tuple[str, DiffEntry]] = []
    for key in sorted(dd.items.keys()):
        entry = dd.items[key]
        if isinstance(entry, FieldDiff) and entry.kind.is_deleted and entry.version != highlight_version:
            continue
        entries.append((key, entry))
    return entries


def _collect_array_elements(afd: ArrayFieldDiff, highlight_version: int) -> list[tuple[int, DiffEntry]]:
    id_to_diff = dict(zip(afd.indices, afd.diffs, strict=True))
    elements: list[tuple[int, DiffEntry]] = []
    for eid in afd.order:
        if eid == 0 or eid == -1:
            continue
        diff = id_to_diff[eid]
        if isinstance(diff, FieldDiff) and diff.kind.is_deleted and diff.version != highlight_version:
            continue
        elements.append((eid, diff))
    return elements


def _format_entry(
    entry: DiffEntry,
    prefix: str,
    comma: str,
    highlight_version: int,
    level: int,
    indent: int,
    left_lines: list[str], right_lines: list[str],
    left_kinds: list[ChangeKind | None], right_kinds: list[ChangeKind | None],
) -> None:
    if isinstance(entry, FieldDiff):
        kind, is_current = _get_field_kind(entry, highlight_version)
        dk = entry.kind if is_current else ChangeKind.ORIGIN
        if dk.is_origin:
            val_str = _serialize(entry.value, indent, sort_keys=True, _level=level)
            _emit(f'{prefix}{val_str}{comma}', left_lines, right_lines, left_kinds, right_kinds)
        else:
            has_old = entry.old_value is not None
            has_new = entry.value is not None
            if has_old and has_new and entry.old_value != entry.value:
                hl = ChangeKind.CHANGED | kind.flags
                old_str = _serialize(entry.old_value, indent, sort_keys=True, _level=level)
                new_str = _serialize(entry.value, indent, sort_keys=True, _level=level)
                _emit_changed(f'{prefix}{old_str}{comma}', f'{prefix}{new_str}{comma}',
                              left_lines, right_lines, left_kinds, right_kinds, hl)
            elif has_new and not has_old:
                hl = ChangeKind.ADDED | kind.flags
                val_str = _serialize(entry.value, indent, sort_keys=True, _level=level)
                _emit(f'{prefix}{val_str}{comma}', left_lines, right_lines, left_kinds, right_kinds, hl, "right")
            elif has_old and not has_new:
                hl = ChangeKind.DELETED | kind.flags
                old_str = _serialize(entry.old_value, indent, sort_keys=True, _level=level)
                _emit(f'{prefix}{old_str}{comma}', left_lines, right_lines, left_kinds, right_kinds, hl, "left")
            else:
                val_str = _serialize(entry.value if has_new else entry.old_value, indent, sort_keys=True, _level=level)
                _emit(f'{prefix}{val_str}{comma}', left_lines, right_lines, left_kinds, right_kinds)
    else:
        sub_left: list[str] = []
        sub_right: list[str] = []
        sub_lk: list[ChangeKind | None] = []
        sub_rk: list[ChangeKind | None] = []
        if isinstance(entry, DictFieldDiff):
            _format_diffdict(entry, highlight_version, level, indent,
                             sub_left, sub_right, sub_lk, sub_rk)
        else:
            _format_arraydiff(entry, highlight_version, level, indent,
                              sub_left, sub_right, sub_lk, sub_rk)
        if sub_left:
            sub_left[0] = prefix + sub_left[0]
            sub_right[0] = prefix + sub_right[0]
        if sub_left and comma:
            sub_left[-1] += comma
            sub_right[-1] += comma
        left_lines.extend(sub_left)
        right_lines.extend(sub_right)
        left_kinds.extend(sub_lk)
        right_kinds.extend(sub_rk)


def _format_diffdict(
    dd: DictFieldDiff,
    highlight_version: int,
    level: int,
    indent: int,
    left_lines: list[str], right_lines: list[str],
    left_kinds: list[ChangeKind | None], right_kinds: list[ChangeKind | None],
    is_root: bool = False,
) -> None:
    """递归格式化 DiffDict，同步产出左右预对齐文本。"""
    current_ind = ' ' * (indent * level)
    next_ind = ' ' * (indent * (level + 1))

    entries = _collect_dict_entries(dd, highlight_version)

    if not entries and not is_root:
        _emit('{}', left_lines, right_lines, left_kinds, right_kinds)
        return
    _emit('{', left_lines, right_lines, left_kinds, right_kinds)

    for idx, (key, entry) in enumerate(entries):
        key_str = _dumps_key(key)
        comma = ',' if idx < len(entries) - 1 else ''
        if isinstance(entry, ArrayFieldDiff) and entry.is_duplist:
            _format_duplist_field(key_str, entry, highlight_version, level, indent, comma,
                                 left_lines, right_lines, left_kinds, right_kinds)
        else:
            _format_entry(entry, f'{next_ind}{key_str}: ', comma, highlight_version, level + 1, indent,
                          left_lines, right_lines, left_kinds, right_kinds)

    _emit(current_ind + '}', left_lines, right_lines, left_kinds, right_kinds)


def _format_arraydiff(
    afd: ArrayFieldDiff,
    highlight_version: int,
    level: int,
    indent: int,
    left_lines: list[str], right_lines: list[str],
    left_kinds: list[ChangeKind | None], right_kinds: list[ChangeKind | None],
) -> None:
    """递归格式化 ArrayFieldDiff，同步产出左右预对齐文本。"""
    current_ind = ' ' * (indent * level)
    next_ind = ' ' * (indent * (level + 1))

    elements = _collect_array_elements(afd, highlight_version)
    if not elements:
        _emit('[]', left_lines, right_lines, left_kinds, right_kinds)
        return
    _emit('[', left_lines, right_lines, left_kinds, right_kinds)

    for idx, (_eid, entry) in enumerate(elements):
        comma = ',' if idx < len(elements) - 1 else ''
        _format_entry(entry, next_ind, comma, highlight_version, level + 1, indent,
                      left_lines, right_lines, left_kinds, right_kinds)

    _emit(current_ind + ']', left_lines, right_lines, left_kinds, right_kinds)


def _format_duplist_field(
    key_str: str,
    afd: ArrayFieldDiff,
    highlight_version: int,
    level: int,
    indent: int,
    trailing_comma: str,
    left_lines: list[str], right_lines: list[str],
    left_kinds: list[ChangeKind | None], right_kinds: list[ChangeKind | None],
) -> None:
    """格式化 DupList 字段：每个元素展开为独立 key: value 行"""
    next_ind = ' ' * (indent * (level + 1))
    elements = _collect_array_elements(afd, highlight_version)

    for idx, (_eid, entry) in enumerate(elements):
        comma = trailing_comma if idx == len(elements) - 1 else ','
        _format_entry(entry, f'{next_ind}{key_str}: ', comma, highlight_version, level + 1, indent,
                      left_lines, right_lines, left_kinds, right_kinds)
