"""Mod 管理层 — 扫描、冲突检测、ID 重映射、部署"""

from .conflict import (
    DeletionRecord as DeletionRecord,
    FileOverrideInfo as FileOverrideInfo,
    analyze_all_overrides as analyze_all_overrides,
)
from .deployer import (
    copy_resources as copy_resources,
    generate_info_json as generate_info_json,
    scan_synthetic_mods as scan_synthetic_mods,
)
from .id_remap import RemapTable as RemapTable, remap_mod_configs as remap_mod_configs
from .overlap import compute_all_overlaps as compute_all_overlaps
from .scanner import ModInfo as ModInfo, scan_all_mods as scan_all_mods
