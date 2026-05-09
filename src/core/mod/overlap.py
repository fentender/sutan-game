"""
本体重叠检测 - 判断 Mod 是否修改了游戏本体已有的内容

利用 DataManager 已缓存的 JSON 数据，无需额外文件加载。
"""
from sultan_core.json_ops import classify_json, extract_root_keys

from ..data_manager import DataManager


def compute_base_overlap(dm: DataManager, mod_id: str) -> bool:
    """检测 mod 是否修改了本体已有内容。

    返回 True 表示有重叠（高风险），False 表示纯增量（低风险）。
    """
    for rel_path in dm.mod_files(mod_id):
        if not dm.has_base(rel_path):
            continue

        base_doc = dm.get_base(rel_path)
        file_type = classify_json(base_doc)
        if file_type == "dictionary":
            base_keys = set(extract_root_keys(base_doc))
            mod_keys = set(extract_root_keys(dm.get_mod(mod_id, rel_path)))
            if mod_keys & base_keys:
                return True
        else:
            return True

    return False


def compute_all_overlaps(dm: DataManager, mod_ids: list[str]) -> dict[str, bool]:
    """批量计算所有 mod 的重叠状态。"""
    return {mod_id: compute_base_overlap(dm, mod_id) for mod_id in mod_ids}
