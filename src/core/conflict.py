"""
冲突检测与覆盖链报告
"""
from dataclasses import dataclass, field
from pathlib import Path

from .delta_store import ModDelta, flatten_delta
from .json_store import JsonStore
from .profiler import profile
from .schema_loader import get_schema_root_key, load_schemas, resolve_schema
from .types import FIELD_SEP, CancelCheck, ChangeKind


@dataclass
class DeletionRecord:
    """单个字段的删除记录"""
    field_path: str       # 被删除的字段路径（用 FIELD_SEP 分隔）
    base_value: object    # 被删除的原始值
    mod_name: str         # 执行删除的 Mod 名称


@dataclass
class FieldOverride:
    """单个字段的覆盖信息"""
    field_path: str
    base_value: object = None
    # [(mod_name, value), ...]
    mod_values: list[tuple[str, object]] = field(default_factory=list)
    final_value: object = None
    is_array_touched: bool = False  # 该字段路径属于被多 mod 触碰的数组

    @property
    def is_conflict(self) -> bool:
        """多个 mod 修改了同一字段，且修改值不完全相同"""
        if len(self.mod_values) <= 1:
            return False
        first_val = self.mod_values[0][1]
        return any(v != first_val for _, v in self.mod_values[1:])


@dataclass
class FileOverrideInfo:
    """单个文件的覆盖信息"""
    rel_path: str
    # 参与覆盖的 mod 列表（按优先级顺序）
    mod_chain: list[str] = field(default_factory=list)
    # 字段级覆盖详情
    field_overrides: list[FieldOverride] = field(default_factory=list)
    # 新增条目
    new_entries: list[tuple[str, str]] = field(default_factory=list)  # (mod_name, description)
    # 删除记录
    deletions: list[DeletionRecord] = field(default_factory=list)
    # 被多 mod 修改的数组路径列表
    array_warnings: list[str] = field(default_factory=list)

    @property
    def has_conflict(self) -> bool:
        return any(f.is_conflict for f in self.field_overrides)

    @property
    def has_warning(self) -> bool:
        """有数组级潜在冲突警告"""
        return len(self.array_warnings) > 0

    @property
    def has_conflict_or_warning(self) -> bool:
        return self.has_conflict or self.has_warning


# ==================== 差异收集（复用 merger 的 compute_delta + flatten_delta） ====================


def analyze_file_overrides(
    rel_path: str,
    base_data: dict[str, object],
    mod_data_list: list[tuple[str, str, dict[str, object]]],
    schema: dict[str, object] | None = None,
    field_path: list[str] | None = None,
) -> FileOverrideInfo:
    """
    分析单个文件的覆盖情况。

    参数:
        rel_path: 文件相对路径
        base_data: 游戏本体数据
        mod_data_list: [(mod_id, mod_name, mod_data), ...] 按优先级排序
        schema: 该文件对应的 schema 规则
        field_path: schema 根路径
    """
    info = FileOverrideInfo(rel_path=rel_path)
    info.mod_chain = [name for _, name, _ in mod_data_list]

    # 收集每个 mod 相对于 base 的差异
    field_map: dict[str, FieldOverride] = {}
    # 追踪每个数组路径被哪些 mod 触碰
    array_mod_tracker: dict[str, set[str]] = {}

    for mod_id, mod_name, _ in mod_data_list:
        delta = ModDelta.get(mod_id, rel_path)
        if delta is None:
            continue

        flat = flatten_delta(delta)
        for path_tuple, field_diff in flat:
            fp = FIELD_SEP.join(path_tuple)

            # 追踪数组级触碰：路径中含 "[" 表示这是数组元素
            for i, seg in enumerate(path_tuple):
                if seg.startswith("["):
                    arr_path = FIELD_SEP.join(path_tuple[:i])
                    if arr_path not in array_mod_tracker:
                        array_mod_tracker[arr_path] = set()
                    array_mod_tracker[arr_path].add(mod_name)
                    break

            if field_diff.kind.base_kind == ChangeKind.ADDED:
                info.new_entries.append((mod_name, f"新增: {fp}"))
                continue

            if field_diff.kind.base_kind == ChangeKind.DELETED:
                info.new_entries.append((mod_name, f"删除: {fp}"))
                info.deletions.append(DeletionRecord(
                    field_path=fp, base_value=field_diff.value, mod_name=mod_name,
                ))
                continue

            # CHANGED
            if fp not in field_map:
                field_map[fp] = FieldOverride(field_path=fp)
            field_map[fp].mod_values.append((mod_name, field_diff.value))

    # 最终值 = 最后一个 mod 的值
    for fo in field_map.values():
        fo.final_value = fo.mod_values[-1][1] if fo.mod_values else fo.base_value

    info.field_overrides = list(field_map.values())

    # 标记被多 mod 触碰的数组
    warned_arrays = {path for path, mods in array_mod_tracker.items() if len(mods) >= 2}
    info.array_warnings = sorted(warned_arrays)
    if warned_arrays:
        for fo in info.field_overrides:
            for arr_path in warned_arrays:
                if fo.field_path.startswith(arr_path + FIELD_SEP) or fo.field_path == arr_path:
                    fo.is_array_touched = True
                    break

    return info


@profile
def analyze_all_overrides(
    mod_configs: list[tuple[str, str, Path]],
    schema_dir: Path | None = None,
    cancel_check: CancelCheck | None = None,
) -> list[FileOverrideInfo]:
    """
    分析所有文件的覆盖情况。

    参数:
        mod_configs: [(mod_id, mod_name, mod_config_path), ...] 按优先级排序
        schema_dir: schema 规则文件目录
        cancel_check: 可选的取消检查回调，调用时若已取消则抛出异常
    返回:
        FileOverrideInfo 列表
    """
    store = JsonStore.instance()
    schemas = load_schemas(schema_dir) if schema_dir else {}
    mod_ids = [mod_id for mod_id, _, _ in mod_configs]

    results: list[FileOverrideInfo] = []

    for rel_path in sorted(store.all_rel_paths()):
        if cancel_check:
            cancel_check()

        base_data = store.get_base(rel_path)

        mod_data_list: list[tuple[str, str, dict[str, object]]] = []
        for mod_id in mod_ids:
            if store.has_mod(mod_id, rel_path):
                mod_data_list.append((
                    mod_id, store.mod_name(mod_id),
                    store.get_mod(mod_id, rel_path),
                ))

        if not mod_data_list:
            continue

        # 查找 schema
        schema = resolve_schema(rel_path, schemas) if schemas else None
        root_key = get_schema_root_key(schema) if schema else None
        schema_path = [root_key] if root_key else None

        info = analyze_file_overrides(rel_path, base_data, mod_data_list,
                                       schema=schema, field_path=schema_path)
        results.append(info)

    return results
