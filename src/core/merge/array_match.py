"""
数组元素匹配工具 - merger 和 conflict 共享的匹配算法
"""
import json

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from ..infra.profiler import profile
from ..infra.types import ArrayMatching, JsonArray, JsonObject

# 对象数组中常见的标识字段（delta_store 也使用）
COMMON_MATCH_KEYS: tuple[str, ...] = ('guid', 'id', 'tag', 'key')

# 内容字段，用于启发式模糊匹配
_CONTENT_FIELDS: tuple[str, ...] = ('result_text', 'condition', 'action', 'result', 'result_title')


@profile
def item_similarity(a: JsonObject, b: JsonObject) -> float:
    """计算两个 dict 的字符串相似度（0.0 ~ 1.0）"""
    a_str = json.dumps(a, sort_keys=True, ensure_ascii=False)
    b_str = json.dumps(b, sort_keys=True, ensure_ascii=False)
    return fuzz.ratio(a_str, b_str) / 100.0


# ==================== 匹配策略函数 ====================

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
    base_arr: JsonArray,
    mod_arr: JsonArray,
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
                and mod_arr[mi][key] == base_val  # type: ignore[call-overload]
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
    remaining_mod_set = {mi for mi in range(mod_len) if mi not in matched_mod}

    # bi→mi 映射，用于计算合法范围
    pair_map: dict[int, int] = dict(pairs)

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
                if not base_str or len(base_str) < 2:
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
                    if sim > 0.5:
                        fwd_sim.setdefault(bi, []).append((mi, sim))
                        rev_sim.setdefault(mi, []).append((bi, sim))

            while fwd_sim:
                best_pair_sim: tuple[int, int, float] | None = None
                bi_s: int
                candidates_s: list[tuple[int, float]]
                for bi_s, candidates_s in fwd_sim.items():
                    if not candidates_s:
                        continue
                    best_mi_s, best_sim_s = max(candidates_s, key=lambda x: x[1])
                    mi_candidates_s = rev_sim.get(best_mi_s, [])
                    if not mi_candidates_s:
                        continue
                    best_bi_for_mi_s, _ = max(mi_candidates_s, key=lambda x: x[1])
                    if best_bi_for_mi_s == bi_s:
                        if (
                            best_pair_sim is None
                            or best_sim_s > best_pair_sim[2]
                        ):
                            best_pair_sim = (bi_s, best_mi_s, best_sim_s)

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
                for cs in fwd_sim.values():
                    cs[:] = [(m, s) for m, s in cs if m != mi]
                for cs in rev_sim.values():
                    cs[:] = [(b, s) for b, s in cs if b != bi]
                changed = True
                has_fallback = True

        remaining_base = [bi for bi in remaining_base if bi not in matched_base]

    # 非 dict 元素的兜底：阶段 1 跳过了非 dict 元素
    for bi in list(remaining_base):
        if isinstance(base_arr[bi], dict):
            continue
        lo, hi = _get_mod_range(bi, base_len, mod_len, pair_map)
        best_mi_scalar: int | None = None
        best_sim_scalar = -1.0
        for mi in remaining_mod_set:
            if mi < lo or mi >= hi:
                continue
            sim = element_similarity(base_arr[bi], mod_arr[mi])
            if sim > best_sim_scalar:
                best_sim_scalar = sim
                best_mi_scalar = mi
        if best_mi_scalar is not None:
            pairs.append((bi, best_mi_scalar))
            matched_mod.add(best_mi_scalar)
            matched_base.add(bi)
            pair_map[bi] = best_mi_scalar
            remaining_mod_set.discard(best_mi_scalar)
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
