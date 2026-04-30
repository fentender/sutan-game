"""合并引擎层 — 差异计算、合并算法、缓存、格式化"""

from .cache import MergeCache as MergeCache
from .delta import (
    ModDelta as ModDelta,
    compute_delta as compute_delta,
    flatten_delta as flatten_delta,
)
from .formatter import format_delta_json as format_delta_json
from .merger import (
    apply_delta as apply_delta,
    copy_failed_files as copy_failed_files,
    merge_all_files as merge_all_files,
    merge_file as merge_file,
)
