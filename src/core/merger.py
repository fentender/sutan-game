"""
核心合并算法 - 基于 schema 规则的字典合并、实体合并、数组智能匹配

delta 产出使用强类型 DiffDict / ArrayFieldDiff / FieldDiff 树，
替代旧的 _DELETED 哨兵和 _delta/_new_entry/_deleted 魔法标记。
"""
import copy
from dataclasses import dataclass, field
from pathlib import Path

from .delta_store import (
    ModDelta,
)
from .diagnostics import diag, merge_ctx
from .dsl_patterns import classify_dsl_key
from .json_parser import DupList, dump_json
from .json_store import JsonStore
from .profiler import profile
from .schema_loader import (
    check_type_match,
    get_field_def,
    get_schema_root_key,
    load_schemas,
    resolve_schema,
)
from .types import (
    ArrayFieldDiff,
    CancelCheck,
    ChangeKind,
    DiffDict,
    FieldDiff,
)

# 需要整文件替换而非合并的文件
WHOLE_FILE_REPLACE = {'sfx_config.json'}


@dataclass
class MergeResult:
    """合并结果"""
    merged_data: dict[str, object] = field(default_factory=dict)
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
    child_def: dict[str, object] | None,
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
                from .type_utils import get_type_str
                actual = get_type_str(actual_val)
                type_warn = f"字段 '{key}' 类型不匹配: schema 期望 {schema_type}，实际为 {actual}"
                return strategy, type_warn

        return strategy, None

    # 无 schema 时的默认策略
    if isinstance(base_val, dict) and isinstance(override_val, (dict, DiffDict)):
        return "merge", None
    return "replace", None


def _is_modified(entry: FieldDiff | DiffDict | ArrayFieldDiff | None) -> bool:
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
    raise TypeError(f"不应对 DiffDict 调用 _is_modified，应递归到子字段")


def _extract_value(entry: FieldDiff | DiffDict | ArrayFieldDiff | None) -> object:
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
    schema: dict[str, object] | None = None,
    field_path: list[str] | None = None,
    allow_deletions: bool = False,
    version: int = 0,
) -> ArrayFieldDiff:
    """将 ArrayFieldDiff delta 应用到全状态 ArrayFieldDiff 上，原地修改并返回。

    参数:
        base: 全状态 ArrayFieldDiff（所有元素带 ChangeKind 注解）
        delta: 稀疏 ArrayFieldDiff（仅变化的元素）
        version: 当前 mod 迭代版本号
    """
    # ID 重分配
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

        if isinstance(diff.value, DiffDict) and isinstance(existing.value, DiffDict):
            # 嵌套 dict 变更：递归 apply_delta，子字段级别追踪 MULTI_MOD
            apply_delta(existing.value, diff.value, schema, field_path,
                        allow_deletions=allow_deletions, version=version)
            # 更新元素级标记
            kind = ChangeKind.CHANGED | (ChangeKind.MULTI_MOD if was_modified else ChangeKind.ORIGIN)
            existing.kind = kind
            existing.version = version
        elif isinstance(diff.value, ArrayFieldDiff) and isinstance(existing.value, ArrayFieldDiff):
            # 嵌套数组变更：递归
            apply_array_delta(existing.value, diff.value, schema, field_path,
                              allow_deletions=allow_deletions, version=version)
            kind = ChangeKind.CHANGED | (ChangeKind.MULTI_MOD if was_modified else ChangeKind.ORIGIN)
            existing.kind = kind
            existing.version = version
        elif isinstance(diff.value, FieldDiff):
            # 标量变更（_recursive_delta 返回的 FieldDiff 叶子）
            kind = ChangeKind.CHANGED | (ChangeKind.MULTI_MOD if was_modified else ChangeKind.ORIGIN)
            base.diffs[pos] = FieldDiff(kind, diff.value.value,
                                        old_value=existing.value, version=version)
        else:
            # 直接值替换
            kind = ChangeKind.CHANGED | (ChangeKind.MULTI_MOD if was_modified else ChangeKind.ORIGIN)
            base.diffs[pos] = FieldDiff(kind, diff.value,
                                        old_value=existing.value, version=version)

    # 应用 ADDED 和 DELETED
    deleted_ids: set[int] = set()
    for diff, elem_id in zip(delta.diffs, delta.indices, strict=True):
        if diff.kind == ChangeKind.ADDED:
            base.diffs.append(FieldDiff(ChangeKind.ADDED, diff.value, version=version))
            base.indices.append(elem_id)
        elif diff.kind == ChangeKind.DELETED:
            deleted_ids.add(elem_id)
            if elem_id in id_map:
                pos = id_map[elem_id]
                existing = base.diffs[pos]
                was_modified = existing.kind.base_kind != ChangeKind.ORIGIN
                if allow_deletions:
                    kind = ChangeKind.DELETED | (ChangeKind.MULTI_MOD if was_modified else ChangeKind.ORIGIN)
                    base.diffs[pos] = FieldDiff(kind, None,
                                                old_value=existing.value, version=version)

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

    # 有效 ID 集合
    valid_ids = set(base.indices)
    if allow_deletions:
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
            if not allow_deletions and eid in valid_ids:
                new_order.append(eid)
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
    schema: dict[str, object] | None = None,
    field_path: list[str] | None = None,
    allow_deletions: bool = False,
    version: int = 0,
) -> DiffDict:
    """将 DiffDict delta 应用到全状态 DiffDict 上，原地修改并返回。

    参数:
        base: 全状态 DiffDict（所有字段带 ChangeKind 注解）
        delta: 稀疏 DiffDict（仅变化的字段）
        version: 当前 mod 迭代版本号
    """
    # 查找当前层的 schema 定义
    current_def: dict[str, object] | None = None
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
            old_val = _extract_value(existing)

            if diff.kind == ChangeKind.DELETED:
                # 合法 existing: None、DiffDict、ArrayFieldDiff、FieldDiff(ORIGIN/CHANGED/DELETED)
                if isinstance(existing, FieldDiff):
                    assert existing.kind.base_kind in (ChangeKind.ORIGIN, ChangeKind.CHANGED, ChangeKind.DELETED), \
                        f"DELETED delta 遇到非预期 existing kind: {existing.kind}"
                if allow_deletions:
                    kind = ChangeKind.DELETED | (
                        ChangeKind.MULTI_MOD if was_modified else ChangeKind.ORIGIN
                    )
                    base.items[key] = FieldDiff(kind, None,
                                                old_value=old_val, version=version)
            elif diff.kind == ChangeKind.ADDED:
                # 合法 existing: None 或 FieldDiff(ADDED)
                assert existing is None or (isinstance(existing, FieldDiff) and existing.kind.base_kind == ChangeKind.ADDED), \
                    f"ADDED delta 遇到非预期 existing: {type(existing).__name__}" + \
                    (f" kind={existing.kind}" if isinstance(existing, FieldDiff) else "")
                # 类型校验
                child_def = get_field_def(schema, child_path) if schema and child_path else None
                _, type_warn = _resolve_merge_strategy(child_def, None, diff, key)
                if type_warn:
                    diag.warn("merge", _build_warn_msg(child_path, type_warn))
                kind = ChangeKind.ADDED | (
                    ChangeKind.MULTI_MOD if was_modified else ChangeKind.ORIGIN
                )
                base.items[key] = FieldDiff(kind, diff.value,
                                            old_value=old_val, version=version)
            else:
                # CHANGED
                # 合法 existing: None、DiffDict、ArrayFieldDiff、FieldDiff(ORIGIN/CHANGED/DELETED)
                if isinstance(existing, FieldDiff):
                    assert existing.kind.base_kind in (ChangeKind.ORIGIN, ChangeKind.CHANGED, ChangeKind.DELETED), \
                        f"CHANGED delta 遇到非预期 existing kind: {existing.kind}"
                child_def = get_field_def(schema, child_path) if schema and child_path else None
                _, type_warn = _resolve_merge_strategy(
                    child_def, old_val, diff, key,
                )
                if type_warn:
                    diag.warn("merge", _build_warn_msg(child_path, type_warn))
                kind = ChangeKind.CHANGED | (
                    ChangeKind.MULTI_MOD if was_modified else ChangeKind.ORIGIN
                )
                base.items[key] = FieldDiff(kind, diff.value,
                                            old_value=old_val, version=version)

        elif isinstance(diff, DiffDict):
            # 嵌套 dict 的部分修改——递归 apply_delta
            existing = base.items.get(key)
            if isinstance(existing, DiffDict):
                apply_delta(existing, diff, schema, child_path,
                            allow_deletions=allow_deletions, version=version)
            elif isinstance(existing, FieldDiff) and isinstance(existing.value, DiffDict):
                apply_delta(existing.value, diff, schema, child_path,
                            allow_deletions=allow_deletions, version=version)
            elif isinstance(existing, FieldDiff) and isinstance(existing.value, dict):
                sub = DiffDict.from_dict(existing.value)
                apply_delta(sub, diff, schema, child_path,
                            allow_deletions=allow_deletions, version=version)
                base.items[key] = sub
            else:
                sub = DiffDict()
                apply_delta(sub, diff, schema, child_path,
                            allow_deletions=allow_deletions, version=version)
                base.items[key] = sub

        elif isinstance(diff, ArrayFieldDiff):
            existing = base.items.get(key)
            if isinstance(existing, ArrayFieldDiff):
                base_afd = existing
            elif isinstance(existing, FieldDiff) and isinstance(existing.value, ArrayFieldDiff):
                base_afd = existing.value
            elif isinstance(existing, FieldDiff) and isinstance(existing.value, (list, DupList)):
                base_afd = ArrayFieldDiff.from_list(existing.value)
            else:
                base_afd = ArrayFieldDiff(
                    diffs=[], base_count=0, indices=[], order=[0, -1],
                    is_duplist=diff.is_duplist,
                )
            apply_array_delta(base_afd, diff, schema, child_path,
                              allow_deletions=allow_deletions, version=version)
            base.items[key] = base_afd

    # 未知 key 警告
    if current_def and isinstance(current_def, dict):
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
    base_data: dict[str, object],
    mod_data_list: list[tuple[str, str, DiffDict, str]],
    rel_path: str = "",
    schema: dict[str, object] | None = None,
    allow_deletions: bool = False,
) -> MergeResult:
    """合并单个文件。

    参数:
        base_data: 游戏本体的 JSON 数据
        mod_data_list: [(mod_id, mod_name, delta, source_file), ...] 按优先级排序
        rel_path: 文件相对路径（用于判断特殊文件）
        schema: 该文件对应的 schema 规则
        allow_deletions: 是否应用删除标记
    """
    result = MergeResult()
    file_name = Path(rel_path).name if rel_path else ""

    # sfx_config.json 等特殊文件：整文件替换
    if file_name in WHOLE_FILE_REPLACE:
        if mod_data_list:
            _, last_mod_name, _, _ = mod_data_list[-1]
            current = DiffDict.from_dict(base_data)
            for step, (_, _mod_name, delta, _) in enumerate(mod_data_list, 1):
                apply_delta(current, delta, schema, None,
                            allow_deletions=allow_deletions, version=step)
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
        apply_delta(current, delta, schema, fp,
                    allow_deletions=allow_deletions, version=step)

        # 检查用户 override
        override = JsonStore.instance().get_override(mod_id, rel_path)
        if override is not None:
            current = DiffDict.from_dict(override)

    result.merged_data = current.to_dict()
    return result


@profile
def merge_all_files(
    mod_configs: list[tuple[str, str, Path]],
    output_path: Path,
    schema_dir: Path | None = None,
    allow_deletions: bool = False,
    cancel_check: CancelCheck | None = None,
) -> dict[str, MergeResult]:
    """合并所有文件。

    参数:
        mod_configs: [(mod_id, mod_name, mod_config_path), ...] 按优先级排序
        output_path: 输出目录
        schema_dir: schema 规则文件目录
        allow_deletions: 是否允许删减
        cancel_check: 可选的取消检查回调
    """
    diag.snapshot("merge")

    store = JsonStore.instance()
    schemas = load_schemas(schema_dir) if schema_dir else {}
    mod_ids = [mod_id for mod_id, _, _ in mod_configs]

    results: dict[str, MergeResult] = {}

    for rel_path in sorted(store.all_rel_paths()):
        if cancel_check:
            cancel_check()

        # 从 store 获取本体数据（不存在时为 {}）
        base_data = store.get_base(rel_path)

        # 查找 schema
        schema = resolve_schema(rel_path, schemas) if schemas else None

        # 从 store 获取各 mod 的数据，从缓存获取 delta
        mod_data_list: list[tuple[str, str, DiffDict, str]] = []
        for mod_id in mod_ids:
            if not store.has_mod(mod_id, rel_path):
                continue
            mod_name = store.mod_name(mod_id)

            # tag.json name 匹配验证
            if rel_path == "tag.json" and base_data:
                mod_data = store.get_mod(mod_id, rel_path)
                _validate_tag_names(base_data, [(mod_id, mod_name, mod_data)])

            delta = ModDelta.get(mod_id, rel_path)
            if delta is not None:
                mod_data_list.append((mod_id, mod_name, delta, ""))

        if not mod_data_list:
            continue

        # 合并
        merge_result = merge_file(base_data, mod_data_list, rel_path,
                                   schema=schema,
                                   allow_deletions=allow_deletions)
        results[rel_path] = merge_result

        # 输出
        out_file = output_path / rel_path
        dump_json(merge_result.merged_data, out_file)

    return results


def _validate_tag_names(
    base_data: dict[str, object],
    mod_data_list: list[tuple[str, str, dict[str, object]]],
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
