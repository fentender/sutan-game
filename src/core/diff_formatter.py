"""
Diff 格式化核心逻辑 — 行级 diff 计算 + DiffDict 结构化序列化

不依赖任何 GUI 模块，供 diff_dialog 和测试调用。
"""
import json

from .json_parser import _serialize
from .types import ArrayFieldDiff, ChangeKind, DiffDict, FieldDiff

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


def _serialize_value(val: object, indent: int, level: int) -> str:
    """将标量/DiffDict/ArrayFieldDiff 的值序列化为 JSON 文本（无 ChangeKind 追踪）。

    用于序列化 old_value（旧值不含注解信息，但可能是 DiffDict 或 ArrayFieldDiff）。
    """
    if isinstance(val, DiffDict):
        return _serialize_diffdict_plain(val, indent, level)
    if isinstance(val, ArrayFieldDiff):
        return _serialize_arraydiff_plain(val, indent, level)
    # 普通标量 / dict / list — 复用 json_parser._serialize（sort_keys=True）
    return _serialize(val, indent, sort_keys=True, _level=level)


def _serialize_diffdict_plain(dd: DiffDict, indent: int, level: int) -> str:
    """将 DiffDict 序列化为纯 JSON 文本（跳过 DELETED，不含注解）"""
    ind = ' ' * indent
    current_ind = ind * level
    next_ind = ind * (level + 1)

    parts: list[str] = []
    for key in sorted(dd.items.keys()):
        entry = dd.items[key]
        if isinstance(entry, FieldDiff):
            if entry.kind.base_kind == ChangeKind.DELETED:
                continue
            key_str = json.dumps(key, ensure_ascii=False)
            val_str = _serialize_value(entry.value, indent, level + 1)
            parts.append(f'{next_ind}{key_str}: {val_str}')
        elif isinstance(entry, DiffDict):
            key_str = json.dumps(key, ensure_ascii=False)
            val_str = _serialize_diffdict_plain(entry, indent, level + 1)
            parts.append(f'{next_ind}{key_str}: {val_str}')
        elif isinstance(entry, ArrayFieldDiff):
            key_str = json.dumps(key, ensure_ascii=False)
            if entry.is_duplist:
                for elem_str in _serialize_arraydiff_elements_plain(entry, indent, level + 1):
                    parts.append(f'{next_ind}{key_str}: {elem_str}')
            else:
                val_str = _serialize_arraydiff_plain(entry, indent, level + 1)
                parts.append(f'{next_ind}{key_str}: {val_str}')

    if not parts:
        return '{}'
    return '{\n' + ',\n'.join(parts) + '\n' + current_ind + '}'


def _serialize_arraydiff_plain(afd: ArrayFieldDiff, indent: int, level: int) -> str:
    """将 ArrayFieldDiff 序列化为纯 JSON 数组文本"""
    ind = ' ' * indent
    current_ind = ind * level
    next_ind = ind * (level + 1)

    id_to_diff = dict(zip(afd.indices, afd.diffs, strict=True))
    parts: list[str] = []
    for eid in afd.order:
        if eid == 0 or eid == -1:
            continue
        diff = id_to_diff.get(eid)
        if diff is None or diff.kind.base_kind == ChangeKind.DELETED:
            continue
        parts.append(next_ind + _serialize_value(diff.value, indent, level + 1))

    if not parts:
        return '[]'
    return '[\n' + ',\n'.join(parts) + '\n' + current_ind + ']'


def _serialize_arraydiff_elements_plain(
    afd: ArrayFieldDiff, indent: int, level: int,
) -> list[str]:
    """将 ArrayFieldDiff 的各元素序列化为独立字符串列表（用于 DupList 展开）"""
    id_to_diff = dict(zip(afd.indices, afd.diffs, strict=True))
    result: list[str] = []
    for eid in afd.order:
        if eid == 0 or eid == -1:
            continue
        diff = id_to_diff.get(eid)
        if diff is None or diff.kind.base_kind == ChangeKind.DELETED:
            continue
        result.append(_serialize_value(diff.value, indent, level))
    return result


# ==================== 结构化 Diff 格式化 ====================


def format_delta_json(
    delta: DiffDict,
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


def _emit_both(
    text: str,
    left_lines: list[str], right_lines: list[str],
    left_kinds: list[ChangeKind | None], right_kinds: list[ChangeKind | None],
    kind: ChangeKind | None = ChangeKind.ORIGIN,
) -> None:
    """将文本按行输出到左右两侧（相同内容）"""
    for line in text.split('\n'):
        left_lines.append(line)
        right_lines.append(line)
        left_kinds.append(kind)
        right_kinds.append(kind)


def _emit_left_only(
    text: str,
    left_lines: list[str], right_lines: list[str],
    left_kinds: list[ChangeKind | None], right_kinds: list[ChangeKind | None],
    kind: ChangeKind,
) -> None:
    """将文本按行输出到左侧，右侧补填充行"""
    for line in text.split('\n'):
        left_lines.append(line)
        right_lines.append('')
        left_kinds.append(kind)
        right_kinds.append(None)


def _emit_right_only(
    text: str,
    left_lines: list[str], right_lines: list[str],
    left_kinds: list[ChangeKind | None], right_kinds: list[ChangeKind | None],
    kind: ChangeKind,
) -> None:
    """将文本按行输出到右侧，左侧补填充行"""
    for line in text.split('\n'):
        left_lines.append('')
        right_lines.append(line)
        left_kinds.append(None)
        right_kinds.append(kind)


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
    entry: FieldDiff | DiffDict | ArrayFieldDiff,
    highlight_version: int,
) -> tuple[ChangeKind, bool]:
    """获取字段的 ChangeKind 和是否匹配当前版本。

    返回: (kind, is_current_version)
    - DiffDict/ArrayFieldDiff 类型的条目视为 ORIGIN（内部子字段递归处理）
    """
    if isinstance(entry, FieldDiff):
        if entry.version == highlight_version and entry.kind.base_kind != ChangeKind.ORIGIN:
            return entry.kind, True
        return ChangeKind.ORIGIN, False
    return ChangeKind.ORIGIN, False


def _format_diffdict(
    dd: DiffDict,
    highlight_version: int,
    level: int,
    indent: int,
    left_lines: list[str], right_lines: list[str],
    left_kinds: list[ChangeKind | None], right_kinds: list[ChangeKind | None],
    is_root: bool = False,
) -> None:
    """递归格式化 DiffDict，同步产出左右预对齐文本。"""
    ind = ' ' * indent
    current_ind = ind * level
    next_ind = ind * (level + 1)

    # 收集要输出的 key-entry 对
    entries: list[tuple[str, FieldDiff | DiffDict | ArrayFieldDiff]] = []
    for key in sorted(dd.items.keys()):
        entry = dd.items[key]
        if isinstance(entry, FieldDiff) and entry.kind.base_kind == ChangeKind.DELETED:
            if entry.version != highlight_version:
                continue
        entries.append((key, entry))

    if not entries and not is_root:
        _emit_both('{}', left_lines, right_lines, left_kinds, right_kinds)
        return

    _emit_both('{', left_lines, right_lines, left_kinds, right_kinds)

    for idx, (key, entry) in enumerate(entries):
        key_str = json.dumps(key, ensure_ascii=False)
        comma = ',' if idx < len(entries) - 1 else ''
        kind, is_current = _get_field_kind(entry, highlight_version)

        if isinstance(entry, FieldDiff):
            base_kind = entry.kind.base_kind if is_current else ChangeKind.ORIGIN

            if base_kind == ChangeKind.ORIGIN:
                val_str = _serialize_value(entry.value, indent, level + 1)
                text = f'{next_ind}{key_str}: {val_str}{comma}'
                _emit_both(text, left_lines, right_lines, left_kinds, right_kinds)

            elif base_kind == ChangeKind.ADDED:
                val_str = _serialize_value(entry.value, indent, level + 1)
                text = f'{next_ind}{key_str}: {val_str}{comma}'
                _emit_right_only(text, left_lines, right_lines, left_kinds, right_kinds, kind)

            elif base_kind == ChangeKind.DELETED:
                old_str = _serialize_value(entry.old_value, indent, level + 1)
                text = f'{next_ind}{key_str}: {old_str}{comma}'
                _emit_left_only(text, left_lines, right_lines, left_kinds, right_kinds, kind)

            elif base_kind == ChangeKind.CHANGED:
                old_str = _serialize_value(entry.old_value, indent, level + 1)
                new_str = _serialize_value(entry.value, indent, level + 1)
                old_text = f'{next_ind}{key_str}: {old_str}{comma}'
                new_text = f'{next_ind}{key_str}: {new_str}{comma}'
                _emit_changed(old_text, new_text,
                              left_lines, right_lines, left_kinds, right_kinds, kind)

        elif isinstance(entry, DiffDict):
            prefix = f'{next_ind}{key_str}: '
            sub_left: list[str] = []
            sub_right: list[str] = []
            sub_lk: list[ChangeKind | None] = []
            sub_rk: list[ChangeKind | None] = []
            _format_diffdict(entry, highlight_version, level + 1, indent,
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

        elif isinstance(entry, ArrayFieldDiff):
            if entry.is_duplist:
                _format_duplist_field(
                    key_str, entry, highlight_version, level, indent, comma,
                    left_lines, right_lines, left_kinds, right_kinds,
                )
            else:
                prefix = f'{next_ind}{key_str}: '
                sub_left2: list[str] = []
                sub_right2: list[str] = []
                sub_lk2: list[ChangeKind | None] = []
                sub_rk2: list[ChangeKind | None] = []
                _format_arraydiff(entry, highlight_version, level + 1, indent,
                                  sub_left2, sub_right2, sub_lk2, sub_rk2)
                if sub_left2:
                    sub_left2[0] = prefix + sub_left2[0]
                    sub_right2[0] = prefix + sub_right2[0]
                if sub_left2 and comma:
                    sub_left2[-1] += comma
                    sub_right2[-1] += comma
                left_lines.extend(sub_left2)
                right_lines.extend(sub_right2)
                left_kinds.extend(sub_lk2)
                right_kinds.extend(sub_rk2)

    _emit_both(current_ind + '}',
               left_lines, right_lines, left_kinds, right_kinds)


def _format_arraydiff(
    afd: ArrayFieldDiff,
    highlight_version: int,
    level: int,
    indent: int,
    left_lines: list[str], right_lines: list[str],
    left_kinds: list[ChangeKind | None], right_kinds: list[ChangeKind | None],
) -> None:
    """递归格式化 ArrayFieldDiff，同步产出左右预对齐文本。"""
    ind = ' ' * indent
    current_ind = ind * level
    next_ind = ind * (level + 1)

    id_to_diff = dict(zip(afd.indices, afd.diffs, strict=True))

    elements: list[tuple[int, FieldDiff]] = []
    for eid in afd.order:
        if eid == 0 or eid == -1:
            continue
        diff = id_to_diff.get(eid)
        if diff is None:
            continue
        if diff.kind.base_kind == ChangeKind.DELETED and diff.version != highlight_version:
            continue
        elements.append((eid, diff))

    deleted_elems: dict[int, FieldDiff] = {}
    if afd.old_order:
        for eid in afd.old_order:
            if eid == 0 or eid == -1:
                continue
            diff = id_to_diff.get(eid)
            if diff and diff.kind.base_kind == ChangeKind.DELETED and diff.version == highlight_version:
                deleted_elems[eid] = diff

    # 将删除元素按 old_order 位置插入到 elements 的正确位置
    insert_before: dict[int, list[tuple[int, FieldDiff]]] = {}
    trailing_deleted: list[tuple[int, FieldDiff]] = []
    if deleted_elems and afd.old_order:
        elem_id_set = {eid for eid, _ in elements}
        pending: list[tuple[int, FieldDiff]] = []
        for eid in afd.old_order:
            if eid == 0 or eid == -1:
                continue
            if eid in deleted_elems:
                pending.append((eid, deleted_elems[eid]))
            elif eid in elem_id_set and pending:
                insert_before.setdefault(eid, []).extend(pending)
                pending.clear()
        trailing_deleted = pending

    if not elements and not deleted_elems:
        _emit_both('[]', left_lines, right_lines, left_kinds, right_kinds)
        return

    _emit_both('[', left_lines, right_lines, left_kinds, right_kinds)

    # 计算总输出数（含删除元素）用于逗号判断
    total = len(elements) + sum(len(v) for v in insert_before.values()) + len(trailing_deleted)
    output_idx = 0

    for idx, (eid, diff) in enumerate(elements):
        # 先输出需要插入在此元素之前的删除元素
        for _del_eid, del_diff in insert_before.get(eid, []):
            del_comma = ',' if output_idx < total - 1 else ''
            del_kind, _ = _get_field_kind(del_diff, highlight_version)
            old_str = _serialize_value(del_diff.old_value, indent, level + 1)
            del_text = f'{next_ind}{old_str}{del_comma}'
            _emit_left_only(del_text, left_lines, right_lines, left_kinds, right_kinds, del_kind)
            output_idx += 1

        comma = ',' if output_idx < total - 1 else ''
        kind, is_current = _get_field_kind(diff, highlight_version)
        base_kind = diff.kind.base_kind if is_current else ChangeKind.ORIGIN

        if base_kind == ChangeKind.ORIGIN:
            val_str = _serialize_value(diff.value, indent, level + 1)
            text = f'{next_ind}{val_str}{comma}'
            _emit_both(text, left_lines, right_lines, left_kinds, right_kinds)

        elif base_kind == ChangeKind.ADDED:
            val_str = _serialize_value(diff.value, indent, level + 1)
            text = f'{next_ind}{val_str}{comma}'
            _emit_right_only(text, left_lines, right_lines, left_kinds, right_kinds, kind)

        elif base_kind == ChangeKind.DELETED:
            old_str = _serialize_value(diff.old_value, indent, level + 1)
            text = f'{next_ind}{old_str}{comma}'
            _emit_left_only(text, left_lines, right_lines, left_kinds, right_kinds, kind)

        elif base_kind == ChangeKind.CHANGED:
            if isinstance(diff.value, DiffDict):
                sub_left: list[str] = []
                sub_right: list[str] = []
                sub_lk: list[ChangeKind | None] = []
                sub_rk: list[ChangeKind | None] = []
                _format_diffdict(diff.value, highlight_version, level + 1, indent,
                                 sub_left, sub_right, sub_lk, sub_rk)
                if sub_left:
                    sub_left[0] = next_ind + sub_left[0]
                    sub_right[0] = next_ind + sub_right[0]
                if sub_left and comma:
                    sub_left[-1] += comma
                    sub_right[-1] += comma
                left_lines.extend(sub_left)
                right_lines.extend(sub_right)
                left_kinds.extend(sub_lk)
                right_kinds.extend(sub_rk)
            else:
                old_str = _serialize_value(diff.old_value, indent, level + 1)
                new_str = _serialize_value(diff.value, indent, level + 1)
                old_text = f'{next_ind}{old_str}{comma}'
                new_text = f'{next_ind}{new_str}{comma}'
                _emit_changed(old_text, new_text,
                              left_lines, right_lines, left_kinds, right_kinds, kind)

        output_idx += 1

    # 末尾删除元素（old_order 尾部无锚点的删除元素）
    for i, (_del_eid, del_diff) in enumerate(trailing_deleted):
        del_comma = ',' if output_idx < total - 1 else ''
        del_kind, _ = _get_field_kind(del_diff, highlight_version)
        old_str = _serialize_value(del_diff.old_value, indent, level + 1)
        del_text = f'{next_ind}{old_str}{del_comma}'
        _emit_left_only(del_text, left_lines, right_lines, left_kinds, right_kinds, del_kind)
        output_idx += 1

    _emit_both(current_ind + ']',
               left_lines, right_lines, left_kinds, right_kinds)


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
    next_ind = ' ' * indent * (level + 1)
    id_to_diff = dict(zip(afd.indices, afd.diffs, strict=True))

    elements: list[tuple[int, FieldDiff]] = []
    for eid in afd.order:
        if eid == 0 or eid == -1:
            continue
        diff = id_to_diff.get(eid)
        if diff is None:
            continue
        if diff.kind.base_kind == ChangeKind.DELETED and diff.version != highlight_version:
            continue
        elements.append((eid, diff))

    for idx, (_eid, diff) in enumerate(elements):
        is_last_elem = idx == len(elements) - 1
        comma = trailing_comma if is_last_elem else ','

        kind, is_current = _get_field_kind(diff, highlight_version)
        base_kind = diff.kind.base_kind if is_current else ChangeKind.ORIGIN

        if base_kind == ChangeKind.ORIGIN:
            val_str = _serialize_value(diff.value, indent, level + 1)
            text = f'{next_ind}{key_str}: {val_str}{comma}'
            _emit_both(text, left_lines, right_lines, left_kinds, right_kinds)
        elif base_kind == ChangeKind.ADDED:
            val_str = _serialize_value(diff.value, indent, level + 1)
            text = f'{next_ind}{key_str}: {val_str}{comma}'
            _emit_right_only(text, left_lines, right_lines, left_kinds, right_kinds, kind)
        elif base_kind == ChangeKind.DELETED:
            old_str = _serialize_value(diff.old_value, indent, level + 1)
            text = f'{next_ind}{key_str}: {old_str}{comma}'
            _emit_left_only(text, left_lines, right_lines, left_kinds, right_kinds, kind)
        elif base_kind == ChangeKind.CHANGED:
            old_str = _serialize_value(diff.old_value, indent, level + 1)
            new_str = _serialize_value(diff.value, indent, level + 1)
            old_text = f'{next_ind}{key_str}: {old_str}{comma}'
            new_text = f'{next_ind}{key_str}: {new_str}{comma}'
            _emit_changed(old_text, new_text,
                          left_lines, right_lines, left_kinds, right_kinds, kind)
