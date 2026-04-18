"""
数组元素匹配工具 - merger 和 conflict 共享的匹配算法
"""
import json

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from .profiler import profile
from .types import ArrayMatching

# 对象数组中常见的标识字段（delta_store 也使用）
COMMON_MATCH_KEYS: tuple[str, ...] = ('guid', 'id', 'tag', 'key')

# 内容字段，用于启发式模糊匹配
_CONTENT_FIELDS: tuple[str, ...] = ('condition', 'action', 'result', 'result_text')


@profile
def find_matching_item(
    base_arr: list[dict[str, object]],
    mod_item: dict[str, object],
    matched: set[int],
    match_keys: list[str],
) -> int | None:
    """
    按 match_keys 中所有字段精确匹配，全部相等才算匹配到。
    返回 base_arr 中第一个匹配元素的索引，或 None。
    （用于一对一场景，如 conflict 差异对比）
    """
    for i, base_item in enumerate(base_arr):
        if i in matched:
            continue
        if all(
            key in mod_item and key in base_item
            and mod_item[key] == base_item[key]
            for key in match_keys
        ):
            return i
    return None


@profile
def item_similarity(a: dict[str, object], b: dict[str, object]) -> float:
    """计算两个 dict 的字符串相似度（0.0 ~ 1.0）"""
    a_str = json.dumps(a, sort_keys=True, ensure_ascii=False)
    b_str = json.dumps(b, sort_keys=True, ensure_ascii=False)
    return fuzz.ratio(a_str, b_str) / 100.0


@profile
def resolve_duplicates(
    mod_items: list[tuple[int, dict[str, object]]],
    base_arr: list[object],
    base_indices: list[int],
) -> tuple[list[tuple[int, dict[str, object], int]], list[tuple[int, dict[str, object]]]]:
    """
    多对多相似度匹配：mod 侧和 base 侧各有多个同 key 元素。
    贪心策略：每次从所有 mod×base 配对中选相似度最高的一对，
    双方移出待匹配池，重复直到 base 候选耗尽。

    参数:
        mod_items: [(mod 在 mod_arr 中的原始索引, mod_item), ...]
        base_arr: result 数组的引用
        base_indices: base 中候选元素的索引列表

    返回:
        matched_pairs: [(mod_orig_idx, mod_item, base_idx), ...]
        unmatched_mod: [(mod_orig_idx, mod_item), ...] — 未匹配的 mod 元素（新增）
    """
    if not base_indices:
        return [], list(mod_items)

    # 短路 1：1×1 直接配对，无需相似度计算
    if len(mod_items) == 1 and len(base_indices) == 1:
        mod_orig_idx, mod_item = mod_items[0]
        return [(mod_orig_idx, mod_item, base_indices[0])], []

    # 预序列化所有元素，避免重复 json.dumps
    mod_strs = [
        json.dumps(item, sort_keys=True, ensure_ascii=False)
        for _, item in mod_items
    ]
    base_strs = {
        bi: json.dumps(base_arr[bi], sort_keys=True, ensure_ascii=False)
        for bi in base_indices
    }

    # 短路 2：单个 mod 元素，对每个 base 候选取相似度最大者，跳过矩阵+贪心循环
    if len(mod_items) == 1:
        mod_str = mod_strs[0]
        best_bi = base_indices[0]
        best_ratio = -1.0
        for bi in base_indices:
            base_str = base_strs[bi]
            if base_str == mod_str:
                best_bi = bi
                break
            ratio = fuzz.ratio(mod_str, base_str) / 100.0
            if ratio > best_ratio:
                best_ratio = ratio
                best_bi = bi
        mod_orig_idx, mod_item = mod_items[0]
        return [(mod_orig_idx, mod_item, best_bi)], []

    # 预计算完整相似度矩阵；完全相等直接给 1.0 跳过 SequenceMatcher
    matrix: dict[tuple[int, int], float] = {}
    for mi in range(len(mod_items)):
        ms = mod_strs[mi]
        for bi_idx, bi in enumerate(base_indices):
            bs = base_strs[bi]
            if ms == bs:
                matrix[(mi, bi_idx)] = 1.0
            else:
                matrix[(mi, bi_idx)] = fuzz.ratio(ms, bs) / 100.0

    remaining_mod = set(range(len(mod_items)))
    remaining_base = set(range(len(base_indices)))
    matched_pairs: list[tuple[int, dict[str, object], int]] = []

    while remaining_base and remaining_mod:
        best_ratio = -1.0
        best_mi = 0
        best_bi = 0
        for mi in remaining_mod:
            for bi in remaining_base:
                ratio = matrix[(mi, bi)]
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_mi = mi
                    best_bi = bi
        remaining_mod.discard(best_mi)
        remaining_base.discard(best_bi)
        mod_orig_idx, mod_item = mod_items[best_mi]
        matched_pairs.append((mod_orig_idx, mod_item, base_indices[best_bi]))

    unmatched = [(mod_items[mi][0], mod_items[mi][1]) for mi in sorted(remaining_mod)]
    return matched_pairs, unmatched


def get_key_vals(item: dict[str, object], match_keys: list[str]) -> tuple[object, ...] | None:
    """提取 match_key 值元组，任一 key 缺失则返回 None"""
    vals = tuple(item.get(k) for k in match_keys)
    if any(v is None for v in vals):
        return None
    return vals


def is_obj_array(arr: object) -> bool:
    """判断是否是非空对象数组"""
    return isinstance(arr, list) and bool(arr) and all(isinstance(x, dict) for x in arr)


# ==================== 匹配策略函数 ====================


@profile
def match_by_keys(
    base_arr: list[dict[str, object]],
    mod_arr: list[dict[str, object]],
    match_keys: list[str],
) -> ArrayMatching:
    """按 match_keys 分组 + resolve_duplicates 配对。

    无 key 的 mod 元素视为 unmatched_mod；
    base 中有 key 但 mod 中无对应 key 的视为 unmatched_base。
    """
    # base 按 key 分组，记录原始 0-based 索引
    base_groups: dict[tuple[object, ...], list[int]] = {}
    for i, item in enumerate(base_arr):
        kv = get_key_vals(item, match_keys)
        if kv is not None:
            base_groups.setdefault(kv, []).append(i)

    # mod 按 key 分组
    mod_groups: dict[tuple[object, ...], list[int]] = {}
    mod_no_key: list[int] = []
    for i, item in enumerate(mod_arr):
        kv = get_key_vals(item, match_keys)
        if kv is not None:
            mod_groups.setdefault(kv, []).append(i)
        else:
            mod_no_key.append(i)

    pairs: list[tuple[int, int]] = []
    unmatched_mod: list[int] = []
    unmatched_base: list[int] = []
    seen_keys: set[tuple[object, ...]] = set()

    for kv, mod_indices in mod_groups.items():
        seen_keys.add(kv)
        base_indices = base_groups.get(kv, [])

        if not base_indices:
            # 全部新增
            unmatched_mod.extend(mod_indices)
            continue

        # 利用 resolve_duplicates 做多对多配对
        mod_items: list[tuple[int, dict[str, object]]] = [
            (mi, mod_arr[mi]) for mi in mod_indices
        ]
        base_objs: list[object] = [base_arr[bi] for bi in base_indices]
        base_idx_list = list(range(len(base_indices)))
        matched, unmatched = resolve_duplicates(mod_items, base_objs, base_idx_list)

        for mod_orig_idx, _, local_idx in matched:
            pairs.append((base_indices[local_idx], mod_orig_idx))

        for mod_orig_idx, _ in unmatched:
            unmatched_mod.append(mod_orig_idx)

    # 无 key 的 mod 元素直接作为新增
    unmatched_mod.extend(mod_no_key)

    # base 中未出现在 seen_keys 中的 = 删除
    for kv, base_indices in base_groups.items():
        if kv not in seen_keys:
            unmatched_base.extend(base_indices)

    return ArrayMatching(pairs=pairs, unmatched_mod=unmatched_mod, unmatched_base=unmatched_base)


def match_by_index(
    base_arr: list[object],
    mod_arr: list[object],
) -> ArrayMatching:
    """按位置索引一一对应。"""
    min_len = min(len(base_arr), len(mod_arr))
    pairs = [(i, i) for i in range(min_len)]
    unmatched_mod = list(range(min_len, len(mod_arr)))
    unmatched_base = list(range(min_len, len(base_arr)))
    return ArrayMatching(pairs=pairs, unmatched_mod=unmatched_mod, unmatched_base=unmatched_base)


def match_by_consume(
    base_arr: list[object],
    mod_arr: list[object],
) -> ArrayMatching:
    """消耗式相等匹配。"""
    pairs: list[tuple[int, int]] = []
    unmatched_mod: list[int] = []
    remaining = list(range(len(base_arr)))  # 0-based

    for mi, item in enumerate(mod_arr):
        found = False
        for ri, bi in enumerate(remaining):
            if base_arr[bi] == item:
                pairs.append((bi, mi))
                remaining.pop(ri)
                found = True
                break
        if not found:
            unmatched_mod.append(mi)

    return ArrayMatching(pairs=pairs, unmatched_mod=unmatched_mod, unmatched_base=remaining)


def _to_string(val: object) -> str:
    """将字段值转为字符串，用于模糊匹配。"""
    if isinstance(val, (dict, list)):
        return json.dumps(val, sort_keys=True, ensure_ascii=False)
    return str(val) if val is not None else ""


def element_similarity(a: object, b: object) -> float:
    """计算两个元素的相似度（0.0 ~ 1.0）。
    标量直接比较，dict 使用序列化字符串相似度。
    """
    if a == b:
        return 1.0
    if type(a) is not type(b):
        return 0.0
    if isinstance(a, (str, int, float, bool)):
        return 0.0
    if isinstance(a, dict):
        # TODO: 实现更精细的 Object 相似度比较
        return item_similarity(a, b)  # type: ignore[arg-type]
    return 0.0


def _get_mod_range(
    bi: int,
    base_len: int,
    mod_len: int,
    pair_map_bi_to_mi: dict[int, int],
) -> tuple[int, int]:
    """计算 base[bi] 在阶段 2/3 中合法的 mod 搜索范围 [lo, hi)。

    根据已有匹配对的顺序约束：
    - lo = bi 之前最近已匹配 base 元素对应的 mi + 1（无则 0）
    - hi = bi 之后最近已匹配 base 元素对应的 mi（无则 mod_len）
    """
    lo = 0
    for prev_bi in range(bi - 1, -1, -1):
        if prev_bi in pair_map_bi_to_mi:
            lo = pair_map_bi_to_mi[prev_bi] + 1
            break
    hi = mod_len
    for next_bi in range(bi + 1, base_len):
        if next_bi in pair_map_bi_to_mi:
            hi = pair_map_bi_to_mi[next_bi]
            break
    return lo, hi


def match_by_heuristic(
    base_arr: list[object],
    mod_arr: list[object],
) -> ArrayMatching:
    """启发式数组匹配（分阶段全局匹配）。

    假设 mod 作者不会调整原有元素之间的相对顺序，只会修改、删除或插入新元素。

    分四个阶段：
    1. COMMON_MATCH_KEYS 精确匹配（滑动窗口，未匹配不推进窗口）
    2. 内容字段模糊匹配（全局双向最优分配）
    3. 兜底相似度匹配（全局双向最优分配）
    4. 未配对元素位置对应
    """
    base_len = len(base_arr)
    mod_len = len(mod_arr)

    if base_len == 0:
        return ArrayMatching(
            pairs=[],
            unmatched_mod=list(range(mod_len)),
            unmatched_base=[],
        )
    if mod_len == 0:
        return ArrayMatching(
            pairs=[],
            unmatched_mod=[],
            unmatched_base=list(range(base_len)),
        )

    pairs: list[tuple[int, int]] = []
    matched_mod: set[int] = set()
    matched_base: set[int] = set()
    has_fallback = False

    # ── 阶段 1：COMMON_MATCH_KEYS 精确匹配 ──
    s_start = 0

    for bi in range(base_len):
        base_elem = base_arr[bi]

        if not isinstance(base_elem, dict):
            continue

        s = [mi for mi in range(s_start, mod_len) if mi not in matched_mod]
        if not s:
            continue

        matched_in_step1 = False

        for key in COMMON_MATCH_KEYS:
            if key not in base_elem:
                continue
            base_val = base_elem[key]
            hits = [
                mi for mi in s
                if isinstance(mod_arr[mi], dict)
                and key in mod_arr[mi]  # type: ignore[operator]
                and mod_arr[mi][key] == base_val  # type: ignore[index]
            ]
            if len(hits) == 1:
                pairs.append((bi, hits[0]))
                matched_mod.add(hits[0])
                matched_base.add(bi)
                s_start = hits[0] + 1
                matched_in_step1 = True
                break
            if len(hits) > 1:
                s = hits

        if matched_in_step1:
            continue
        # 未匹配：不推进 s_start，留给阶段 2

    # ── 阶段 2：内容字段模糊匹配（全局双向最优分配）──
    remaining_base = [bi for bi in range(base_len) if bi not in matched_base]
    remaining_mod_set = set(mi for mi in range(mod_len) if mi not in matched_mod)

    # bi→mi 映射，用于计算合法范围
    pair_map: dict[int, int] = {bi: mi for bi, mi in pairs}

    for field in _CONTENT_FIELDS:
        changed = True
        while changed:
            changed = False
            # 构建双向候选矩阵
            fwd: dict[int, list[tuple[int, int]]] = {}  # bi → [(mi, dist)]
            rev: dict[int, list[tuple[int, int]]] = {}  # mi → [(bi, dist)]

            for bi in remaining_base:
                if bi in matched_base:
                    continue
                base_elem = base_arr[bi]
                if not isinstance(base_elem, dict) or field not in base_elem:
                    continue
                base_str = _to_string(base_elem[field])
                if not base_str:
                    continue

                lo, hi = _get_mod_range(bi, base_len, mod_len, pair_map)

                for mi in remaining_mod_set:
                    if mi < lo or mi >= hi:
                        continue
                    mod_elem = mod_arr[mi]
                    if not isinstance(mod_elem, dict) or field not in mod_elem:
                        continue
                    mod_str = _to_string(mod_elem[field])
                    if not mod_str:
                        continue
                    dist = Levenshtein.distance(base_str, mod_str)
                    max_len = max(len(base_str), len(mod_str))
                    if dist < max_len * 0.33:
                        fwd.setdefault(bi, []).append((mi, dist))
                        rev.setdefault(mi, []).append((bi, dist))

            # 贪心双向最优分配
            while fwd:
                best_pair: tuple[int, int, int] | None = None  # (bi, mi, dist)
                for bi, candidates in fwd.items():
                    if not candidates:
                        continue
                    # bi 的最优 mi
                    best_mi, best_dist = min(candidates, key=lambda x: x[1])
                    # 检查 mi 的最优 bi 是否也是当前 bi
                    mi_candidates = rev.get(best_mi, [])
                    if not mi_candidates:
                        continue
                    best_bi_for_mi, _ = min(
                        mi_candidates, key=lambda x: x[1]
                    )
                    if best_bi_for_mi == bi:
                        if (
                            best_pair is None
                            or best_dist < best_pair[2]
                        ):
                            best_pair = (bi, best_mi, best_dist)

                if best_pair is None:
                    break

                bi, mi, _ = best_pair
                pairs.append((bi, mi))
                matched_mod.add(mi)
                matched_base.add(bi)
                pair_map[bi] = mi
                remaining_mod_set.discard(mi)
                # 从候选矩阵中清除
                fwd.pop(bi, None)
                rev.pop(mi, None)
                for candidates in fwd.values():
                    candidates[:] = [(m, d) for m, d in candidates if m != mi]
                for candidates in rev.values():
                    candidates[:] = [(b, d) for b, d in candidates if b != bi]
                changed = True

    remaining_base = [bi for bi in remaining_base if bi not in matched_base]

    # ── 阶段 3：兜底相似度匹配 ──
    if remaining_base and remaining_mod_set:
        changed = True
        while changed:
            changed = False
            fwd_sim: dict[int, list[tuple[int, float]]] = {}
            rev_sim: dict[int, list[tuple[int, float]]] = {}

            for bi in remaining_base:
                if bi in matched_base:
                    continue
                lo, hi = _get_mod_range(bi, base_len, mod_len, pair_map)
                for mi in remaining_mod_set:
                    if mi < lo or mi >= hi:
                        continue
                    sim = element_similarity(base_arr[bi], mod_arr[mi])
                    if sim > 0.3:
                        fwd_sim.setdefault(bi, []).append((mi, sim))
                        rev_sim.setdefault(mi, []).append((bi, sim))

            while fwd_sim:
                best_pair_sim: tuple[int, int, float] | None = None
                for bi, candidates in fwd_sim.items():
                    if not candidates:
                        continue
                    best_mi, best_sim = max(candidates, key=lambda x: x[1])
                    mi_candidates = rev_sim.get(best_mi, [])
                    if not mi_candidates:
                        continue
                    best_bi_for_mi, _ = max(mi_candidates, key=lambda x: x[1])
                    if best_bi_for_mi == bi:
                        if (
                            best_pair_sim is None
                            or best_sim > best_pair_sim[2]
                        ):
                            best_pair_sim = (bi, best_mi, best_sim)

                if best_pair_sim is None:
                    break

                bi, mi, _ = best_pair_sim
                pairs.append((bi, mi))
                matched_mod.add(mi)
                matched_base.add(bi)
                pair_map[bi] = mi
                remaining_mod_set.discard(mi)
                fwd_sim.pop(bi, None)
                rev_sim.pop(mi, None)
                for candidates in fwd_sim.values():
                    candidates[:] = [(m, s) for m, s in candidates if m != mi]
                for candidates in rev_sim.values():
                    candidates[:] = [(b, s) for b, s in candidates if b != bi]
                changed = True
                has_fallback = True

        remaining_base = [bi for bi in remaining_base if bi not in matched_base]

    # 非 dict 元素的兜底：阶段 1 跳过了非 dict 元素
    for bi in list(remaining_base):
        if isinstance(base_arr[bi], dict):
            continue
        lo, hi = _get_mod_range(bi, base_len, mod_len, pair_map)
        best_mi: int | None = None
        best_sim = -1.0
        for mi in remaining_mod_set:
            if mi < lo or mi >= hi:
                continue
            sim = element_similarity(base_arr[bi], mod_arr[mi])
            if sim > best_sim:
                best_sim = sim
                best_mi = mi
        if best_mi is not None:
            pairs.append((bi, best_mi))
            matched_mod.add(best_mi)
            matched_base.add(bi)
            pair_map[bi] = best_mi
            remaining_mod_set.discard(best_mi)
            has_fallback = True

    unmatched_base = [bi for bi in range(base_len) if bi not in matched_base]
    unmatched_mod = [mi for mi in range(mod_len) if mi not in matched_mod]

    # ── 阶段 4：未配对元素按位置间隙对应 ──
    # 将 unmatched_base 和 unmatched_mod 按它们在已匹配对之间的"间隙"分组，
    # 同一间隙内按顺序一一配对（处理"原地替换"场景）。
    if unmatched_base and unmatched_mod:
        # 构建已匹配对的 bi→mi 排序列表，作为间隙分界线
        sorted_pairs = sorted(pair_map.items(), key=lambda x: x[0])
        # 分界点：(-1, -1) 和 (base_len, mod_len) 作为哨兵
        boundaries: list[tuple[int, int]] = [(-1, -1)]
        boundaries.extend(sorted_pairs)
        boundaries.append((base_len, mod_len))

        ub_set = set(unmatched_base)
        um_set = set(unmatched_mod)
        new_pairs: list[tuple[int, int]] = []

        for k in range(len(boundaries) - 1):
            bi_lo, mi_lo = boundaries[k]
            bi_hi, mi_hi = boundaries[k + 1]
            # 该间隙内的 unmatched 元素
            slot_ub = [b for b in unmatched_base if bi_lo < b < bi_hi]
            slot_um = [m for m in unmatched_mod if mi_lo < m < mi_hi]
            pair_count = min(len(slot_ub), len(slot_um))
            for i in range(pair_count):
                b, m = slot_ub[i], slot_um[i]
                # dict 元素需要满足相似度门槛，标量元素直接配对
                if isinstance(base_arr[b], dict) and isinstance(mod_arr[m], dict):
                    sim = element_similarity(base_arr[b], mod_arr[m])
                    if sim < 0.5:
                        continue
                new_pairs.append((b, m))
                ub_set.discard(b)
                um_set.discard(m)

        pairs.extend(new_pairs)
        unmatched_base = sorted(ub_set)
        unmatched_mod = sorted(um_set)
        if new_pairs:
            has_fallback = True

    confidence = 0.3 if has_fallback else 1.0

    return ArrayMatching(
        pairs=pairs,
        unmatched_mod=unmatched_mod,
        unmatched_base=unmatched_base,
        confidence=confidence,
    )
