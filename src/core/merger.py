"""
核心合并算法 - 基于 schema 规则的字典合并、实体合并、数组智能匹配

delta 产出使用强类型 DictDelta / ArrayFieldDiff / FieldDiff 树，
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
    DictDelta,
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
    if isinstance(base_val, dict) and isinstance(override_val, (dict, DictDelta)):
        return "merge", None
    return "replace", None


def _remap_array_delta(
    delta: ArrayFieldDiff,
    id_arr: list[tuple[int, object]],
) -> tuple[ArrayFieldDiff, dict[int, int]]:
    """对 ArrayFieldDiff 中的 ADDED 元素 ID 进行重分配。

    返回重映射后的 ArrayFieldDiff 和映射表 {原ID: 新ID}。
    CHANGED/DELETED 的 ID 必须 <= base_count，否则 raise。
    """
    cur_max = max((eid for eid, _ in id_arr), default=0)
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
    ), remap


def apply_array_delta(
    id_arr: list[tuple[int, object]],
    delta: ArrayFieldDiff,
    schema: dict[str, object] | None = None,
    field_path: list[str] | None = None,
    allow_deletions: bool = False,
) -> list[tuple[int, object]]:
    """将 ArrayFieldDiff 应用到带 ID 的数组上。

    内部自动调用 _remap_array_delta 完成 ADDED 元素的 ID 重分配，
    调用方无需预先 remap。

    参数:
        id_arr: [(element_id, value), ...] 当前数组状态
        delta: 要应用的 ArrayFieldDiff
        schema: schema 规则
        field_path: 当前字段路径
        allow_deletions: 是否实际执行删除
    返回:
        更新后的 [(element_id, value), ...]
    """
    # ID 重分配
    delta, _ = _remap_array_delta(delta, id_arr)

    # 构建 ID → 索引映射
    id_map: dict[int, int] = {eid: i for i, (eid, _) in enumerate(id_arr)}

    # 应用 CHANGED
    for diff, elem_id in zip(delta.diffs, delta.indices, strict=True):
        if diff.kind != ChangeKind.CHANGED:
            continue
        if elem_id not in id_map:
            continue  # 元素已不存在，跳过
        idx = id_map[elem_id]
        old_eid, old_val = id_arr[idx]
        if isinstance(diff.value, DictDelta) and isinstance(old_val, dict):
            apply_delta(old_val, diff.value, schema, field_path,
                        allow_deletions=allow_deletions)
            # old_val 已原地修改
        elif isinstance(diff.value, ArrayFieldDiff) and isinstance(old_val, list):
            # 嵌套数组变更：递归应用 ArrayFieldDiff
            if old_val and isinstance(old_val[0], tuple):
                nested_id_arr: list[tuple[int, object]] = old_val
            else:
                nested_id_arr = [(i + 1, elem) for i, elem in enumerate(old_val)]
            new_nested = apply_array_delta(
                nested_id_arr, diff.value, schema, field_path,
                allow_deletions=allow_deletions,
            )
            id_arr[idx] = (old_eid, new_nested)
        elif isinstance(diff.value, FieldDiff):
            id_arr[idx] = (old_eid, copy.deepcopy(diff.value.value))
        else:
            id_arr[idx] = (old_eid, copy.deepcopy(diff.value))

    # 收集 ADDED 和 DELETED
    new_items: dict[int, object] = {}
    deleted_ids: set[int] = set()
    for diff, elem_id in zip(delta.diffs, delta.indices, strict=True):
        if diff.kind == ChangeKind.ADDED:
            new_items[elem_id] = copy.deepcopy(diff.value)
        elif diff.kind == ChangeKind.DELETED:
            deleted_ids.add(elem_id)

    # 按 order 重建数组
    # 策略：delta.order 中的 base 元素 ID（<= base_count）和边界标记（0, -1）
    # 决定 base 元素的相对顺序。新增 ID 在 order 中指定的位置插入。
    # current 中已有的非 base 元素（前 mod 新增的）保持原有位置。

    # 先收集 order 中 base 元素和新增元素的序列（去掉边界标记和已删除的）
    # 同时保留 current 中前 mod 新增的元素
    current_ids = [eid for eid, _ in id_arr]
    current_non_base = {eid for eid in current_ids if eid > delta.base_count}

    # 按 delta.order 中的相对位置构建最终数组
    # delta.order 只包含 base 元素和本次新增元素，不包含前 mod 的新增
    # 需要把前 mod 新增元素插回到它们在 current 中相邻 base 元素之间

    # 步骤 1：从 current 中提取 base 元素间的前 mod 新增元素的位置关系
    # 记录每个 base ID 后面跟着哪些非 base ID
    after_base: dict[int, list[int]] = {0: []}  # 0 代表开头之前
    last_base_id = 0
    for eid in current_ids:
        if eid <= delta.base_count:
            last_base_id = eid
            if eid not in after_base:
                after_base[eid] = []
        elif eid in current_non_base:
            after_base.setdefault(last_base_id, []).append(eid)

    # 步骤 2：按 delta.order 构建新数组
    result: list[tuple[int, object]] = []
    # 重建 id_map（CHANGED 可能已修改值）
    id_map_final: dict[int, object] = dict(id_arr)
    # 加入新增元素
    id_map_final.update(new_items)

    for eid in delta.order:
        if eid == 0 or eid == -1:
            # 开头标记后的非 base 元素
            if eid == 0:
                for non_base_id in after_base.get(0, []):
                    if non_base_id not in deleted_ids and non_base_id in id_map_final:
                        result.append((non_base_id, id_map_final[non_base_id]))
            continue

        if eid in deleted_ids:
            if not allow_deletions and eid in id_map_final:
                result.append((eid, id_map_final[eid]))
            continue

        if eid in id_map_final:
            result.append((eid, id_map_final[eid]))

        # 在此 base 元素后插入 current 中跟在它后面的非 base 元素
        if eid <= delta.base_count:
            for non_base_id in after_base.get(eid, []):
                if non_base_id not in deleted_ids and non_base_id in id_map_final:
                    result.append((non_base_id, id_map_final[non_base_id]))

    return result


@profile
def apply_delta(
    base: dict[str, object],
    delta: DictDelta,
    schema: dict[str, object] | None = None,
    field_path: list[str] | None = None,
    allow_deletions: bool = False,
) -> dict[str, object]:
    """将 DictDelta 应用到 base dict 上，原地修改并返回。

    替代旧的 deep_merge 函数。
    """
    # 查找当前层的 schema 定义
    current_def: dict[str, object] | None = None
    if schema and field_path:
        current_def = get_field_def(schema, field_path)

    for key, diff in delta.items.items():
        child_path = field_path + [key] if field_path is not None else None

        if isinstance(diff, FieldDiff):
            if diff.kind == ChangeKind.DELETED:
                if allow_deletions:
                    base.pop(key, None)
            elif diff.kind == ChangeKind.ADDED:
                # 类型校验
                child_def = get_field_def(schema, child_path) if schema and child_path else None
                _, type_warn = _resolve_merge_strategy(child_def, None, diff, key)
                if type_warn:
                    diag.warn("merge", _build_warn_msg(child_path, type_warn))
                base[key] = copy.deepcopy(diff.value)
            else:
                # CHANGED
                child_def = get_field_def(schema, child_path) if schema and child_path else None
                _, type_warn = _resolve_merge_strategy(child_def, base.get(key), diff, key)
                if type_warn:
                    diag.warn("merge", _build_warn_msg(child_path, type_warn))
                base[key] = copy.deepcopy(diff.value)

        elif isinstance(diff, DictDelta):
            existing = base.get(key)
            if isinstance(existing, dict):
                apply_delta(existing, diff, schema, child_path,
                            allow_deletions=allow_deletions)
            else:
                # base 中不是 dict 或不存在：从 DictDelta 重建
                new_dict: dict[str, object] = {}
                apply_delta(new_dict, diff, schema, child_path,
                            allow_deletions=allow_deletions)
                base[key] = new_dict

        elif isinstance(diff, ArrayFieldDiff):
            # 检查 base[key] 是否已是 id_arr 格式（前一个 mod 留下的）
            base_arr = base.get(key)
            was_duplist = isinstance(base_arr, DupList)
            if isinstance(base_arr, list) and base_arr and isinstance(base_arr[0], tuple):
                # 已是 id_arr 格式，直接复用
                id_arr: list[tuple[int, object]] = base_arr
            elif isinstance(base_arr, list):
                # 首次转换：普通 list → id_arr
                id_arr = [(i + 1, elem) for i, elem in enumerate(base_arr)]
            else:
                id_arr = []

            # 应用 delta（内部自动完成 ID 重分配）
            new_id_arr = apply_array_delta(
                id_arr, diff, schema, child_path,
                allow_deletions=allow_deletions,
            )
            # 保留 DupList 类型（重复键序列化需要）
            if was_duplist:
                new_id_arr = DupList(new_id_arr)
            # 保留 id_arr 格式，不剥离 ID（由 merge_file 统一剥离）
            base[key] = new_id_arr

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


def _strip_id_arrays(data: object) -> object:
    """递归将 id_arr（list[tuple[int, object]]）还原为纯 list。

    apply_delta 在合并过程中以 (element_id, value) 格式保存数组，
    合并完成后需要统一剥离 ID，恢复为游戏使用的纯 list 格式。
    """
    if isinstance(data, dict):
        for key, val in data.items():
            data[key] = _strip_id_arrays(val)
        return data
    if isinstance(data, list):
        if data and isinstance(data[0], tuple):
            # id_arr 格式 → 提取 value 并递归
            stripped = [_strip_id_arrays(v) for _, v in data]
            return DupList(stripped) if isinstance(data, DupList) else stripped
        # 普通 list → 递归处理每个元素
        for i, elem in enumerate(data):
            data[i] = _strip_id_arrays(elem)
        return data
    return data


@profile
def merge_file(
    base_data: dict[str, object],
    mod_data_list: list[tuple[str, str, DictDelta, str]],
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
        overrides_dir: 用户 override 文件目录
        allow_deletions: 是否应用删除标记
    """
    result = MergeResult()
    file_name = Path(rel_path).name if rel_path else ""

    # sfx_config.json 等特殊文件：整文件替换
    if file_name in WHOLE_FILE_REPLACE:
        if mod_data_list:
            _, last_mod_name, _, _ = mod_data_list[-1]
            # 特殊文件不使用 delta，需要从原始 mod 数据重建——
            # 但此处传入的已是 delta，无法还原原始数据。
            # 暂时用 base + 最后一个 delta apply 的方式。
            current = copy.deepcopy(base_data)
            for _, _mod_name, delta, _ in mod_data_list:
                apply_delta(current, delta, schema, None,
                            allow_deletions=allow_deletions)
            _strip_id_arrays(current)
            result.merged_data = current
            if len(mod_data_list) > 1:
                diag.warn("merge", f"{rel_path}: 多个 mod 修改此文件（整文件替换模式），最终使用 {last_mod_name}")
        else:
            result.merged_data = copy.deepcopy(base_data)
        return result

    current = copy.deepcopy(base_data)

    # 确定 schema 根 key
    root_key = get_schema_root_key(schema) if schema else None

    for mod_id, mod_name, delta, source_file in mod_data_list:
        # 设置线程本地上下文，供 apply_delta 内部的警告使用
        merge_ctx.mod_name = mod_name
        merge_ctx.mod_id = mod_id
        merge_ctx.rel_path = rel_path
        merge_ctx.source_file = source_file

        fp: list[str] | None = [root_key] if root_key else None
        apply_delta(current, delta, schema, fp,
                    allow_deletions=allow_deletions)

        # 检查用户 override
        from .json_store import JsonStore
        override = JsonStore.instance().get_override(mod_id, rel_path)
        if override is not None:
            current = copy.deepcopy(override)

    _strip_id_arrays(current)
    result.merged_data = current
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
        mod_data_list: list[tuple[str, str, DictDelta, str]] = []
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
