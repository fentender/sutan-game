"""
全局 Delta 缓存管理器

启动时预计算所有 mod 相对于游戏本体的 delta，缓存结果供冲突分析、
合并、Diff 对话框等模块直接取用，避免重复计算。

所有方法和属性均为类级别，直接通过 ModDelta.get(...) 调用。
"""
import copy
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .array_match import (
    COMMON_MATCH_KEYS,
    is_obj_array,
    match_by_consume,
    match_by_heuristic,
    match_by_index,
    match_by_keys,
)
from .json_parser import DupList
from .json_store import JsonStore
from .profiler import profile
from .schema_loader import (
    get_field_def,
    get_schema_root_key,
    load_schemas,
    resolve_schema,
)
from .smart_rules import smart_allow_deletion
from .type_utils import classify_json
from .types import (
    ArrayFieldDiff,
    ArrayMatching,
    ChangeKind,
    DiffDict,
    FieldDiff,
    MergeMode,
    ProgressCallback,
)


def find_array_match_key(arr: list[object]) -> str | None:
    """在对象数组中找到可用于匹配的唯一标识字段"""
    if not arr or not all(isinstance(x, dict) for x in arr):
        return None
    for key in COMMON_MATCH_KEYS:
        values = []
        for item in arr:
            if not isinstance(item, dict) or key not in item:
                break
            values.append(item[key])
        else:
            if len({(type(v), v) for v in values}) == len(values):
                return key
    return None


# ==================== Delta 产出（内部函数） ====================


def _select_array_matching(
    base: list[object],
    mod: list[object],
    schema: dict[str, object] | None,
    field_path: list[str] | None,
) -> ArrayMatching:
    """根据 schema 规则选择数组匹配策略。"""
    return match_by_heuristic(base, mod)

    merge_strategy: str | None = None
    schema_match_keys: list[str] | None = None
    if schema and field_path:
        field_def = get_field_def(schema, field_path)
        if field_def:
            ms = field_def.get("__merge__")
            merge_strategy = str(ms) if ms is not None else None
            if merge_strategy == "smart_match":
                mk = field_def.get("__match_key__")
                schema_match_keys = mk if isinstance(mk, list) else None

    if is_obj_array(mod) and (not base or is_obj_array(base)) and schema_match_keys:
        return match_by_keys(
            base,
            mod,
            schema_match_keys,
        )
    if merge_strategy == "append":
        return match_by_consume(base, mod)
    return match_by_index(base, mod)


def _recursive_delta(
    base: object,
    mod: object,
    schema: dict[str, object] | None = None,
    field_path: list[str] | None = None,
    merge_mode: MergeMode = MergeMode.NORMAL,
) -> DiffDict | ArrayFieldDiff | FieldDiff | None:
    """递归比较，返回 mod 相对于 base 的变化部分。None 表示无差异。

    返回类型:
    - DiffDict: dict 间的部分字段变化
    - ArrayFieldDiff: 数组间的元素级变化
    - FieldDiff: 标量/原子级变化（叶子节点）
    - None: 无差异
    """
    # 早期相等退出
    if base == mod:
        return None

    if isinstance(base, dict) and isinstance(mod, dict):
        items: dict[str, FieldDiff | DiffDict | ArrayFieldDiff] = {}
        for key, mod_val in mod.items():
            child_path = field_path + [key] if field_path is not None else None
            if key not in base:
                # 新增字段：数组仍需走 delta 逻辑以产出 ArrayFieldDiff
                if isinstance(mod_val, list) and schema and child_path:
                    empty_base: DupList | list[object] = (
                        DupList() if isinstance(mod_val, DupList) else []
                    )
                    sub = _recursive_delta(empty_base, mod_val, schema, child_path, merge_mode)
                    if isinstance(sub, ArrayFieldDiff):
                        items[key] = sub
                    else:
                        items[key] = FieldDiff(ChangeKind.ADDED, copy.deepcopy(mod_val))
                else:
                    items[key] = FieldDiff(ChangeKind.ADDED, copy.deepcopy(mod_val))
            else:
                sub = _recursive_delta(base[key], mod_val, schema, child_path, merge_mode)
                if sub is not None:
                    items[key] = sub
        # 删除的字段
        for key in base:
            if key not in mod:
                if merge_mode == MergeMode.SMART:
                    child_path = field_path + [key] if field_path is not None else []
                    if not smart_allow_deletion(child_path, is_array_element=False):
                        continue
                items[key] = FieldDiff(ChangeKind.DELETED, None)
        return DiffDict(items) if items else None

    # ── 数组归一化：DupList / 标量 → list ──
    if isinstance(base, DupList) or isinstance(mod, DupList):
        base = base if isinstance(base, DupList) else DupList([base])
        mod = mod if isinstance(mod, DupList) else DupList([mod])
    elif isinstance(base, list) and not isinstance(mod, (list, dict)):
        mod = [mod]
    elif isinstance(mod, list) and not isinstance(base, (list, dict)):
        base = [base]

    if isinstance(base, list) and isinstance(mod, list):
        # 从 schema 查询合并策略
        matching = _select_array_matching(base, mod, schema, field_path)
        return _array_delta_from_matching(
            base, mod, matching, schema, field_path,
            is_duplist=isinstance(mod, DupList),
            merge_mode=merge_mode,
        )

    # 标量比较
    if base == mod:
        return None
    return FieldDiff(ChangeKind.CHANGED, copy.deepcopy(mod))


def _build_order(
    matching: ArrayMatching,
    added_id_map: dict[int, int],
) -> list[int]:
    """构建 ArrayFieldDiff.order 列表，保持 mod 中元素的实际顺序。

    参数:
        matching: 匹配结果
        added_id_map: mod_idx → added_id 的映射（仅新增元素）

    规则:
    - 按 mod 数组的顺序排列所有元素（配对的用 base_id，新增的用 added_id）
    - DELETED 的 base 元素不出现在 order 中
    - 边界标记 0（开头）和 -1（末尾）始终包含
    """
    # mod_idx → base_id (1-based) 的映射
    paired_map: dict[int, int] = {
        mod_idx: base_idx + 1 for base_idx, mod_idx in matching.pairs
    }

    # 按 mod 数组顺序，收集所有 mod 中出现的元素的 ID
    mod_len = max(
        (mi for _, mi in matching.pairs),
        default=-1,
    )
    mod_len = max(mod_len, max(matching.unmatched_mod, default=-1))
    total_mod = mod_len + 1

    order: list[int] = [0]
    for mi in range(total_mod):
        if mi in paired_map:
            order.append(paired_map[mi])
        elif mi in added_id_map:
            order.append(added_id_map[mi])
    order.append(-1)
    return order


def _array_delta_from_matching(
    base_arr: list[object],
    mod_arr: list[object],
    matching: ArrayMatching,
    schema: dict[str, object] | None = None,
    field_path: list[str] | None = None,
    is_duplist: bool = False,
    merge_mode: MergeMode = MergeMode.NORMAL,
) -> ArrayFieldDiff | None:
    """根据匹配结果计算 ArrayFieldDiff。"""
    base_count = len(base_arr)

    diffs: list[FieldDiff] = []
    indices: list[int] = []
    next_added_id = base_count + 1

    # 配对元素：递归比较，有差异则记录 CHANGED
    for base_idx, mod_idx in matching.pairs:
        elem_delta = _recursive_delta(
            base_arr[base_idx], mod_arr[mod_idx], schema, field_path, merge_mode,
        )
        if elem_delta is not None:
            base_id = base_idx + 1  # 1-based
            diffs.append(FieldDiff(ChangeKind.CHANGED, elem_delta))
            indices.append(base_id)

    # 未匹配的 mod 元素 → 新增
    added_id_map: dict[int, int] = {}
    for mod_idx in matching.unmatched_mod:
        diffs.append(FieldDiff(ChangeKind.ADDED, copy.deepcopy(mod_arr[mod_idx])))
        indices.append(next_added_id)
        added_id_map[mod_idx] = next_added_id
        next_added_id += 1

    # 未匹配的 base 元素 → 删除（SMART 模式下数组元素禁删）
    if merge_mode != MergeMode.SMART:
        for base_idx in matching.unmatched_base:
            base_id = base_idx + 1  # 1-based
            diffs.append(FieldDiff(ChangeKind.DELETED, None))
            indices.append(base_id)

    if not diffs:
        return None

    order = _build_order(matching, added_id_map)
    return ArrayFieldDiff(
        diffs=diffs, base_count=base_count, indices=indices, order=order,
        is_duplist=is_duplist,
    )


# ==================== ADAPTIVE 模式：delta 重映射 ====================


def _remap_delta_to_current(
    delta: DiffDict,
    hist_data: object,
    current_data: object,
) -> DiffDict | None:
    """将基于历史 base 计算的 delta，重映射到当前 base 的索引/key 体系。

    用于 ADAPTIVE 模式：delta 由 `mod - hist_base` 计算而来，但需要应用到
    当前游戏本体上。若当前 base 与历史 base 存在差异（字段增删、数组元素
    位置变化等），直接应用 delta 会产生幽灵变更或索引错位。

    重映射规则见 plan file。返回 None 表示 delta 全部清空。
    """
    if not isinstance(hist_data, dict) or not isinstance(current_data, dict):
        # 类型不匹配时保守返回原 delta（上游会按原路径应用）
        return delta

    new_items: dict[str, FieldDiff | DiffDict | ArrayFieldDiff] = {}

    for key, entry in delta.items.items():
        hist_val = hist_data.get(key) if key in hist_data else None
        hist_has = key in hist_data
        cur_val = current_data.get(key) if key in current_data else None
        cur_has = key in current_data

        if isinstance(entry, FieldDiff):
            remapped = _remap_field_diff(entry, hist_has, hist_val, cur_has, cur_val)
            if remapped is not None:
                new_items[key] = remapped

        elif isinstance(entry, DiffDict):
            # 嵌套 dict delta：递归
            if cur_has and isinstance(cur_val, dict) and isinstance(hist_val, dict):
                sub = _remap_delta_to_current(entry, hist_val, cur_val)
                if sub is not None and sub.items:
                    new_items[key] = sub
            elif not cur_has:
                # 当前 base 中该字段整体消失：delta 中的嵌套改动无处可施，丢弃
                continue
            else:
                # 类型不匹配（如 dict → 标量），保守丢弃
                continue

        elif isinstance(entry, ArrayFieldDiff):
            if cur_has and isinstance(cur_val, list) and isinstance(hist_val, list):
                remapped_arr = _remap_array_diff(entry, hist_val, cur_val)
                if remapped_arr is not None:
                    new_items[key] = remapped_arr
            # 其余情况丢弃

    if not new_items:
        return None
    return DiffDict(items=new_items)


def _remap_field_diff(
    entry: FieldDiff,
    hist_has: bool,
    hist_val: object,
    cur_has: bool,
    cur_val: object,
) -> FieldDiff | None:
    """重映射单个 FieldDiff，返回 None 表示丢弃。"""
    base_kind = entry.kind.base_kind
    modifier = entry.kind & ~ChangeKind.CHANGED  # 保留 OVERRIDE/MULTI_MOD 等

    if base_kind == ChangeKind.DELETED:
        # 删除的字段当前 base 已不存在 → 丢弃
        if not cur_has:
            return None
        return entry

    if base_kind == ChangeKind.ADDED:
        if cur_has:
            # 已存在且值相同 → 丢弃；不同 → 改为 CHANGED
            if cur_val == entry.value:
                return None
            new_kind = ChangeKind.CHANGED | modifier
            return FieldDiff(
                kind=new_kind,
                value=entry.value,
                old_value=cur_val,
                version=entry.version,
            )
        return entry

    if base_kind == ChangeKind.CHANGED:
        if not cur_has:
            # 历史 base 有、当前 base 无：改为 ADDED
            new_kind = ChangeKind.ADDED | modifier
            return FieldDiff(
                kind=new_kind,
                value=entry.value,
                old_value=None,
                version=entry.version,
            )
        if cur_val == entry.value:
            # 当前值已等于新值 → 丢弃
            return None
        # 更新 old_value 为当前值
        return FieldDiff(
            kind=entry.kind,
            value=entry.value,
            old_value=cur_val,
            version=entry.version,
        )

    # ORIGIN 或其它：不应出现在稀疏 delta 中，保留原样
    return entry


def _remap_array_diff(
    delta: ArrayFieldDiff,
    hist_arr: list[object],
    current_arr: list[object],
) -> ArrayFieldDiff | None:
    """将 ArrayFieldDiff 从历史 base 索引体系重映射到当前 base。

    步骤：
    1. 用启发式匹配计算 hist_idx → current_idx 映射
    2. 遍历 delta.diffs/indices，转换 CHANGED/DELETED 元素的 ID
    3. ADDED 元素重新分配 ID = current_base_count + 序号
    4. 重写 base_count 和 order
    """
    hist_base_count = delta.base_count
    current_base_count = len(current_arr)

    # 启发式匹配：hist_arr 作为 base，current_arr 作为 mod
    matching = match_by_heuristic(hist_arr, current_arr)
    hist_to_current: dict[int, int] = dict(matching.pairs)

    new_diffs: list[FieldDiff] = []
    new_indices: list[int] = []
    # hist_id → new_id 的映射（用于重写 order）
    id_remap: dict[int, int] = {}

    added_counter = 0

    for fd, eid in zip(delta.diffs, delta.indices, strict=True):
        base_kind = fd.kind.base_kind

        if eid > hist_base_count:
            # ADDED 元素，重新分配 ID
            added_counter += 1
            new_id = current_base_count + added_counter
            new_diffs.append(fd)
            new_indices.append(new_id)
            id_remap[eid] = new_id
            continue

        # CHANGED / DELETED：eid 是历史 base 的 1-based ID
        hist_idx = eid - 1
        if hist_idx not in hist_to_current:
            # 历史元素在当前 base 中没有对应（已被游戏删除）→ 丢弃
            continue
        cur_idx = hist_to_current[hist_idx]
        new_id = cur_idx + 1  # 1-based

        if base_kind == ChangeKind.CHANGED and isinstance(fd.value, DiffDict):
            # 递归重映射子 DiffDict（用对应的历史/当前元素作为参照）
            hist_elem = hist_arr[hist_idx]
            cur_elem = current_arr[cur_idx]
            sub = _remap_delta_to_current(fd.value, hist_elem, cur_elem)
            if sub is None or not sub.items:
                # 子 delta 完全清空 → 丢弃此元素变更
                continue
            new_diffs.append(FieldDiff(
                kind=fd.kind, value=sub,
                old_value=fd.old_value, version=fd.version,
            ))
            new_indices.append(new_id)
            id_remap[eid] = new_id
        elif base_kind == ChangeKind.CHANGED and isinstance(fd.value, ArrayFieldDiff):
            hist_elem = hist_arr[hist_idx]
            cur_elem = current_arr[cur_idx]
            if isinstance(hist_elem, list) and isinstance(cur_elem, list):
                sub_arr = _remap_array_diff(fd.value, hist_elem, cur_elem)
                if sub_arr is None:
                    continue
                new_diffs.append(FieldDiff(
                    kind=fd.kind, value=sub_arr,
                    old_value=fd.old_value, version=fd.version,
                ))
                new_indices.append(new_id)
                id_remap[eid] = new_id
            else:
                new_diffs.append(fd)
                new_indices.append(new_id)
                id_remap[eid] = new_id
        else:
            # CHANGED 标量 或 DELETED：直接重映射 ID
            new_diffs.append(fd)
            new_indices.append(new_id)
            id_remap[eid] = new_id

    if not new_diffs:
        return None

    # 重写 order：order 中包含 mod 数组所有元素的 ID（含未变更的 ORIGIN 元素），
    # 不能只依赖 id_remap（它仅含 diff 元素）。需要用启发式匹配的全量 hist→current
    # 映射来转换配对元素的 ID，ADDED 元素用 id_remap。
    hist_id_to_current_id: dict[int, int] = {
        hist_idx + 1: cur_idx + 1
        for hist_idx, cur_idx in matching.pairs
    }
    new_order: list[int] = []
    for eid in delta.order:
        if eid == 0 or eid == -1:
            new_order.append(eid)
            continue
        if eid > hist_base_count:
            # ADDED 元素：用 id_remap
            if eid in id_remap:
                new_order.append(id_remap[eid])
        else:
            # 配对或 ORIGIN 元素：用全局 hist→current 映射
            if eid in hist_id_to_current_id:
                new_order.append(hist_id_to_current_id[eid])
            # 历史元素在当前 base 中无对应 → 丢弃
    return ArrayFieldDiff(
        diffs=new_diffs,
        base_count=current_base_count,
        indices=new_indices,
        order=new_order,
        is_duplist=delta.is_duplist,
    )


# ==================== Delta 计算入口 ====================


@profile
def compute_delta(
    base_data: dict[str, object],
    mod_data: dict[str, object],
    file_type: str,
    schema: dict[str, object] | None = None,
    root_key: str | None = None,
    merge_mode: MergeMode = MergeMode.NORMAL,
) -> DiffDict | None:
    """计算 mod 相对于游戏本体的实际差异，产出 DiffDict。

    只提取 mod 真正修改的部分，忽略与本体完全相同的内容。
    对 dictionary 类型文件按条目级 + 字段级递归 diff，
    对 entity/config 类型文件按字段级递归 diff。
    """
    field_path = [root_key] if root_key else None

    if not base_data:
        # 本体无此文件，全部是新增
        result = _recursive_delta({}, mod_data, schema, field_path, merge_mode)
        if isinstance(result, DiffDict):
            return result
        # 无变化或非 dict 结果，包装为 DiffDict
        if result is None:
            # 整个 mod_data 与空 dict 不同，每个 key 都是新增
            items: dict[str, FieldDiff | DiffDict | ArrayFieldDiff] = {}
            for k, v in mod_data.items():
                items[k] = FieldDiff(ChangeKind.ADDED, copy.deepcopy(v))
            return DiffDict(items) if items else None
        return None

    if file_type == "dictionary":
        items = {}
        for key, mod_val in mod_data.items():
            if key not in base_data:
                items[key] = FieldDiff(ChangeKind.ADDED, copy.deepcopy(mod_val))
            else:
                sub = _recursive_delta(base_data[key], mod_val, schema, field_path, merge_mode)
                if sub is not None:
                    items[key] = sub
        # dictionary 顶层键是实体 ID，mod 不包含某 ID 不代表删除，
        # 任何模式下都不产生 DELETED
        return DiffDict(items) if items else None
    else:
        # entity/config
        result = _recursive_delta(base_data, mod_data, schema, field_path, merge_mode)
        if isinstance(result, DiffDict):
            return result
        return None


# ==================== Delta 展平 ====================


def flatten_delta(
    delta: DiffDict,
    prefix: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], FieldDiff]]:
    """将 DiffDict 树展平为 (路径, FieldDiff) 列表。

    路径用 tuple[str, ...] 表示，数组元素用 "[{elem_id}]" 格式。
    """
    result: list[tuple[tuple[str, ...], FieldDiff]] = []
    for key, diff in delta.items.items():
        path = prefix + (key,)
        if isinstance(diff, FieldDiff):
            result.append((path, diff))
        elif isinstance(diff, DiffDict):
            result.extend(flatten_delta(diff, path))
        elif isinstance(diff, ArrayFieldDiff):
            for field_diff, elem_id in zip(diff.diffs, diff.indices, strict=True):
                elem_path = path + (f"[{elem_id}]",)
                if isinstance(field_diff.value, DiffDict):
                    # CHANGED 元素的内部字段变化
                    result.extend(flatten_delta(field_diff.value, elem_path))
                else:
                    result.append((elem_path, field_diff))
    return result


# ==================== 全局 Delta 缓存 ====================


class ModDelta:
    """全局 Delta 缓存管理器（纯静态类）。

    启动时调用 init() 预计算所有 delta，后续通过 get() 直接取缓存结果。
    """

    # (mod_id, rel_path) → DiffDict | None
    _cache: dict[tuple[str, str], DiffDict | None] = {}
    _progress: tuple[int, int] = (0, 0)
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def init(
        cls,
        mod_ids: list[str],
        schema_dir: Path | None = None,
        progress_cb: ProgressCallback | None = None,
        merge_mode: MergeMode = MergeMode.SMART,
        mod_merge_modes: dict[str, MergeMode] | None = None,
        mod_update_times: dict[str, int] | None = None,
        history_dir: Path | None = None,
    ) -> None:
        """预计算所有 mod 的 delta 并缓存。

        参数:
            mod_ids: 按优先级排序的 mod ID 列表
            schema_dir: schema 规则文件目录
            progress_cb: 进度回调 (completed, total)
            merge_mode: 全局合并模式
            mod_merge_modes: per-mod 合并模式覆盖
            mod_update_times: mod_id → update_time（ADAPTIVE 模式需要）
            history_dir: history_config 目录路径（ADAPTIVE 模式需要）
        """
        # 延迟导入避免循环依赖（merger → delta_store → merger）
        from .merger import apply_delta

        store = JsonStore.instance()
        schemas = load_schemas(schema_dir) if schema_dir else {}

        # ADAPTIVE 模式：解析历史版本目录
        from .history_config import find_base_version, parse_history_versions
        history_versions = parse_history_versions(history_dir) if history_dir else []
        # 预计算每个 mod 对应的历史基准目录（mod_id → Path | None）
        _adaptive_base_dirs: dict[str, Path | None] = {}
        if history_versions and mod_update_times:
            for mod_id in mod_ids:
                ut = mod_update_times.get(mod_id)
                if ut is not None:
                    _adaptive_base_dirs[mod_id] = find_base_version(ut, history_versions)

        # 收集所有需要计算的 (mod_id, rel_path) 任务
        tasks: list[tuple[str, str]] = []
        for mod_id in mod_ids:
            for rel_path in store.mod_files(mod_id):
                tasks.append((mod_id, rel_path))

        total = len(tasks)
        completed = 0
        with cls._lock:
            cls._cache.clear()
            cls._progress = (0, total)

        if progress_cb:
            progress_cb(0, total)

        def _effective_mode(mod_id: str) -> MergeMode:
            if mod_merge_modes and mod_id in mod_merge_modes:
                return mod_merge_modes[mod_id]
            return merge_mode

        # 检查是否有任何 REPLACE mod
        has_replace = any(
            _effective_mode(mid) == MergeMode.REPLACE for mid in mod_ids
        )

        def _compute_one(
            task: tuple[str, str],
            effective: MergeMode,
        ) -> tuple[str, str, DiffDict | None]:
            mod_id, rel_path = task
            base_data = store.get_base(rel_path)

            # ADAPTIVE：尝试用历史版本的 base_data 替代当前本体
            delta_mode = effective
            hist_base_for_remap: dict[str, object] | None = None
            if effective == MergeMode.ADAPTIVE:
                hist_dir = _adaptive_base_dirs.get(mod_id)
                if hist_dir is not None:
                    hist_file = hist_dir / rel_path
                    if hist_file.exists():
                        hist_base_for_remap = JsonStore.parse_file(hist_file)
                        base_data = hist_base_for_remap
                # 合并行为等同 SMART
                delta_mode = MergeMode.SMART

            mod_data = store.get_mod(mod_id, rel_path)
            file_type = classify_json(base_data) if base_data else "config"
            schema = resolve_schema(rel_path, schemas) if schemas else None
            root_key = get_schema_root_key(schema) if schema else None
            delta = compute_delta(base_data, mod_data, file_type,
                                  schema=schema, root_key=root_key,
                                  merge_mode=delta_mode)

            # ADAPTIVE：delta 基于历史 base 算出，需重映射到当前 base 的索引/key 体系
            if (effective == MergeMode.ADAPTIVE
                    and hist_base_for_remap is not None
                    and delta is not None):
                current_base = store.get_base(rel_path)
                if current_base and current_base is not hist_base_for_remap:
                    delta = _remap_delta_to_current(
                        delta, hist_base_for_remap, current_base,
                    )
            return mod_id, rel_path, delta

        if has_replace:
            # 有 REPLACE mod 时需要按文件分组、按 mod 顺序处理
            # 因为 REPLACE delta 依赖累积合并状态
            # 按 rel_path 分组
            from collections import defaultdict
            tasks_by_file: dict[str, list[str]] = defaultdict(list)
            for mod_id in mod_ids:
                for rel_path in store.mod_files(mod_id):
                    tasks_by_file[rel_path].append(mod_id)

            for rel_path, file_mod_ids in tasks_by_file.items():
                base_data = store.get_base(rel_path)
                file_type = classify_json(base_data) if base_data else "config"
                schema = resolve_schema(rel_path, schemas) if schemas else None
                root_key = get_schema_root_key(schema) if schema else None

                # 累积合并状态（仅 REPLACE mod 需要）
                current: DiffDict | None = None

                for mod_id in file_mod_ids:
                    effective = _effective_mode(mod_id)
                    mod_data = store.get_mod(mod_id, rel_path)

                    if effective == MergeMode.REPLACE:
                        # REPLACE: delta 基于累积合并状态
                        if current is None:
                            current = DiffDict.from_dict(base_data)
                        cumulative_data = current.to_dict()
                        delta = compute_delta(
                            cumulative_data, mod_data, file_type,
                            schema=schema, root_key=root_key,
                        )
                    elif effective == MergeMode.ADAPTIVE:
                        # ADAPTIVE: delta 基于历史版本计算，再重映射到当前 base
                        adaptive_base = base_data
                        hist_base_used: dict[str, object] | None = None
                        hist_dir = _adaptive_base_dirs.get(mod_id)
                        if hist_dir is not None:
                            hist_file = hist_dir / rel_path
                            if hist_file.exists():
                                hist_base_used = JsonStore.parse_file(hist_file)
                                adaptive_base = hist_base_used
                        delta = compute_delta(
                            adaptive_base, mod_data, file_type,
                            schema=schema, root_key=root_key,
                            merge_mode=MergeMode.SMART,
                        )
                        if (hist_base_used is not None
                                and delta is not None
                                and base_data
                                and base_data is not hist_base_used):
                            delta = _remap_delta_to_current(
                                delta, hist_base_used, base_data,
                            )
                    else:
                        # NORMAL/SMART: delta 基于游戏本体
                        delta = compute_delta(
                            base_data, mod_data, file_type,
                            schema=schema, root_key=root_key,
                            merge_mode=effective,
                        )

                    with cls._lock:
                        cls._cache[(mod_id, rel_path)] = delta
                        completed += 1
                        cls._progress = (completed, total)
                    if progress_cb:
                        progress_cb(completed, total)

                    # 维护累积状态（无论什么模式都需要，因为后续 REPLACE mod 可能依赖）
                    if delta is not None:
                        if current is None:
                            current = DiffDict.from_dict(base_data)
                        fp: list[str] | None = [root_key] if root_key else None
                        apply_delta(current, delta, schema, fp)
        else:
            # 无 REPLACE mod 时，走原有的并行/串行逻辑
            if total <= 20:
                for task in tasks:
                    effective = _effective_mode(task[0])
                    mod_id, rel_path, delta = _compute_one(task, effective)
                    with cls._lock:
                        cls._cache[(mod_id, rel_path)] = delta
                        completed += 1
                        cls._progress = (completed, total)
                    if progress_cb:
                        progress_cb(completed, total)
            else:
                with ThreadPoolExecutor() as pool:
                    futures = {
                        pool.submit(_compute_one, t, _effective_mode(t[0])): t
                        for t in tasks
                    }
                    for future in as_completed(futures):
                        mod_id, rel_path, delta = future.result()
                        with cls._lock:
                            cls._cache[(mod_id, rel_path)] = delta
                            completed += 1
                            cls._progress = (completed, total)
                        if progress_cb:
                            progress_cb(completed, total)

    @classmethod
    def get(cls, mod_id: str, rel_path: str) -> DiffDict | None:
        """获取缓存的 delta 结果。"""
        return cls._cache[(mod_id, rel_path)]

    @classmethod
    def has(cls, mod_id: str, rel_path: str) -> bool:
        """检查是否有缓存的 delta"""
        return (mod_id, rel_path) in cls._cache

    @classmethod
    def progress(cls) -> tuple[int, int]:
        """返回当前进度 (completed, total)"""
        with cls._lock:
            return cls._progress

    @classmethod
    def invalidate(cls) -> None:
        """清空缓存（ID 重分配等场景后需重新计算）"""
        with cls._lock:
            cls._cache.clear()
            cls._progress = (0, 0)

    @classmethod
    def clear(cls) -> None:
        """清空所有状态"""
        with cls._lock:
            cls._cache.clear()
            cls._progress = (0, 0)
