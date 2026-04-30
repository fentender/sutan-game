"""基础设施层 — 类型定义、诊断信息、性能监测"""

from .diagnostics import (
    ERROR as ERROR,
    INFO as INFO,
    WARNING as WARNING,
    Diagnostics as Diagnostics,
    MergeContext as MergeContext,
    diag as diag,
    merge_ctx as merge_ctx,
)
from .profiler import profile as profile, profile_block as profile_block
from .types import (
    FIELD_SEP as FIELD_SEP,
    ArrayFieldDiff as ArrayFieldDiff,
    ArrayMatching as ArrayMatching,
    CancelCheck as CancelCheck,
    ChangeKind as ChangeKind,
    DiffDict as DiffDict,
    DupList as DupList,
    FieldDiff as FieldDiff,
    JsonArray as JsonArray,
    JsonObject as JsonObject,
    JsonPrimitive as JsonPrimitive,
    JsonValue as JsonValue,
    MergeMode as MergeMode,
    ParseFailure as ParseFailure,
    ProgressCallback as ProgressCallback,
    normalize_rel_path as normalize_rel_path,
)
