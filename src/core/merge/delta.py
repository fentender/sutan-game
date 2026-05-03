"""
全局 Delta 缓存管理器

启动时预计算所有 mod 相对于游戏本体的 delta，缓存结果供冲突分析、
合并、Diff 对话框等模块直接取用，避免重复计算。

所有方法和属性均为类级别，直接通过 ModDelta.get(...) 调用。
"""
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..platform.history import (
    PathResolver,
    find_base_version,
    parse_history_versions,
    set_resolver,
)
from ..infra.profiler import profile
from ..infra.types import *
from ..json.classify import classify_json
from ..json.store import JsonStore
from ..schema.loader import (
    get_schema_root_key,
    load_schemas,
    resolve_schema,
)
from .array_match import match_by_heuristic
from .rules import smart_allow_deletion


# ==================== Delta 产出（内部函数） ====================


def _recursive_delta(
    base: JsonValue,
    mod: JsonValue,
    field_path: tuple[str, ...] | None = None,
    merge_mode: MergeMode = MergeMode.NORMAL,
) -> DictFieldDiff | ArrayFieldDiff | FieldDiff | None:
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
        items: dict[str, DiffEntry] = {}
        for key, mod_val in mod.items():
            child_path = field_path + (key,) if field_path is not None else None
            if key not in base:
                items[key] = FieldDiff(ChangeKind.ADDED, mod_val)
            else:
                sub = _recursive_delta(base[key], mod_val, child_path, merge_mode)
                if sub is not None:
                    items[key] = sub
        # 删除的字段
        for key in base:
            if key not in mod:
                if merge_mode == MergeMode.SMART:
                    child_path = field_path + (key,) if field_path is not None else ()
                    if not smart_allow_deletion(child_path, is_array_element=False):
                        continue
                items[key] = FieldDiff(ChangeKind.DELETED, None, old_value=base[key])
        return DictFieldDiff(items=items, kind=ChangeKind.ORIGIN) if items else None

    # ── 数组归一化：DupList / 标量 → list ──
    if isinstance(base, DupList) or isinstance(mod, DupList):
        base = base if isinstance(base, DupList) else DupList([base])
        mod = mod if isinstance(mod, DupList) else DupList([mod])
    elif isinstance(base, list) and not isinstance(mod, (list, dict)):
        mod = [mod]
    elif isinstance(mod, list) and not isinstance(base, (list, dict)):
        base = [base]

    if isinstance(base, list) and isinstance(mod, list):
        matching = match_by_heuristic(base, mod)
        return _array_delta_from_matching(
            base, mod, matching, field_path,
            is_duplist=isinstance(mod, DupList),
            merge_mode=merge_mode,
        )

    # 标量比较
    if base == mod:
        return None
    return FieldDiff(ChangeKind.CHANGED, mod)


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
    base_arr: JsonArray,
    mod_arr: JsonArray,
    matching: ArrayMatching,
    field_path: tuple[str, ...] | None = None,
    is_duplist: bool = False,
    merge_mode: MergeMode = MergeMode.NORMAL,
) -> ArrayFieldDiff | None:
    """根据匹配结果计算 ArrayFieldDiff。"""
    base_count = len(base_arr)

    diffs: list[DiffEntry] = []
    indices: list[int] = []
    next_added_id = base_count + 1

    for base_idx, mod_idx in matching.pairs:
        elem_delta = _recursive_delta(
            base_arr[base_idx], mod_arr[mod_idx], field_path, merge_mode,
        )
        if elem_delta is not None:
            base_id = base_idx + 1
            diffs.append(elem_delta)
            indices.append(base_id)

    added_id_map: dict[int, int] = {}
    for mod_idx in matching.unmatched_mod:
        diffs.append(FieldDiff(ChangeKind.ADDED, mod_arr[mod_idx]))
        indices.append(next_added_id)
        added_id_map[mod_idx] = next_added_id
        next_added_id += 1

    # 未匹配的 base 元素 → 删除（SMART 模式下数组元素禁删）
    if merge_mode != MergeMode.SMART:
        for base_idx in matching.unmatched_base:
            base_id = base_idx + 1  # 1-based
            diffs.append(FieldDiff(ChangeKind.DELETED, None, old_value=base_arr[base_idx]))
            indices.append(base_id)

    if not diffs:
        return None

    order = _build_order(matching, added_id_map)
    return ArrayFieldDiff(
        diffs=diffs, base_count=base_count, indices=indices, order=order,
        is_duplist=is_duplist, old_order=None, kind=ChangeKind.ORIGIN,
    )


# ==================== ADAPTIVE 模式：delta 重映射 ====================


def _remap_delta_to_current(
    delta: DictFieldDiff,
    hist_data: object,
    current_data: object,
) -> DictFieldDiff | None:
    """将基于历史 base 计算的 delta，重映射到当前 base 的索引/key 体系。

    用于 ADAPTIVE 模式：delta 由 `mod - hist_base` 计算而来，但需要应用到
    当前游戏本体上。若当前 base 与历史 base 存在差异（字段增删、数组元素
    位置变化等），直接应用 delta 会产生幽灵变更或索引错位。

    重映射规则见 plan file。返回 None 表示 delta 全部清空。
    """
    if not isinstance(hist_data, dict) or not isinstance(current_data, dict):
        # 类型不匹配时保守返回原 delta（上游会按原路径应用）
        return delta

    new_items: dict[str, DiffEntry] = {}

    for key, entry in delta.items.items():
        hist_val = hist_data.get(key) if key in hist_data else None
        hist_has = key in hist_data
        cur_val = current_data.get(key) if key in current_data else None
        cur_has = key in current_data

        if isinstance(entry, FieldDiff):
            remapped = _remap_field_diff(entry, cur_has, cur_val)
            if remapped is not None:
                new_items[key] = remapped

        elif isinstance(entry, DictFieldDiff):
            # 嵌套 dict delta：递归
            if cur_has and isinstance(cur_val, dict) and isinstance(hist_val, dict):
                sub = _remap_delta_to_current(entry, hist_val, cur_val)
                if sub is not None and sub.items:
                    new_items[key] = sub

        elif isinstance(entry, ArrayFieldDiff):
            # 标量归一化：delta 计算阶段将标量包装为单元素数组产出 ArrayFieldDiff，
            # 重映射时需要对 hist_val/cur_val 做同样的归一化
            h = [hist_val] if hist_has and not isinstance(hist_val, (list, dict)) else hist_val
            c = [cur_val] if cur_has and not isinstance(cur_val, (list, dict)) else cur_val
            if cur_has and isinstance(c, list) and isinstance(h, list):
                remapped_arr = _remap_array_diff(entry, h, c)
                if remapped_arr is not None:
                    new_items[key] = remapped_arr
            # 其余情况丢弃

    if not new_items:
        return None
    return DictFieldDiff(items=new_items, kind=delta.kind)


def _set_version(entry: DiffEntry, version: int) -> None:
    """递归设置 DiffEntry 树中所有 FieldDiff 的 version。"""
    if isinstance(entry, FieldDiff):
        object.__setattr__(entry, "version", version)
    elif isinstance(entry, DictFieldDiff):
        for sub in entry.items.values():
            _set_version(sub, version)
    elif isinstance(entry, ArrayFieldDiff):
        for sub in entry.diffs:
            _set_version(sub, version)


def _remap_field_diff(
    entry: FieldDiff,
    cur_has: bool,
    cur_val: JsonValue,
) -> DiffEntry | None:
    """重映射单个 FieldDiff，返回 None 表示丢弃。

    输入是 compute_delta 产出的原始 delta，不含 MULTI_MOD/OVERRIDE 标志位。
    当新旧值均为复合类型时递归比较，保证 FieldDiff(CHANGED) 只含标量。
    """
    base_kind = entry.kind.base_kind

    if base_kind == ChangeKind.DELETED:
        if not cur_has:
            return None
        return FieldDiff(ChangeKind.DELETED, None, old_value=cur_val, version=entry.version)

    if base_kind == ChangeKind.ADDED:
        if cur_has:
            if cur_val == entry.value:
                return None
            result = _recursive_delta(cur_val, entry.value)
            if result is None:
                return None
            _set_version(result, entry.version)
            return result
        return entry

    if base_kind == ChangeKind.CHANGED:
        if not cur_has:
            return FieldDiff(
                kind=ChangeKind.ADDED,
                value=entry.value,
                old_value=None,
                version=entry.version,
            )
        if cur_val == entry.value:
            return None
        result = _recursive_delta(cur_val, entry.value)
        if result is None:
            return None
        _set_version(result, entry.version)
        return result

    # ORIGIN：不应出现在稀疏 delta 中，保留原样
    return entry


def _remap_array_diff(
    delta: ArrayFieldDiff,
    hist_arr: JsonArray,
    current_arr: JsonArray,
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

    new_diffs: list[DiffEntry] = []
    new_indices: list[int] = []
    id_remap: dict[int, int] = {}

    added_counter = 0

    for fd, eid in zip(delta.diffs, delta.indices, strict=True):
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
            continue
        cur_idx = hist_to_current[hist_idx]
        new_id = cur_idx + 1  # 1-based

        if isinstance(fd, DictFieldDiff):
            hist_elem = hist_arr[hist_idx]
            cur_elem = current_arr[cur_idx]
            sub = _remap_delta_to_current(fd, hist_elem, cur_elem)
            if sub is None or not sub.items:
                continue
            new_diffs.append(sub)
            new_indices.append(new_id)
            id_remap[eid] = new_id
        elif isinstance(fd, ArrayFieldDiff):
            hist_elem = hist_arr[hist_idx]
            cur_elem = current_arr[cur_idx]
            if isinstance(hist_elem, list) and isinstance(cur_elem, list):
                sub_arr = _remap_array_diff(fd, hist_elem, cur_elem)
                if sub_arr is None:
                    continue
                new_diffs.append(sub_arr)
                new_indices.append(new_id)
                id_remap[eid] = new_id
            else:
                new_diffs.append(fd)
                new_indices.append(new_id)
                id_remap[eid] = new_id
        else:
            assert isinstance(fd, FieldDiff)
            hist_val = hist_arr[hist_idx]
            cur_val = current_arr[cur_idx]
            remapped = _remap_field_diff(fd, cur_has=True, cur_val=cur_val)
            if remapped is None:
                continue
            new_diffs.append(remapped)
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
            assert eid in id_remap
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
        old_order=None, kind=delta.kind,
    )


# ==================== Delta 计算入口 ====================


@profile
def compute_delta(
    base_data: JsonObject,
    mod_data: JsonObject,
    file_type: str,
    root_key: str | None = None,
    merge_mode: MergeMode = MergeMode.NORMAL,
) -> DictFieldDiff | None:
    """计算 mod 相对于游戏本体的实际差异，产出 DiffDict。

    只提取 mod 真正修改的部分，忽略与本体完全相同的内容。
    对 dictionary 类型文件按条目级 + 字段级递归 diff，
    对 entity/config 类型文件按字段级递归 diff。
    """
    field_path = (root_key,) if root_key else None

    if not base_data:
        result = _recursive_delta({}, mod_data, field_path, merge_mode)
        assert result is None or isinstance(result, DictFieldDiff), (
            f"_recursive_delta(dict, dict) 返回了非预期类型: {type(result)}"
        )
        return result

    if file_type == "dictionary":
        items: dict[str, DiffEntry] = {}
        for key, mod_val in mod_data.items():
            if key not in base_data:
                items[key] = FieldDiff(ChangeKind.ADDED, mod_val)
            else:
                sub = _recursive_delta(base_data[key], mod_val, field_path, merge_mode)
                if sub is not None:
                    items[key] = sub
        return DictFieldDiff(items=items, kind=ChangeKind.ORIGIN) if items else None
    else:
        result = _recursive_delta(base_data, mod_data, field_path, merge_mode)
        assert result is None or isinstance(result, DictFieldDiff), (
            f"_recursive_delta(dict, dict) 返回了非预期类型: {type(result)}"
        )
        return result


# ==================== Delta 展平 ====================


def flatten_delta(
    delta: DictFieldDiff,
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
        elif isinstance(diff, DictFieldDiff):
            result.extend(flatten_delta(diff, path))
        elif isinstance(diff, ArrayFieldDiff):
            for elem_diff, elem_id in zip(diff.diffs, diff.indices, strict=True):
                elem_path = path + (f"[{elem_id}]",)
                if isinstance(elem_diff, DictFieldDiff):
                    result.extend(flatten_delta(elem_diff, elem_path))
                elif isinstance(elem_diff, ArrayFieldDiff):
                    result.extend(flatten_delta(DictFieldDiff(items={
                        f"[{eid}]": d for d, eid in zip(elem_diff.diffs, elem_diff.indices)
                    }, kind=ChangeKind.ORIGIN), elem_path))
                else:
                    assert isinstance(elem_diff, FieldDiff)
                    result.append((elem_path, elem_diff))
    return result


# ==================== init() 辅助函数 ====================


def _effective_mode(
    mod_id: str,
    merge_mode: MergeMode,
    mod_merge_modes: dict[str, MergeMode] | None,
) -> MergeMode:
    """获取 mod 的实际合并模式（per-mod 覆盖 > 全局默认）。"""
    if mod_merge_modes and mod_id in mod_merge_modes:
        return mod_merge_modes[mod_id]
    return merge_mode


@profile
def _process_file_group(
    rel_path: str,
    file_mod_ids: list[str],
    store: JsonStore,
    schemas: dict[str, JsonObject],
    merge_mode: MergeMode,
    mod_merge_modes: dict[str, MergeMode] | None,
    adaptive_base_dirs: dict[str, Path],
    apply_delta: Callable[..., DictFieldDiff],
) -> list[tuple[str, str, DictFieldDiff | None]]:
    """处理单个文件的所有 mod delta 计算。

    按 mod 优先级顺序处理，维护 REPLACE 模式所需的累积合并状态。
    返回 [(mod_id, rel_path, delta), ...] 结果列表。
    """
    from ..platform.history import resolve_path

    base_data = store.get_base(rel_path)
    file_type = classify_json(base_data) if base_data else "config"
    schema = resolve_schema(rel_path, schemas) if schemas else None
    root_key = get_schema_root_key(schema) if schema else None

    current: DictFieldDiff | None = None
    results: list[tuple[str, str, DictFieldDiff | None]] = []

    for mod_id in file_mod_ids:
        effective = _effective_mode(mod_id, merge_mode, mod_merge_modes)
        mod_data = store.get_mod(mod_id, rel_path)

        if effective == MergeMode.REPLACE:
            if current is None:
                current = DictFieldDiff.from_dict(base_data)
            cumulative_data = current.to_dict()
            delta = compute_delta(
                cumulative_data, mod_data, file_type,
                root_key=root_key,
            )
        elif effective == MergeMode.ADAPTIVE:
            adaptive_base = base_data
            hist_base_used: JsonObject | None = None
            hist_dir = adaptive_base_dirs.get(mod_id)
            if hist_dir is not None:
                hist_file = resolve_path(hist_dir, rel_path)
                if hist_file is not None:
                    hist_base_used = store._load_json(hist_file)
                    adaptive_base = hist_base_used
            delta = compute_delta(
                adaptive_base, mod_data, file_type,
                root_key=root_key,
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
            delta = compute_delta(
                base_data, mod_data, file_type,
                root_key=root_key,
                merge_mode=effective,
            )

        results.append((mod_id, rel_path, delta))

        if delta is not None:
            if current is None:
                current = DictFieldDiff.from_dict(base_data)
            fp: tuple[str, ...] | None = (root_key,) if root_key else None
            apply_delta(current, delta, fp)

    return results


# ==================== 全局 Delta 缓存 ====================


class ModDelta:
    """全局 Delta 缓存管理器（纯静态类）。

    启动时调用 init() 预计算所有 delta，后续通过 get() 直接取缓存结果。
    """

    # (mod_id, rel_path) → DiffDict | None
    _cache: dict[tuple[str, str], DictFieldDiff | None] = {}
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
        from .merger import apply_dict_delta

        store = JsonStore.instance()
        schemas = load_schemas(schema_dir) if schema_dir else {}

        # ADAPTIVE 模式：解析历史版本目录
        set_resolver(PathResolver(history_dir) if history_dir else None)
        history_versions = parse_history_versions(history_dir) if history_dir else []

        # ADAPTIVE 前置校验
        adaptive_mods = [
            m for m in mod_ids
            if _effective_mode(m, merge_mode, mod_merge_modes) == MergeMode.ADAPTIVE
        ]
        if adaptive_mods:
            assert history_dir is not None, "ADAPTIVE 模式要求提供 history_dir"
            assert mod_update_times is not None, "ADAPTIVE 模式要求提供 mod_update_times"

        # 预计算每个 mod 对应的历史基准目录（仅存入有匹配的 mod）
        adaptive_base_dirs: dict[str, Path] = {}
        if history_versions and mod_update_times:
            for mod_id in mod_ids:
                ut = mod_update_times[mod_id]
                base_ver = find_base_version(ut, history_versions)
                if base_ver is not None:
                    adaptive_base_dirs[mod_id] = base_ver

        # 按文件分组，保持 mod 优先级顺序
        tasks_by_file: dict[str, list[str]] = defaultdict(list)
        for mod_id in mod_ids:
            for rel_path in store.mod_files(mod_id):
                tasks_by_file[rel_path].append(mod_id)

        total = sum(len(mids) for mids in tasks_by_file.values())
        completed = 0
        with cls._lock:
            cls._cache.clear()
            cls._progress = (0, total)
        if progress_cb:
            progress_cb(0, total)

        file_groups = list(tasks_by_file.items())
        with ThreadPoolExecutor() as pool:
            futures = {
                pool.submit(
                    _process_file_group,
                    rel_path, file_mod_ids, store, schemas,
                    merge_mode, mod_merge_modes, adaptive_base_dirs,
                    apply_dict_delta,
                ): rel_path
                for rel_path, file_mod_ids in file_groups
            }
            for future in as_completed(futures):
                for mod_id, rel_path, delta in future.result():
                    with cls._lock:
                        cls._cache[(mod_id, rel_path)] = delta
                        completed += 1
                        cls._progress = (completed, total)
                    if progress_cb:
                        progress_cb(completed, total)

    @classmethod
    def get(cls, mod_id: str, rel_path: str) -> DictFieldDiff | None:
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
