"""
本体重叠检测 - 判断 Mod 是否修改了游戏本体已有的内容

利用 JsonStore 已缓存的 JSON 数据，无需额外文件加载。
"""
from .json_store import JsonStore
from .type_utils import classify_json


def compute_base_overlap(store: JsonStore, mod_id: str) -> bool:
    """检测 mod 是否修改了本体已有内容。

    返回 True 表示有重叠（高风险），False 表示纯增量（低风险）。

    判定逻辑：
    - 遍历 mod 的所有 config 文件
    - 若本体无同名文件 → 纯新增，跳过
    - 若本体有同名文件：
      - dictionary 类型：比较 key 集合是否有交集
      - entity/config 类型：文件存在于本体即视为修改
    """
    for rel_path in store.mod_files(mod_id):
        if not store.has_base(rel_path):
            continue

        base_data = store.get_base(rel_path)
        mod_data = store.get_mod(mod_id, rel_path)

        file_type = classify_json(base_data)
        if file_type == "dictionary":
            # 字典型：检查 key 交集
            if set(mod_data.keys()) & set(base_data.keys()):
                return True
        else:
            # entity/config：本体有同名文件即视为修改
            return True

    return False


def compute_all_overlaps(store: JsonStore, mod_ids: list[str]) -> dict[str, bool]:
    """批量计算所有 mod 的重叠状态。"""
    return {mod_id: compute_base_overlap(store, mod_id) for mod_id in mod_ids}
