"""
核心合并算法 - 基于 schema 规则的字典合并、实体合并、数组智能匹配

delta 产出使用强类型 DiffDict / ArrayFieldDiff / FieldDiff 树，
替代旧的 _DELETED 哨兵和 _delta/_new_entry/_deleted 魔法标记。
"""
import copy
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.diagnostics import diag, merge_ctx
from ..infra.profiler import profile
from ..infra.types import (
    ArrayFieldDiff,
    CancelCheck,
    ChangeKind,
    DeltaEntry,
    DiffDict,
    DupList,
    FieldDiff,
    JsonObject,
    ParseFailure,
    ProgressCallback,
)
from ..json.parser import dump_json
from ..json.store import JsonStore
from ..schema.dsl import classify_dsl_key
from ..schema.loader import (
    check_type_match,
    get_field_def,
    get_schema_root_key,
)

# 需要整文件替换而非合并的文件
WHOLE_FILE_REPLACE = {'sfx_config.json'}


@dataclass
class MergeResult:
    """合并结果"""
    merged_data: JsonObject = field(default_factory=dict)
    new_entries: list[tuple[str, str, str]] = field(default_factory=list)  # (file, mod_name, description)


# ==================== 合并层 ====================


def _build_warn_msg(field_path: list[str] | None, msg: str) -> str:
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


def _resolve_merge_strategy(
    child_def: JsonObject | None,
    base_val: object,
    override_val: object,
    key: str,
) -> tuple[str, str | None]:
    """确定字段的合并策略，返回 (strategy, type_warn_or_None)"""
    if child_def:
        strategy_val = child_def.get("__merge__", "replace")
        strategy = str(strategy_val) if strategy_val is not None else "replace"

        # 类型校验（对 FieldDiff 叶子提取实际值校验）
        schema_type = child_def.get("__type__")
        if schema_type and override_val is not None:
            actual_val = override_val
            if isinstance(override_val, FieldDiff):
                actual_val = override_val.value

            if actual_val is not None and not check_type_match(
                schema_type if isinstance(schema_type, (str, list)) else None,
                actual_val,
            ):
                from ..json.classify import get_type_str
                actual = get_type_str(actual_val)
                type_warn = f"字段 '{key}' 类型不匹配: schema 期望 {schema_type}，实际为 {actual}"
                return strategy, type_warn

        return strategy, None

    # 无 schema 时的默认策略
    if isinstance(base_val, dict) and isinstance(override_val, (dict, DiffDict)):
        return "merge", None
    return "replace", None


def _wrap_as_duplist(afd: ArrayFieldDiff) -> ArrayFieldDiff:
    """将 flat ArrayFieldDiff 包装为单元素 DupList 结构。

    delta 计算时将非 DupList 的 base 包装为 DupList([原list])（delta_store.py:146），
    产出 base_count=1 的 delta。全状态需要做相同的包装才能匹配。
    """
    return ArrayFieldDiff(
        diffs=[FieldDiff(ChangeKind.ORIGIN, afd)],
        indices=[1],
        base_count=1,
        order=[0, 1, -1],
        is_duplist=True,
    )


def _is_modified(entry: DeltaEntry | None) -> bool:
    """判断条目是否已被之前的 mod 修改过。

    - FieldDiff: base_kind != ORIGIN 即已修改
    - ArrayFieldDiff: 任何元素非 ORIGIN 即认为已修改（保守策略）
    - DiffDict: 不应在此层判断，由调用方递归到子字段
    """
    if entry is None:
        return False
    if isinstance(entry, FieldDiff):
        return entry.kind.base_kind != ChangeKind.ORIGIN
    if isinstance(entry, ArrayFieldDiff):
        return any(d.kind.base_kind != ChangeKind.ORIGIN for d in entry.diffs)
    raise TypeError("不应对 DiffDict 调用 _is_modified，应递归到子字段")


def _extract_value(entry: DeltaEntry | None) -> object:
    """提取现有条目的值，用于保存为 old_value"""
    if entry is None:
        return None
    if isinstance(entry, FieldDiff):
        return entry.value
    return entry  # DiffDict / ArrayFieldDiff 本身


def _remap_array_delta(
    delta: ArrayFieldDiff,
    base: ArrayFieldDiff,
) -> tuple[ArrayFieldDiff, dict[int, int]]:
    """对 ArrayFieldDiff 中的 ADDED 元素 ID 进行重分配。

    基于全状态 base 的最大 ID 进行重映射，避免 ID 冲突。
    返回重映射后的 ArrayFieldDiff 和映射表 {原ID: 新ID}。
    """
    cur_max = max(base.indices, default=0)
    remap: dict[int, int] = {}
    next_id = cur_max + 1

    for diff, orig_id in zip(delta.diffs, delta.indices, strict=True):
        if diff.kind == ChangeKind.ADDED:
            remap[orig_id] = next_id
            next_id += 1
        elif orig_id > delta.base_count:
            raise ValueError(
                f"CHANGED/DELETED 的 ID {orig_id} 超过 base_count {delta.base_count}"
            )

    if not remap:
        return delta, remap

    new_indices = [remap.get(i, i) for i in delta.indices]
    new_order = [remap.get(i, i) for i in delta.order]
    return ArrayFieldDiff(
        diffs=delta.diffs,
        base_count=delta.base_count,
        indices=new_indices,
        order=new_order,
        is_duplist=delta.is_duplist,
    ), remap



def apply_array_delta(
    base: ArrayFieldDiff,
    delta: ArrayFieldDiff,
    schema: JsonObject | None = None,
    field_path: list[str] | None = None,
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
    if is_override:
        # override delta 的 indices 是基于展开后 flat 数组的 1-based 位置，
        # 而全状态的 indices 是 element ID 体系。需要通过 base.order 做映射。
        # base.order 形如 [0, eid1, eid2, ..., -1]，去掉 0 和 -1 后就是展开顺序。
        flat_order = [eid for eid in base.order if eid not in (0, -1)]
        # flat_pos (1-based) → element_id 的映射
        flat_to_eid: dict[int, int] = {
            pos + 1: eid for pos, eid in enumerate(flat_order)
        }
        # 将 delta 的 indices 从 flat position 映射为 element ID
        remapped_indices: list[int] = []
        for idx in delta.indices:
            eid = flat_to_eid.get(idx)
            if eid is not None:
                remapped_indices.append(eid)
            else:
                # flat position 超出当前 order 范围，保留原值（ADDED 等场景）
                remapped_indices.append(idx)
        delta = ArrayFieldDiff(
            diffs=delta.diffs,
            base_count=delta.base_count,
            indices=remapped_indices,
            order=delta.order,
            is_duplist=delta.is_duplist,
        )
    else:
        # 非 override：ID 重分配（处理 ADDED 元素的 ID 冲突）
        delta, _ = _remap_array_delta(delta, base)

    # 构建 ID → 位置映射
    id_map: dict[int, int] = {eid: pos for pos, eid in enumerate(base.indices)}

    # 应用 CHANGED
    for diff, elem_id in zip(delta.diffs, delta.indices, strict=True):
        if diff.kind != ChangeKind.CHANGED:
            continue
        if elem_id not in id_map:
            continue
        pos = id_map[elem_id]
        existing = base.diffs[pos]
        was_modified = existing.kind.base_kind != ChangeKind.ORIGIN

        if is_override:
            modifier = ChangeKind.OVERRIDE
        elif was_modified:
            modifier = ChangeKind.MULTI_MOD
        else:
            modifier = ChangeKind.ORIGIN

        if isinstance(diff.value, DiffDict) and isinstance(existing.value, DiffDict):
            # 嵌套 dict 变更：递归 apply_delta，子字段级别追踪
            apply_delta(existing.value, diff.value, schema, field_path,
                        version=version, is_override=is_override)
            # 更新元素级标记
            existing.kind = ChangeKind.CHANGED | modifier
            existing.version = version
        elif isinstance(diff.value, ArrayFieldDiff) and isinstance(existing.value, ArrayFieldDiff):
            # 嵌套数组变更：递归
            apply_array_delta(existing.value, diff.value, schema, field_path,
                              version=version, is_override=is_override)
            existing.kind = ChangeKind.CHANGED | modifier
            existing.version = version
        elif isinstance(diff.value, FieldDiff):
            # 标量变更（_recursive_delta 返回的 FieldDiff 叶子）
            base.diffs[pos] = FieldDiff(ChangeKind.CHANGED | modifier, diff.value.value,
                                        old_value=existing.value, version=version)
        else:
            # 直接值替换
            base.diffs[pos] = FieldDiff(ChangeKind.CHANGED | modifier, diff.value,
                                        old_value=existing.value, version=version)

    # 应用 ADDED 和 DELETED
    deleted_ids: set[int] = set()
    added_remap: dict[int, int] = {}  # override 用：delta added_id → 重分配 ID

    # override 的 ADDED ID 可能与全状态已有 ID 冲突，需要重分配
    if is_override:
        max_existing = max(base.indices) if base.indices else 0
        next_new_id = max_existing + 1
    else:
        next_new_id = 0  # 不使用

    for diff, elem_id in zip(delta.diffs, delta.indices, strict=True):
        if diff.kind == ChangeKind.ADDED:
            if is_override:
                new_id = next_new_id
                next_new_id += 1
                added_remap[elem_id] = new_id
                base.diffs.append(FieldDiff(ChangeKind.ADDED | ChangeKind.OVERRIDE, diff.value, version=version))
                base.indices.append(new_id)
            else:
                base.diffs.append(FieldDiff(ChangeKind.ADDED, diff.value, version=version))
                base.indices.append(elem_id)
        elif diff.kind == ChangeKind.DELETED:
            deleted_ids.add(elem_id)
            if elem_id in id_map:
                pos = id_map[elem_id]
                existing = base.diffs[pos]
                was_modified = existing.kind.base_kind != ChangeKind.ORIGIN
                if is_override:
                    del_modifier = ChangeKind.OVERRIDE
                elif was_modified:
                    del_modifier = ChangeKind.MULTI_MOD
                else:
                    del_modifier = ChangeKind.ORIGIN
                base.diffs[pos] = FieldDiff(ChangeKind.DELETED | del_modifier, None,
                                            old_value=existing.value, version=version)

    # override 不重建 order：override delta 的 base_count 基于展开后数组，
    # 与全状态的 base_count 不一致，用 delta.order 重建会破坏元素排列。
    if is_override:
        base.old_order = list(base.order)
        if deleted_ids:
            base.order = [eid for eid in base.order if eid not in deleted_ids]
        if added_remap:
            # 通过 delta.order 确定新增元素应插入的位置
            # flat_to_eid: delta 中的 flat position → 全状态 element ID
            flat_order_cur = [eid for eid in base.order if eid not in (0, -1)]
            flat_to_eid_cur: dict[int, int] = {
                pos + 1: eid for pos, eid in enumerate(flat_order_cur)
            }
            delta_body = [x for x in delta.order if x not in (0, -1)]
            for i, did in enumerate(delta_body):
                if did not in added_remap:
                    continue
                new_id = added_remap[did]
                # 找前一个非 added 的已有元素作为锚点
                anchor_eid = 0  # 默认：插在开头（0 标记之后）
                for j in range(i - 1, -1, -1):
                    prev_did = delta_body[j]
                    if prev_did not in added_remap:
                        anchor_eid = flat_to_eid_cur.get(prev_did, 0)
                        break
                # 在 base.order 中 anchor_eid 之后插入
                try:
                    anchor_pos = base.order.index(anchor_eid)
                    base.order.insert(anchor_pos + 1, new_id)
                except ValueError:
                    # fallback: 插在 -1 之前
                    end_pos = len(base.order) - 1
                    base.order.insert(end_pos, new_id)
        return base

    # 保存旧 order，重建 order
    base.old_order = list(base.order)

    # 找出前 mod 新增的非 base 元素（不含本次 delta 的元素）
    this_delta_ids = set(delta.indices)
    current_non_base: set[int] = set()
    for eid in base.indices:
        if eid > delta.base_count and eid not in this_delta_ids:
            current_non_base.add(eid)

    # 从旧 order 中提取 base 元素和前 mod 非 base 元素的相对位置
    after_base: dict[int, list[int]] = {0: []}
    last_base_id = 0
    for eid in base.old_order:
        if eid == 0 or eid == -1:
            continue
        if eid <= delta.base_count:
            last_base_id = eid
            if eid not in after_base:
                after_base[eid] = []
        elif eid in current_non_base:
            after_base.setdefault(last_base_id, []).append(eid)

    # 有效 ID 集合（已删除的元素从 valid 中移除）
    valid_ids = set(base.indices)
    valid_ids -= deleted_ids

    # 按 delta.order 构建新 order
    new_order: list[int] = [0]
    for eid in delta.order:
        if eid == 0:
            for nb_id in after_base.get(0, []):
                if nb_id in valid_ids:
                    new_order.append(nb_id)
            continue
        if eid == -1:
            continue
        if eid in deleted_ids:
            continue
        if eid in valid_ids:
            new_order.append(eid)
        # 在此 base 元素后插入前 mod 新增的非 base 元素
        if eid <= delta.base_count:
            for nb_id in after_base.get(eid, []):
                if nb_id in valid_ids:
                    new_order.append(nb_id)
    new_order.append(-1)
    base.order = new_order

    return base


@profile
def apply_delta(
    base: DiffDict,
    delta: DiffDict,
    schema: JsonObject | None = None,
    field_path: list[str] | None = None,
    version: int = 0,
    is_override: bool = False,
) -> DiffDict:
    """将 DiffDict delta 应用到全状态 DiffDict 上，原地修改并返回。

    参数:
        base: 全状态 DiffDict（所有字段带 ChangeKind 注解）
        delta: 稀疏 DiffDict（仅变化的字段）
        version: 当前 mod 迭代版本号
        is_override: True 时为用户手动覆写，标记 OVERRIDE 且不触发 MULTI_MOD
    """
    # 查找当前层的 schema 定义
    current_def: JsonObject | None = None
    if schema and field_path:
        current_def = get_field_def(schema, field_path)

    for key, diff in delta.items.items():
        child_path = field_path + [key] if field_path is not None else None

        if isinstance(diff, FieldDiff):
            existing = base.items.get(key)
            was_modified = (
                _is_modified(existing)
                if isinstance(existing, (FieldDiff, ArrayFieldDiff))
                else False
            )
            # override 不触发 MULTI_MOD，而是标记 OVERRIDE
            if is_override:
                modifier = ChangeKind.OVERRIDE
            elif was_modified:
                modifier = ChangeKind.MULTI_MOD
            else:
                modifier = ChangeKind.ORIGIN
            old_val = _extract_value(existing)

            if diff.kind == ChangeKind.DELETED:
                # delta 中出现 DELETED 即意味着应该删除（模式过滤已在 delta 计算阶段完成）
                kind = ChangeKind.DELETED | modifier
                base.items[key] = FieldDiff(kind, None,
                                            old_value=old_val, version=version)
            elif diff.kind == ChangeKind.ADDED:
                # 类型校验
                child_def = get_field_def(schema, child_path) if schema and child_path else None
                _, type_warn = _resolve_merge_strategy(child_def, None, diff, key)
                if type_warn:
                    diag.warn("merge", _build_warn_msg(child_path, type_warn))
                kind = ChangeKind.ADDED | modifier
                base.items[key] = FieldDiff(kind, diff.value,
                                            old_value=old_val, version=version)
            else:
                # CHANGED
                child_def = get_field_def(schema, child_path) if schema and child_path else None
                _, type_warn = _resolve_merge_strategy(
                    child_def, old_val, diff, key,
                )
                if type_warn:
                    diag.warn("merge", _build_warn_msg(child_path, type_warn))
                kind = ChangeKind.CHANGED | modifier
                base.items[key] = FieldDiff(kind, diff.value,
                                            old_value=old_val, version=version)

        elif isinstance(diff, DiffDict):
            # 嵌套 dict 的部分修改——递归 apply_delta
            existing = base.items.get(key)
            if isinstance(existing, DiffDict):
                apply_delta(existing, diff, schema, child_path,
                            version=version, is_override=is_override)
            elif isinstance(existing, FieldDiff) and isinstance(existing.value, DiffDict):
                apply_delta(existing.value, diff, schema, child_path,
                            version=version, is_override=is_override)
            elif isinstance(existing, FieldDiff) and isinstance(existing.value, dict):
                sub = DiffDict.from_dict(existing.value)
                apply_delta(sub, diff, schema, child_path,
                            version=version, is_override=is_override)
                base.items[key] = sub
            else:
                sub = DiffDict()
                apply_delta(sub, diff, schema, child_path,
                            version=version, is_override=is_override)

        elif isinstance(diff, ArrayFieldDiff):
            existing = base.items.get(key)
            if isinstance(existing, ArrayFieldDiff):
                base_afd = existing
            elif isinstance(existing, FieldDiff) and isinstance(existing.value, ArrayFieldDiff):
                base_afd = existing.value
            elif isinstance(existing, FieldDiff) and isinstance(existing.value, (list, DupList)):
                base_afd = ArrayFieldDiff.from_list(existing.value)
            elif isinstance(existing, FieldDiff) and existing.value is not None and not isinstance(existing.value, (dict, DiffDict)):
                # 标量归一化为单元素数组（与 delta 计算阶段的归一化对应）
                base_afd = ArrayFieldDiff.from_list([existing.value])
            else:
                base_afd = ArrayFieldDiff(
                    diffs=[], base_count=0, indices=[], order=[0, -1],
                    is_duplist=diff.is_duplist,
                )
            if diff.is_duplist and not base_afd.is_duplist:
                base_afd = _wrap_as_duplist(base_afd)
            apply_array_delta(base_afd, diff, schema, child_path,
                              version=version, is_override=is_override)
            base.items[key] = base_afd

    # 未知 key 警告
    # dictionary 类型文件的顶层 key 是条目 ID（任意字符串），不应做 known_keys 检查
    is_dict_toplevel = (
        schema is not None
        and field_path == ["_entry"]
        and isinstance(schema.get("_meta"), dict)
        and schema["_meta"].get("file_type") == "dictionary"  # type: ignore[attr-defined]
    )
    if current_def and isinstance(current_def, dict) and not is_dict_toplevel:
        known_keys: set[str] = set()
        fields = current_def.get("__fields__")
        if isinstance(fields, dict):
            known_keys = set(fields.keys())
        else:
            meta_keys = {"__type__", "__merge__", "__fields__", "__element__", "__match_key__",
                         "__template__", "__use_template__", "__templates__"}
            field_candidates = {k for k in current_def if k not in meta_keys}
            if field_candidates and all(
                isinstance(current_def[k], dict) and (
                    "__type__" in current_def[k]  # type: ignore[operator]
                    or "__use_template__" in current_def[k]  # type: ignore[operator]
                )
                for k in field_candidates
            ):
                known_keys = field_candidates

        if known_keys:
            for key in delta.items:
                if key not in known_keys:
                    if classify_dsl_key(key):
                        continue
                    path_with_key = field_path + [key] if field_path is not None else None
                    msg = f"未知字段 '{key}'，schema 中未定义"
                    diag.warn("merge", _build_warn_msg(path_with_key, msg))

    return base


# ==================== 文件级合并 ====================


@profile
def merge_file(
    base_data: JsonObject,
    mod_data_list: list[tuple[str, str, DiffDict, str]],
    rel_path: str = "",
    schema: JsonObject | None = None,
) -> MergeResult:
    """合并单个文件。

    参数:
        base_data: 游戏本体的 JSON 数据
        mod_data_list: [(mod_id, mod_name, delta, source_file), ...] 按优先级排序
        rel_path: 文件相对路径（用于判断特殊文件）
        schema: 该文件对应的 schema 规则
    """
    result = MergeResult()
    file_name = Path(rel_path).name if rel_path else ""

    # sfx_config.json 等特殊文件：整文件替换
    if file_name in WHOLE_FILE_REPLACE:
        if mod_data_list:
            _, last_mod_name, _, _ = mod_data_list[-1]
            current = DiffDict.from_dict(base_data)
            for step, (_, _mod_name, delta, _) in enumerate(mod_data_list, 1):
                apply_delta(current, delta, schema, None, version=step)
            result.merged_data = current.to_dict()
            if len(mod_data_list) > 1:
                diag.warn("merge", f"{rel_path}: 多个 mod 修改此文件（整文件替换模式），最终使用 {last_mod_name}")
        else:
            result.merged_data = copy.deepcopy(base_data)
        return result

    current = DiffDict.from_dict(base_data)

    # 确定 schema 根 key
    root_key = get_schema_root_key(schema) if schema else None

    for step, (mod_id, mod_name, delta, source_file) in enumerate(mod_data_list, 1):
        # 设置线程本地上下文，供 apply_delta 内部的警告使用
        merge_ctx.mod_name = mod_name
        merge_ctx.mod_id = mod_id
        merge_ctx.rel_path = rel_path
        merge_ctx.source_file = source_file

        fp: list[str] | None = [root_key] if root_key else None
        apply_delta(current, delta, schema, fp, version=step)

        # 检查用户 override delta
        override_delta = JsonStore.instance().get_override(mod_id, rel_path)
        if override_delta is not None:
            apply_delta(current, override_delta, schema, fp, version=step)

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

        base_data = store.get_base(rel_path)

        # tag.json name 匹配验证
        if rel_path == "tag.json" and base_data:
            for mod_id in mod_ids:
                if not store.has_mod(mod_id, rel_path):
                    continue
                mod_name = store.mod_name(mod_id)
                mod_data = store.get_mod(mod_id, rel_path)
                _validate_tag_names(base_data, [(mod_id, mod_name, mod_data)])

        # 检查是否有 mod 修改此文件
        has_mod = any(store.has_mod(mid, rel_path) for mid in mod_ids)
        if not has_mod:
            continue

        # WHOLE_FILE_REPLACE 警告
        file_name = Path(rel_path).name if rel_path else ""
        if file_name in WHOLE_FILE_REPLACE:
            mod_names = [store.mod_name(mid) for mid in mod_ids
                         if store.has_mod(mid, rel_path)]
            if len(mod_names) > 1:
                diag.warn("merge", f"{rel_path}: 多个 mod 修改此文件（整文件替换模式），最终使用 {mod_names[-1]}")

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


def _validate_tag_names(
    base_data: JsonObject,
    mod_data_list: list[tuple[str, str, JsonObject]],
) -> None:
    """验证 tag.json 中覆盖的 tag 的 name 是否与原 tag 一致"""
    for _, mod_name, mod_data in mod_data_list:
        for key, value in mod_data.items():
            base_val = base_data.get(key)
            if base_val is not None and isinstance(value, dict) and isinstance(base_val, dict):
                base_name = base_val.get('name', '')
                mod_tag_name = value.get('name', '')
                if mod_tag_name and base_name and mod_tag_name != base_name:
                    msg = (f"tag.json: Mod [{mod_name}] 的 tag [{key}] "
                           f"name=\"{mod_tag_name}\" 与本体 name=\"{base_name}\" 不一致，可能导致游戏出错")
                    diag.warn("merge", msg)


def copy_failed_files(
    mod_configs: list[tuple[str, str, Path]],
    output_path: Path,
) -> list[str]:
    """将解析失败但用户选择忽略的 JSON 文件原样复制到输出目录。

    同一 rel_path 有多个失败 mod 时，使用优先级最高（mod_configs 中靠后）的版本。
    返回被复制的 rel_path 列表。
    """
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
        shutil.copy2(src, dest)
        diag.warn("merge", f"{rel_path}: JSON 解析失败，已从 {failure.mod_name} 整文件复制")
        copied.append(rel_path)

    return copied
