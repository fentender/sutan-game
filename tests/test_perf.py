"""
性能测试 - 不启动 GUI，集成 profiler 输出热点报告
"""
import copy
import logging
import time

from src.config import SCHEMA_DIR, UserConfig
from src.core import profiler
from tests.test_runner import TestResult, assert_true, run_test, skip

log = logging.getLogger("test")


def _get_real_paths():
    """获取真实游戏路径，不存在则返回 None"""
    config = UserConfig.load()
    game_config = config.game_config_path
    if not game_config.exists():
        return None, None, None
    return game_config, config.workshop_dir, config.enabled_mods


def _require_real_data():
    """检查是否有真实数据可用"""
    game_config, workshop, _ = _get_real_paths()
    if not game_config or not workshop or not workshop.exists():
        skip("真实游戏/workshop 数据不可用")
    return game_config, workshop


def _init_store_and_delta(game_config, workshop):
    """初始化 JsonStore 和 ModDelta，返回 (mod_configs, mod_ids)。"""
    from src.core.delta_store import ModDelta
    from src.core.json_store import JsonStore
    from src.core.mod_scanner import scan_all_mods

    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]
    store = JsonStore.instance()
    store.init(game_config, mod_configs)
    mod_ids = [m.mod_id for m in mods]
    ModDelta.init(mod_ids, schema_dir=SCHEMA_DIR)
    return mod_configs, mod_ids


# ==================== 性能测试 ====================

def perf_load_schemas():
    """Schema 加载性能"""
    if not SCHEMA_DIR.exists() or not any(SCHEMA_DIR.glob("*.schema.json")):
        skip("schema 目录为空")
    from src.core.schema_loader import load_schemas
    start = time.perf_counter()
    schemas = load_schemas(SCHEMA_DIR)
    elapsed = time.perf_counter() - start
    log.info("    加载 %d 个 schema，耗时 %.3fs", len(schemas), elapsed)
    assert_true(elapsed < 10, f"加载 schema 超时: {elapsed:.3f}s")


def perf_scan_mods():
    """Mod 扫描性能"""
    _, workshop = _require_real_data()
    from src.core.mod_scanner import scan_all_mods
    start = time.perf_counter()
    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    elapsed = time.perf_counter() - start
    log.info("    扫描 %d 个 Mod，耗时 %.3fs", len(mods), elapsed)
    assert_true(elapsed < 10, f"扫描 Mod 超时: {elapsed:.3f}s")


def perf_analyze_all():
    """完整冲突分析性能"""
    game_config, workshop = _require_real_data()
    from src.core.conflict import analyze_all_overrides

    mod_configs, _ = _init_store_and_delta(game_config, workshop)

    start = time.perf_counter()
    overrides = analyze_all_overrides(mod_configs,
                                       schema_dir=SCHEMA_DIR)
    elapsed = time.perf_counter() - start
    log.info("    分析 %d 个文件，耗时 %.3fs", len(overrides), elapsed)


def perf_apply_delta_large():
    """大对象递归合并性能"""
    from src.core.delta_store import compute_delta
    from src.core.merger import apply_delta
    from src.core.types import DiffDict

    # 构造 1000 key 的嵌套字典
    base = {f"key_{i}": {"sub_a": i, "sub_b": f"value_{i}",
                          "nested": {"x": i * 2, "y": i * 3}}
            for i in range(1000)}
    override = dict(base)
    for i in range(0, 1000, 2):
        override[f"key_{i}"] = {**base[f"key_{i}"], "sub_a": i * 10}

    delta = compute_delta(base, override, "config")
    assert_true(delta is not None, "应有变化")

    start = time.perf_counter()
    for _ in range(10):
        apply_delta(DiffDict.from_dict(base), delta)
    elapsed = time.perf_counter() - start
    log.info("    1000-key 字典合并 ×10，耗时 %.3fs", elapsed)
    assert_true(elapsed < 30, f"大对象合并超时: {elapsed:.3f}s")


def perf_resolve_duplicates():
    """数组相似度匹配性能"""
    from src.core.array_match import resolve_duplicates

    base = [{"id": f"item_{i}", "name": f"Name {i}", "value": i,
             "desc": f"Description for item {i} with some extra text"}
            for i in range(100)]
    mod_items = [(i, {"id": f"item_{i}", "name": f"Name {i} Modified",
                       "value": i * 2})
                 for i in range(50)]

    start = time.perf_counter()
    pairs, unmatched = resolve_duplicates(mod_items, base, list(range(100)))
    elapsed = time.perf_counter() - start
    log.info("    100 元素数组匹配 50 个 mod 项，耗时 %.3fs", elapsed)
    assert_true(len(pairs) == 50, f"应匹配 50 对，实际 {len(pairs)}")


def perf_diff_dialog_tab_load():
    """DiffDialog 打开 + 首次 tab 切换性能（无头 Qt）"""
    import os
    game_config, workshop = _require_real_data()
    from src.core.conflict import analyze_all_overrides

    mod_configs, _ = _init_store_and_delta(game_config, workshop)

    # 挑选最坏情况：被最多 mod 同时修改、且字段 override 数最多的文件
    overrides = analyze_all_overrides(mod_configs, schema_dir=SCHEMA_DIR)
    candidates = [o for o in overrides if len(o.mod_chain) >= 2]
    if not candidates:
        skip("没有多 mod 同时修改的文件")
    candidates.sort(key=lambda o: (len(o.mod_chain), len(o.field_overrides)), reverse=True)
    target = candidates[0]
    log.info("    目标文件 %s (%d mods, %d field overrides)",
             target.rel_path, len(target.mod_chain), len(target.field_overrides))

    # 对应的 mod_configs 子集（按原顺序保留修改过此文件的 mod）
    target_mods = [
        (mid, mname, path) for (mid, mname, path) in mod_configs
        if mname in target.mod_chain
    ]

    # 无头 Qt
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        skip("PySide6 不可用")
    app = QApplication.instance() or QApplication([])

    from src.gui.diff_dialog import DiffDialog

    start = time.perf_counter()
    dialog = DiffDialog(target.rel_path, target_mods)
    construct_elapsed = time.perf_counter() - start
    log.info("    DiffDialog 构造（含 precompute + tab 0 加载）%.3fs", construct_elapsed)

    tab_count = len(dialog._diff_pairs)
    if tab_count <= 1:
        log.info("    仅 1 个 tab，无后续切换")
        dialog.deleteLater()
        app.processEvents()
        return

    tab_times = []
    for i in range(1, tab_count):
        t0 = time.perf_counter()
        dialog._load_tab(i)
        tab_times.append(time.perf_counter() - t0)
    total_tab = sum(tab_times)
    avg_tab = total_tab / len(tab_times)
    log.info("    首次加载 %d 个后续 tab：总 %.3fs / 平均 %.3fs / 最慢 %.3fs",
             len(tab_times), total_tab, avg_tab, max(tab_times))

    dialog.deleteLater()
    app.processEvents()


def perf_merge_all():
    """完整合并流程性能"""
    import tempfile
    game_config, workshop = _require_real_data()
    from src.core.merger import merge_all_files

    mod_configs, _ = _init_store_and_delta(game_config, workshop)

    with tempfile.TemporaryDirectory() as tmpdir:
        from pathlib import Path
        output = Path(tmpdir) / "config"
        output.mkdir()

        start = time.perf_counter()
        results = merge_all_files(
            mod_configs, output, schema_dir=SCHEMA_DIR,
        )
        elapsed = time.perf_counter() - start
        log.info("    合并 %d 个文件，耗时 %.3fs", len(results), elapsed)


def perf_json_parse():
    """JSON 解析性能（含逗号修复）"""
    from src.core.json_store import JsonStore

    game_config, workshop = _require_real_data()

    # 收集所有 mod JSON 文件
    json_files = []
    for mod_dir in workshop.iterdir():
        if not mod_dir.is_dir():
            continue
        config_dir = mod_dir / "config"
        if config_dir.exists():
            json_files.extend(config_dir.rglob("*.json"))

    if not json_files:
        skip("没有可用的 JSON 文件")

    # 也加入本体的 JSON 文件
    base_files = list(game_config.rglob("*.json"))
    all_files = base_files + json_files

    start = time.perf_counter()
    repair_count = 0
    for f in all_files:
        try:
            JsonStore.parse_file(f)
        except Exception:
            repair_count += 1
    elapsed = time.perf_counter() - start
    log.info("    解析 %d 个 JSON（%d 本体 + %d mod），耗时 %.3fs",
             len(all_files), len(base_files), len(json_files), elapsed)


def perf_full_pipeline_profile():
    """完整合并管线 profile：scan → analyze → merge，输出全函数耗时"""
    import tempfile
    game_config, workshop = _require_real_data()
    from src.core.conflict import analyze_all_overrides
    from src.core.merger import merge_all_files

    mod_configs, _ = _init_store_and_delta(game_config, workshop)
    log.info("    使用 %d 个 Mod 进行完整管线 profile", len(mod_configs))

    # 阶段 1：冲突分析
    start = time.perf_counter()
    overrides = analyze_all_overrides(mod_configs,
                                         schema_dir=SCHEMA_DIR)
    analyze_elapsed = time.perf_counter() - start
    log.info("    冲突分析: %.3fs (%d 个文件)", analyze_elapsed, len(overrides))

    # 阶段 2：完整合并
    with tempfile.TemporaryDirectory() as tmpdir:
        from pathlib import Path
        output = Path(tmpdir) / "config"
        output.mkdir()

        start = time.perf_counter()
        results = merge_all_files(
            mod_configs, output, schema_dir=SCHEMA_DIR,
        )
        merge_elapsed = time.perf_counter() - start
        log.info("    完整合并: %.3fs (%d 个文件)", merge_elapsed, len(results))

    log.info("    管线总耗时: %.3fs (分析 + 合并)", analyze_elapsed + merge_elapsed)


def perf_delta_init():
    """ModDelta.init() 性能（含并行计算）"""
    game_config, workshop = _require_real_data()
    from src.core.delta_store import ModDelta
    from src.core.json_store import JsonStore
    from src.core.mod_scanner import scan_all_mods

    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]
    store = JsonStore.instance()
    store.init(game_config, mod_configs)
    mod_ids = [m.mod_id for m in mods]

    ModDelta.clear()
    start = time.perf_counter()
    ModDelta.init(mod_ids, schema_dir=SCHEMA_DIR)
    elapsed = time.perf_counter() - start
    completed, total = ModDelta.progress()
    log.info("    ModDelta.init: %d 个 delta，耗时 %.3fs", total, elapsed)
    ModDelta.clear()


def perf_delta_cache_hit():
    """ModDelta 缓存命中性能"""
    game_config, workshop = _require_real_data()
    from src.core.delta_store import ModDelta
    from src.core.json_store import JsonStore
    from src.core.mod_scanner import scan_all_mods

    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]
    store = JsonStore.instance()
    store.init(game_config, mod_configs)
    mod_ids = [m.mod_id for m in mods]
    ModDelta.init(mod_ids, schema_dir=SCHEMA_DIR)

    # 收集所有缓存 key
    keys: list[tuple[str, str]] = []
    for mod_id in mod_ids:
        for rel_path in store.mod_files(mod_id):
            keys.append((mod_id, rel_path))
    if not keys:
        skip("没有可用的缓存 key")

    iterations = 1000
    start = time.perf_counter()
    for _ in range(iterations):
        for mod_id, rel_path in keys:
            ModDelta.get(mod_id, rel_path)
    elapsed = time.perf_counter() - start
    total_gets = iterations * len(keys)
    log.info("    缓存命中 %d 次，耗时 %.3fs（%.1f ns/次）",
             total_gets, elapsed, elapsed / total_gets * 1e9)
    ModDelta.clear()


def perf_id_remap_conflict():
    """ID 重分配线性查找性能"""
    from src.core.id_remapper import _next_available_id

    # 构造 10000 个连续已占用 ID
    used = {str(i) for i in range(10000)}

    start = time.perf_counter()
    for _ in range(1000):
        _next_available_id(0, used)
    elapsed = time.perf_counter() - start
    log.info("    _next_available_id ×1000（10000 已占用），耗时 %.3fs", elapsed)
    assert_true(elapsed < 1, f"ID 线性查找超时: {elapsed:.3f}s")


def perf_merge_cache_compute():
    """MergeCache 首次计算 vs 缓存命中"""
    game_config, workshop = _require_real_data()
    from src.core.conflict import analyze_all_overrides
    from src.core.merge_cache import MergeCache

    mod_configs, _ = _init_store_and_delta(game_config, workshop)

    overrides = analyze_all_overrides(mod_configs, schema_dir=SCHEMA_DIR)
    candidates = [o for o in overrides if len(o.mod_chain) >= 2]
    if not candidates:
        skip("没有多 mod 同时修改的文件")
    candidates.sort(key=lambda o: len(o.field_overrides), reverse=True)
    target = candidates[0]
    log.info("    目标文件 %s (%d mods, %d field overrides)",
             target.rel_path, len(target.mod_chain), len(target.field_overrides))

    cache = MergeCache.instance()
    cache.invalidate_all()

    # 首次计算
    start = time.perf_counter()
    cache.get(target.rel_path, mod_configs, SCHEMA_DIR)
    first_elapsed = time.perf_counter() - start
    log.info("    首次计算: %.3fs", first_elapsed)

    # 缓存命中
    start = time.perf_counter()
    for _ in range(100):
        cache.get(target.rel_path, mod_configs, SCHEMA_DIR)
    cached_elapsed = time.perf_counter() - start
    log.info("    缓存命中 ×100: %.3fs（%.6fs/次）",
             cached_elapsed, cached_elapsed / 100)
    assert_true(cached_elapsed < first_elapsed,
                f"缓存命中应快于首次计算: {cached_elapsed:.3f}s vs {first_elapsed:.3f}s")


def perf_compute_all_overlaps():
    """全量 Mod 重叠检测性能"""
    game_config, workshop = _require_real_data()
    from src.core.json_store import JsonStore
    from src.core.overlap import compute_all_overlaps

    _, mod_ids = _init_store_and_delta(game_config, workshop)
    store = JsonStore.instance()

    start = time.perf_counter()
    results = compute_all_overlaps(store, mod_ids)
    elapsed = time.perf_counter() - start
    overlap_count = sum(1 for v in results.values() if v)
    log.info("    %d 个 mod 重叠检测，%d 个有重叠，耗时 %.3fs",
             len(results), overlap_count, elapsed)
    assert_true(elapsed < 5, f"重叠检测超时: {elapsed:.3f}s")


def perf_format_delta_json_deep():
    """大型 DiffDict 格式化性能"""
    from src.core.diff_formatter import format_delta_json
    from src.core.types import ArrayFieldDiff, ChangeKind, DiffDict, FieldDiff

    def _build_nested(depth: int, width: int, version: int) -> DiffDict:
        items: dict[str, FieldDiff | DiffDict | ArrayFieldDiff] = {}
        for i in range(width):
            if depth > 0:
                items[f"level{depth}_key{i}"] = _build_nested(depth - 1, width, version)
            else:
                kind = ChangeKind.CHANGED if i % 3 == 0 else ChangeKind.ORIGIN
                items[f"leaf_{i}"] = FieldDiff(
                    kind=kind, value=f"val_{i}", old_value=f"old_{i}",
                    version=version if kind == ChangeKind.CHANGED else 0,
                )
        return DiffDict(items=items)

    dd = _build_nested(depth=3, width=10, version=1)

    start = time.perf_counter()
    for _ in range(100):
        format_delta_json(dd, highlight_version=1)
    elapsed = time.perf_counter() - start
    log.info("    4 层 ×10 DiffDict 格式化 ×100，耗时 %.3fs", elapsed)
    assert_true(elapsed < 5, f"DiffDict 格式化超时: {elapsed:.3f}s")


def perf_smart_allow_deletion():
    """智能删除规则高频调用吞吐"""
    from src.core.smart_rules import smart_allow_deletion

    field_path = ["root", "entries", "0", "condition", "sub1", "sub2",
                  "sub3", "sub4", "sub5", "leaf"]

    start = time.perf_counter()
    for _ in range(100000):
        smart_allow_deletion(field_path, False)
    elapsed = time.perf_counter() - start
    log.info("    smart_allow_deletion ×100000，耗时 %.3fs（%.1f ns/次）",
             elapsed, elapsed / 100000 * 1e9)
    assert_true(elapsed < 0.5, f"smart_allow_deletion 超时: {elapsed:.3f}s")


# ==================== 入口 ====================

def run_all(result: TestResult):
    """运行全部性能测试，自动启用 profiler"""
    profiler.enable()
    profiler.reset()

    tests = [
        ("perf_load_schemas", perf_load_schemas),
        ("perf_json_parse", perf_json_parse),
        ("perf_scan_mods", perf_scan_mods),
        ("perf_analyze_all", perf_analyze_all),
        ("perf_apply_delta_large", perf_apply_delta_large),
        ("perf_resolve_duplicates", perf_resolve_duplicates),
        ("perf_merge_all", perf_merge_all),
        ("perf_delta_init", perf_delta_init),
        ("perf_delta_cache_hit", perf_delta_cache_hit),
        ("perf_diff_dialog_tab_load", perf_diff_dialog_tab_load),
        ("perf_full_pipeline_profile", perf_full_pipeline_profile),
        ("perf_id_remap_conflict", perf_id_remap_conflict),
        ("perf_merge_cache_compute", perf_merge_cache_compute),
        ("perf_compute_all_overlaps", perf_compute_all_overlaps),
        ("perf_format_delta_json_deep", perf_format_delta_json_deep),
        ("perf_smart_allow_deletion", perf_smart_allow_deletion),
    ]
    for name, func in tests:
        run_test(name, func, result)

    # 输出 profiler 报告
    log.info("")
    log.info(profiler.get_report(top_n=30))
    profiler.disable()
