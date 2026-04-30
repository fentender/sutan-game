"""
用户覆盖目录管理工具
"""
import shutil
from pathlib import Path


def invalidate_stale_overrides(
    overrides_dir: Path,
    old_enabled_ids: list[str],
    new_enabled_ids: list[str],
) -> list[str]:
    """
    Mod 排序或启用状态变化时，删除失效的 override 目录。

    从两个有序 mod_id 列表中找到第一个差异位置，差异点及之后的所有 mod
    都视为失效（其覆盖结果建立在已变化的合并链之上）。

    返回被删除的 mod_id 列表（顺序不保证）。
    """
    if not overrides_dir.exists():
        return []

    # 找到第一个不同的位置
    min_len = min(len(old_enabled_ids), len(new_enabled_ids))
    diverge = min_len
    for i in range(min_len):
        if old_enabled_ids[i] != new_enabled_ids[i]:
            diverge = i
            break

    # 收集受影响的 mod ID（变化点及之后的所有 mod）
    stale_ids = set(old_enabled_ids[diverge:]) | set(new_enabled_ids[diverge:])
    if not stale_ids:
        return []

    deleted = []
    for mod_id in stale_ids:
        override_dir = overrides_dir / mod_id
        if override_dir.exists():
            shutil.rmtree(override_dir)
            deleted.append(mod_id)
    return deleted
