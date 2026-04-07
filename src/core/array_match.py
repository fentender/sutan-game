"""
数组元素匹配工具 - merger 和 conflict 共享的匹配算法
"""
import json
from difflib import SequenceMatcher

from .profiler import profile


@profile
def find_matching_item(base_arr: list[dict], mod_item: dict,
                       matched: set[int], match_keys: list[str]) -> int | None:
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
def item_similarity(a: dict, b: dict) -> float:
    """计算两个 dict 的字符串相似度（0.0 ~ 1.0）"""
    a_str = json.dumps(a, sort_keys=True, ensure_ascii=False)
    b_str = json.dumps(b, sort_keys=True, ensure_ascii=False)
    return SequenceMatcher(None, a_str, b_str).ratio()


@profile
def resolve_duplicates(
    mod_items: list[tuple[int, dict]],
    base_arr: list,
    base_indices: list[int],
) -> tuple[list[tuple[int, dict, int]], list[tuple[int, dict]]]:
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

    # 预序列化所有元素，避免重复 json.dumps
    mod_strs = [
        json.dumps(item, sort_keys=True, ensure_ascii=False)
        for _, item in mod_items
    ]
    base_strs = {
        bi: json.dumps(base_arr[bi], sort_keys=True, ensure_ascii=False)
        for bi in base_indices
    }

    # 预计算完整相似度矩阵
    matrix: dict[tuple[int, int], float] = {}
    for mi in range(len(mod_items)):
        for bi_idx, bi in enumerate(base_indices):
            matrix[(mi, bi_idx)] = SequenceMatcher(
                None, mod_strs[mi], base_strs[bi]
            ).ratio()

    remaining_mod = set(range(len(mod_items)))
    remaining_base = set(range(len(base_indices)))
    matched_pairs = []

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


def get_key_vals(item: dict, match_keys: list[str]) -> tuple | None:
    """提取 match_key 值元组，任一 key 缺失则返回 None"""
    vals = tuple(item.get(k) for k in match_keys)
    if any(v is None for v in vals):
        return None
    return vals
