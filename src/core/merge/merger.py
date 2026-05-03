"""
核心合并算法 - 字典合并、实体合并、数组智能匹配

delta 产出使用强类型 DiffDict / ArrayFieldDiff / FieldDiff 树，
替代旧的 _DELETED 哨兵和 _delta/_new_entry/_deleted 魔法标记。
"""
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from ..infra.diagnostics import diag, merge_ctx
from ..infra.profiler import profile
from ..infra.types import *
from ..json.parser import dump_json
from ..json.store import JsonStore
from ..schema.loader import get_schema_root_key

# 需要整文件替换而非合并的文件
WHOLE_FILE_REPLACE = {'sfx_config.json'}


@dataclass
class MergeResult:
    """合并结果"""
    merged_data: JsonObject = field(default_factory=dict)

# ==================== 合并层 ====================


def _build_warn_msg(field_path: tuple[str, ...] | None, msg: str) -> str:
    """拼接合并警告消息，从 merge_ctx 读取 mod 名称和文件路径"""
    parts: list[str] = []
    if merge_ctx.mod_name:
        parts.append(f"[{merge_ctx.mod_name}]")
    if merge_ctx.source_file:
        parts.append(merge_ctx.source_file)
    elif merge_ctx.rel_path:
        parts.append(merge_ctx.rel_path)
    if field_path:
        parts.append(".".join(field_path))
    prefix = " > ".join(parts)
    return f"{prefix}: {msg}" if prefix else msg


def _prepare_array_delta(
    base: ArrayFieldDiff,
    delta: ArrayFieldDiff,
    is_override: bool,
) -> ArrayFieldDiff:
    """统一 delta 的 indices/order 到全状态 element ID 空间。

    - override: flat position → element ID，ADDED 元素分配新 ID
    - 非 override: ADDED 元素 ID 重分配（避免跨 Mod 冲突）
    """
    id_remap: dict[int, int] = {}

    if is_override:
        flat_order = [eid for eid in base.order if eid not in (0, -1)]
        flat_to_eid: dict[int, int] = {
            pos + 1: eid for pos, eid in enumerate(flat_order)
        }
        id_remap = flat_to_eid
    else:
        cur_max = max(base.indices, default=0)
        next_id = cur_max + 1

        for diff, orig_id in zip(delta.diffs, delta.indices, strict=True):
            if isinstance(diff, FieldDiff) and diff.kind == ChangeKind.ADDED:
                id_remap[orig_id] = next_id
                next_id += 1
            elif orig_id > delta.base_count:
                raise ValueError(
                    f"CHANGED/DELETED 的 ID {orig_id} 超过 base_count {delta.base_count}")

    if not id_remap:
        return delta

    return ArrayFieldDiff(
        diffs=delta.diffs,
        base_count=delta.base_count,
        indices=[id_remap.get(i, i) for i in delta.indices],
        order=[id_remap.get(i, i) for i in delta.order],
        is_duplist=delta.is_duplist,
        old_order=None, kind=delta.kind,
    )



def _rebuild_array_order(
    base: ArrayFieldDiff,
    delta: ArrayFieldDiff,
) -> list[int]:
    """基于 delta.order 重建 base 的元素顺序。

    delta.order 定义了已知元素的目标顺序。
    不在 delta.order 中的元素（orphan）保留在其最近锚点之后。
    """
    orphans = set(base.indices) - set(delta.order)

    after: dict[int, list[int]] = {}
    last_anchor = 0
    for eid in base.order:
        if eid in orphans:
            after.setdefault(last_anchor, []).append(eid)
        else:
            last_anchor = eid

    new_order: list[int] = []
    for eid in delta.order:
        new_order.append(eid)
        for ob_id in after.get(eid, []):
            new_order.append(ob_id)
    return new_order


def apply_array_delta(
    base: ArrayFieldDiff,
    delta: ArrayFieldDiff,
    field_path: tuple[str, ...] | None = None,
    version: int = 0,
    is_override: bool = False,
) -> ArrayFieldDiff:
    """将 ArrayFieldDiff delta 应用到全状态 ArrayFieldDiff 上，原地修改并返回。

    参数:
        base: 全状态 ArrayFieldDiff（所有元素带 ChangeKind 注解）
        delta: 稀疏 ArrayFieldDiff（仅变化的元素）
        version: 当前 mod 迭代版本号
        is_override: True 时为用户手动覆写，标记 OVERRIDE 且不触发 MULTI_MOD
    """
    base.kind = delta.kind
    delta = _prepare_array_delta(base, delta, is_override)

    id_map: dict[int, int] = {eid: pos for pos, eid in enumerate(base.indices)}

    for diff, elem_id in zip(delta.diffs, delta.indices, strict=True):
        pos = id_map.get(elem_id)
        existing = base.diffs[pos] if pos is not None else None

        applied = _apply_delta_entry(diff, existing, field_path, version, is_override)
        if applied is None:
            continue
        if pos is None:
            base.diffs.append(applied)
            base.indices.append(elem_id)
        else:
            base.diffs[pos] = applied

    base.old_order = list(base.order)
    base.order = _rebuild_array_order(base, delta)
    return base


@profile
def apply_field_delta(
    diff: FieldDiff,
    existing: FieldDiff | None,
    version: int,
    is_override: bool,
) -> FieldDiff:
    base_kind = diff.kind.base_kind

    modifier = ChangeKind.ORIGIN
    if is_override:
        modifier |= ChangeKind.OVERRIDE
    if existing is not None:
        old_val: JsonValue = existing.value
        if existing.is_modified and existing.version != version:
            modifier |= ChangeKind.MULTI_MOD
        modifier |= existing.kind.flags
    else:
        old_val: JsonValue = None
    kind = base_kind | modifier

    return FieldDiff(kind, diff.value, old_value=old_val, version=version)


def _apply_delta_entry(
    diff: DiffEntry,
    existing: DiffEntry | None,
    field_path: tuple[str, ...] | None,
    version: int,
    is_override: bool,
) -> DiffEntry | None:
    """归一化、类型校验、分派。返回合并后的值，None 表示类型校验失败。"""
    if isinstance(diff, FieldDiff) and isinstance(existing, DictFieldDiff) and isinstance(diff.old_value, dict):
        if not (diff.old_value is not None and diff.kind.base_kind == ChangeKind.DELETED):
            print(str(field_path) + " " + str(diff) + " " + str(existing))
        assert diff.old_value is not None and diff.kind.base_kind == ChangeKind.DELETED
        diff = DictFieldDiff(items={
            k: FieldDiff(diff.kind, None, old_value=v) for k, v in diff.old_value.items()
        }, kind=ChangeKind.DELETED)
    elif isinstance(diff, FieldDiff) and isinstance(existing, ArrayFieldDiff) and isinstance(diff.old_value, list):
        assert diff.old_value is not None and diff.kind.base_kind == ChangeKind.DELETED
        elems = diff.old_value
        n = len(elems)
        diff = ArrayFieldDiff(
            diffs=[FieldDiff(diff.kind, None, old_value=elem) for elem in elems],
            base_count=n,
            indices=list(range(1, n + 1)),
            order=[0, *range(1, n + 1), -1],
            is_duplist=existing.is_duplist,
            old_order=None, kind=ChangeKind.DELETED,
        )

    if isinstance(diff, ArrayFieldDiff) and isinstance(existing, FieldDiff):
        existing = ArrayFieldDiff.wrap(existing, is_dup=diff.is_duplist)
    elif isinstance(existing, ArrayFieldDiff) and isinstance(diff, FieldDiff):
        diff = ArrayFieldDiff.wrap(diff, is_dup=existing.is_duplist)

    if existing is not None and type(diff) is not type(existing):
        diag.error("merge", _build_warn_msg(field_path,
            "字段类型不匹配，该 Mod 可能基于旧版本游戏制作"))
        return None
    if existing is None and isinstance(diff, (DictFieldDiff, ArrayFieldDiff)):
        diag.error("merge", _build_warn_msg(field_path,
            "字段在本体中不存在，该 Mod 可能基于旧版本游戏制作"))
        return None

    if isinstance(diff, FieldDiff):
        assert isinstance(existing, FieldDiff) or existing is None
        return apply_field_delta(
            diff, cast(FieldDiff | None, existing),
            version, is_override)

    if isinstance(diff, DictFieldDiff):
        assert isinstance(existing, DictFieldDiff)
        apply_dict_delta(existing, diff, field_path,
                         version=version, is_override=is_override)
        return existing

    assert isinstance(diff, ArrayFieldDiff) and isinstance(existing, ArrayFieldDiff)
    apply_array_delta(existing, diff, field_path,
                      version=version, is_override=is_override)
    return existing


@profile
def apply_dict_delta(
    base: DictFieldDiff,
    delta: DictFieldDiff,
    field_path: tuple[str, ...] | None = None,
    version: int = 0,
    is_override: bool = False,
) -> DictFieldDiff:
    """将 DiffDict delta 应用到全状态 DiffDict 上，原地修改并返回。

    参数:
        base: 全状态 DiffDict（所有字段带 ChangeKind 注解）
        delta: 稀疏 DiffDict（仅变化的字段）
        version: 当前 mod 迭代版本号
        is_override: True 时为用户手动覆写，标记 OVERRIDE 且不触发 MULTI_MOD
    """
    base.kind = delta.kind

    for key, diff in delta.items.items():
        child_path = field_path + (key,) if field_path is not None else None
        existing = base.items.get(key)

        existing_duplist = isinstance(existing, ArrayFieldDiff) and existing.is_duplist
        diff_duplist = isinstance(diff, ArrayFieldDiff) and diff.is_duplist
        if existing_duplist and not diff_duplist:
            diff = ArrayFieldDiff.wrap(diff, is_dup=True)
        elif diff_duplist and not existing_duplist:
            existing = ArrayFieldDiff.wrap(existing, is_dup=True)
            base.items[key] = existing

        applied = _apply_delta_entry(diff, existing, child_path, version, is_override)
        if applied is None:
            continue
        base.items[key] = applied

    return base


# ==================== 文件级合并 ====================


def apply_mod_deltas(
    current: DictFieldDiff,
    mod_data_list: list[tuple[str, str, DictFieldDiff, str]],
    field_path: tuple[str, ...] | None,
    rel_path: str,
    *,
    step_cb: Callable[[str, str, DictFieldDiff, int], None] | None = None,
) -> None:
    """逐 Mod 合并循环：设置上下文 → 应用 delta → 应用 override → 回调。

    参数:
        mod_data_list: [(mod_id, mod_name, delta, source_file), ...] 按优先级排序
        step_cb: 每步合并后的回调 (mod_id, mod_name, current, version)
    """
    for version, (mod_id, mod_name, delta, source_file) in enumerate(mod_data_list, 1):
        merge_ctx.mod_name = mod_name
        merge_ctx.mod_id = mod_id
        merge_ctx.rel_path = rel_path
        merge_ctx.source_file = source_file

        apply_dict_delta(current, delta, field_path, version=version)

        override_delta = JsonStore.instance().get_override(mod_id, rel_path)
        if override_delta is not None:
            apply_dict_delta(current, override_delta, field_path,
                             version=version, is_override=True)

        if step_cb is not None:
            step_cb(mod_id, mod_name, current, version)


@profile
def merge_file(
    base_data: JsonObject,
    mod_data_list: list[tuple[str, str, DictFieldDiff, str]],
    rel_path: str = "",
    schema: JsonObject | None = None,
) -> MergeResult:
    """合并单个文件。

    参数:
        base_data: 游戏本体的 JSON 数据
        mod_data_list: [(mod_id, mod_name, delta, source_file), ...] 按优先级排序
        rel_path: 文件相对路径（用于判断特殊文件）
        schema: 仅用于获取 root_key
    """
    result = MergeResult()
    file_name = Path(rel_path).name if rel_path else ""

    if file_name in WHOLE_FILE_REPLACE:
        if mod_data_list:
            _, last_mod_name, _, _ = mod_data_list[-1]
            current = DictFieldDiff.from_dict(base_data)
            for step, (_, _, delta, _) in enumerate(mod_data_list, 1):
                apply_dict_delta(current, delta, None, version=step)
            result.merged_data = current.to_dict()
            if len(mod_data_list) > 1:
                diag.warn("merge", f"{rel_path}: 多个 mod 修改此文件（整文件替换模式），最终使用 {last_mod_name}")
        else:
            result.merged_data = dict(base_data)
        return result

    current = DictFieldDiff.from_dict(base_data)
    root_key = get_schema_root_key(schema) if schema else None
    fp: tuple[str, ...] | None = (root_key,) if root_key else None

    apply_mod_deltas(current, mod_data_list, fp, rel_path)

    result.merged_data = current.to_dict()
    return result


@profile
def merge_all_files(
    mod_configs: list[tuple[str, str, Path]],
    output_path: Path,
    schema_dir: Path | None = None,
    cancel_check: CancelCheck | None = None,
    progress_cb: ProgressCallback | None = None,
) -> dict[str, MergeResult]:
    """合并所有文件。

    参数:
        mod_configs: [(mod_id, mod_name, mod_config_path), ...] 按优先级排序
        output_path: 输出目录
        schema_dir: schema 规则文件目录
        cancel_check: 可选的取消检查回调
    """
    diag.snapshot("merge")

    store = JsonStore.instance()
    from .cache import MergeCache
    cache = MergeCache.instance()
    mod_ids = [mod_id for mod_id, _, _ in mod_configs]

    results: dict[str, MergeResult] = {}

    all_paths = sorted(store.all_rel_paths())
    total = len(all_paths)
    for i, rel_path in enumerate(all_paths):
        if cancel_check:
            cancel_check()
        if progress_cb:
            progress_cb(i, total)

        # 检查是否有 mod 修改此文件
        has_mod = any(store.has_mod(mid, rel_path) for mid in mod_ids)
        if not has_mod:
            continue

        # 从缓存获取合并结果
        state = cache.get(rel_path, mod_configs, schema_dir, need_steps=False)
        result = MergeResult(merged_data=state.final_dict)
        results[rel_path] = result

        # 输出
        out_file = output_path / rel_path
        dump_json(result.merged_data, out_file)

    if progress_cb:
        progress_cb(total, total)
    return results

def _append_dict_content(src: Path, dest: Path) -> bool:
    """将解析失败的 dictionary 文件内容追加到已有合并结果末尾。

    提取 src 外层花括号之间的内容，插入到 dest 最后一个 '}' 之前。
    返回 True 表示追加成功，False 表示无法提取内容应回退覆盖。
    """
    failed_text = src.read_text(encoding='utf-8')
    first_brace = failed_text.find('{')
    last_brace = failed_text.rfind('}')
    if first_brace == -1 or last_brace == -1 or first_brace >= last_brace:
        return False
    inner = failed_text[first_brace + 1 : last_brace].strip()
    if not inner:
        return False

    existing_text = dest.read_text(encoding='utf-8')
    close_brace = existing_text.rfind('}')
    if close_brace == -1:
        return False

    before = existing_text[:close_brace].rstrip()
    if before and before[-1] not in ('{', ','):
        before += ','
    dest.write_text(before + '\n' + inner + '\n}', encoding='utf-8')
    return True


def copy_failed_files(
    mod_configs: list[tuple[str, str, Path]],
    output_path: Path,
) -> list[str]:
    """将解析失败但用户选择忽略的 JSON 文件原样复制到输出目录。

    同一 rel_path 有多个失败 mod 时，使用优先级最高（mod_configs 中靠后）的版本。
    dictionary 类型文件若已有合并结果，追加内容而非覆盖，避免破坏其它 mod 的结果。
    返回被复制的 rel_path 列表。
    """
    from ..json.classify import classify_json

    store = JsonStore.instance()
    ignored = store.get_ignored_failures()
    if not ignored:
        return []

    enabled_ids = {mid for mid, _, _ in mod_configs}
    relevant = [f for f in ignored if f.mod_id in enabled_ids]
    if not relevant:
        return []

    priority: dict[str, int] = {mid: i for i, (mid, _, _) in enumerate(mod_configs)}
    best: dict[str, ParseFailure] = {}
    for f in relevant:
        existing = best.get(f.rel_path)
        if existing is None or priority.get(f.mod_id, -1) > priority.get(existing.mod_id, -1):
            best[f.rel_path] = f

    copied: list[str] = []
    for rel_path, failure in sorted(best.items()):
        src = failure.file_path
        dest = output_path / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists():
            base_data = store.get_base(rel_path)
            file_type = classify_json(base_data) if base_data else "config"
            if file_type == "dictionary" and _append_dict_content(src, dest):
                diag.warn("merge", f"{rel_path}: JSON 解析失败，已从 {failure.mod_name} 追加内容到合并结果")
                copied.append(rel_path)
                continue

        shutil.copy2(src, dest)
        diag.warn("merge", f"{rel_path}: JSON 解析失败，已从 {failure.mod_name} 整文件复制")
        copied.append(rel_path)

    return copied
