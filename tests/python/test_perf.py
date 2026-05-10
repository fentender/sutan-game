"""
性能测试 - 不启动 GUI，集成 profiler 输出热点报告
"""
import copy
import logging
import time

from src.config import SCHEMA_DIR, UserConfig
from src.core.infra import profiler
from tests.python.test_runner import TestResult, assert_true, run_test, skip

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


def _init_store_and_delta(workshop):
    """初始化 DataManager 和 ModDelta，返回 (mod_configs, mod_ids)。"""
    from src.core.merge.delta import ModDelta
    from src.core.data_manager import DataManager
    from src.core.mod.scanner import scan_all_mods

    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]
    config = UserConfig.load()
    store = DataManager.instance()
    store.init(config, mod_configs)
    mod_ids = [m.mod_id for m in mods]
    ModDelta.init(mod_ids)
    return mod_configs, mod_ids


# ==================== 性能测试 ====================

def perf_load_schemas():
    """Schema 加载性能"""
    if not SCHEMA_DIR.exists() or not any(SCHEMA_DIR.glob("*.schema.json")):
        skip("schema 目录为空")
    from src.core.schema.loader import load_schemas
    start = time.perf_counter()
    schemas = load_schemas(SCHEMA_DIR)
    elapsed = time.perf_counter() - start
    log.info("    加载 %d 个 schema，耗时 %.3fs", len(schemas), elapsed)
    assert_true(elapsed < 10, f"加载 schema 超时: {elapsed:.3f}s")


def perf_scan_mods():
    """Mod 扫描性能"""
    _, workshop = _require_real_data()
    from src.core.mod.scanner import scan_all_mods
    start = time.perf_counter()
    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    elapsed = time.perf_counter() - start
    log.info("    扫描 %d 个 Mod，耗时 %.3fs", len(mods), elapsed)
    assert_true(elapsed < 10, f"扫描 Mod 超时: {elapsed:.3f}s")


def perf_analyze_all():
    """完整冲突分析性能"""
    _, workshop = _require_real_data()
    from src.core.mod.conflict import analyze_all_overrides

    mod_configs, _ = _init_store_and_delta(workshop)

    start = time.perf_counter()
    overrides = analyze_all_overrides(mod_configs,
                                       schema_dir=SCHEMA_DIR)
    elapsed = time.perf_counter() - start
    log.info("    分析 %d 个文件，耗时 %.3fs", len(overrides), elapsed)


def perf_apply_delta_large():
    """大对象递归合并性能（C++ State/Delta）"""
    import json as _json
    from sultan_core.json import JsonDoc
    from sultan_core.state import JsonState
    from sultan_core.delta import compute_delta, apply_delta

    base = {f"key_{i}": {"sub_a": i, "sub_b": f"value_{i}",
                          "nested": {"x": i * 2, "y": i * 3}}
            for i in range(1000)}
    override = dict(base)
    for i in range(0, 1000, 2):
        override[f"key_{i}"] = {**base[f"key_{i}"], "sub_a": i * 10}

    base_doc = JsonDoc.parse(_json.dumps(base))
    mod_doc = JsonDoc.parse(_json.dumps(override))

    state = JsonState.from_doc(base_doc)
    delta = compute_delta(base_doc, mod_doc)
    changed = delta is not None
    if changed:
        apply_delta(delta, state, version=1)
    assert_true(changed, "应有变化")

    start = time.perf_counter()
    for _ in range(10):
        s = JsonState.from_doc(base_doc)
        d = compute_delta(base_doc, mod_doc)
        if d is not None:
            apply_delta(d, s, version=1)
    elapsed = time.perf_counter() - start
    log.info("    1000-key 字典合并 ×10，耗时 %.3fs", elapsed)
    assert_true(elapsed < 30, f"大对象合并超时: {elapsed:.3f}s")


def perf_apply_delta_real():
    """真实 Mod 数据 apply_delta 性能（C++ API）"""
    _, workshop = _require_real_data()
    from src.core.merge.delta import ModDelta
    from src.core.data_manager import DataManager
    from sultan_core.state import JsonState
    from sultan_core.delta import apply_delta

    _, mod_ids = _init_store_and_delta(workshop)
    store = DataManager.instance()

    tasks: list[tuple[str, str]] = []
    for mod_id in mod_ids:
        for rel_path in store.mod_files(mod_id):
            if ModDelta.get(mod_id, rel_path) is not None:
                tasks.append((mod_id, rel_path))

    if not tasks:
        skip("没有可用的 delta 数据")

    start = time.perf_counter()
    for mod_id, rel_path in tasks:
        base_doc = store.get_base(rel_path)
        state = JsonState.from_doc(base_doc)
        delta = ModDelta.get(mod_id, rel_path)
        apply_delta(delta, state, version=1)
    elapsed = time.perf_counter() - start

    avg = elapsed / len(tasks) * 1000
    log.info("    真实数据 apply_delta (C++) ×%d，总耗时 %.3fs，平均 %.3fms/次",
             len(tasks), elapsed, avg)


def perf_diff_dialog_tab_load():
    """DiffDialog 打开 + 首次 tab 切换性能（无头 Qt）"""
    import os
    _, workshop = _require_real_data()
    from src.core.mod.conflict import analyze_all_overrides

    mod_configs, _ = _init_store_and_delta(workshop)

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

    from src.gui.dialogs.diff import DiffDialog
    from src.core.mod_manager import ModManager
    from src.core.data_manager import DataManager

    class _PerfService:
        def __init__(self) -> None:
            self._mm = ModManager(UserConfig.load())
        def get_merge_state(self, rel_path: str,
                            mod_configs: list, need_steps: bool = True):
            return self._mm.get_merge_state(rel_path, mod_configs, need_steps=need_steps)
        def get_base(self, rel_path: str):
            return DataManager.instance().get_base(rel_path)

    service = _PerfService()  # type: ignore[arg-type]

    start = time.perf_counter()
    dialog = DiffDialog(target.rel_path, target_mods, service=service)
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
    _, workshop = _require_real_data()
    from src.core.merge.merger import merge_all_files

    mod_configs, _ = _init_store_and_delta(workshop)

    with tempfile.TemporaryDirectory() as tmpdir:
        from pathlib import Path
        output = Path(tmpdir) / "config"
        output.mkdir()

        start = time.perf_counter()
        results = merge_all_files(
            mod_configs, output,
        )
        elapsed = time.perf_counter() - start
        log.info("    合并 %d 个文件，耗时 %.3fs", len(results), elapsed)


def perf_json_parse():
    """JSON 解析性能（含逗号修复）"""
    from sultan_core.json import JsonDoc

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
    paths = [str(f) for f in all_files]
    handle = JsonDoc.batch_parse_files(paths)
    handle.wait()
    fail_count = sum(1 for i in range(handle.total()) if handle.error(i))
    elapsed = time.perf_counter() - start
    log.info("    批量解析 %d 个 JSON（%d 本体 + %d mod），耗时 %.3fs，%d 失败",
             len(all_files), len(base_files), len(json_files), elapsed, fail_count)


def perf_full_pipeline_profile():
    """完整合并管线 profile：scan → analyze → merge，输出全函数耗时"""
    import tempfile
    _, workshop = _require_real_data()
    from src.core.mod.conflict import analyze_all_overrides
    from src.core.merge.merger import merge_all_files

    mod_configs, _ = _init_store_and_delta(workshop)
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
            mod_configs, output,
        )
        merge_elapsed = time.perf_counter() - start
        log.info("    完整合并: %.3fs (%d 个文件)", merge_elapsed, len(results))

    log.info("    管线总耗时: %.3fs (分析 + 合并)", analyze_elapsed + merge_elapsed)


def perf_delta_init():
    """ModDelta.init() 性能（含并行计算）"""
    _, workshop = _require_real_data()
    from src.core.merge.delta import ModDelta
    from src.core.data_manager import DataManager
    from src.core.mod.scanner import scan_all_mods

    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]
    config = UserConfig.load()
    store = DataManager.instance()
    store.init(config, mod_configs)
    mod_ids = [m.mod_id for m in mods]

    ModDelta.clear()
    start = time.perf_counter()
    ModDelta.init(mod_ids)
    elapsed = time.perf_counter() - start
    completed, total = ModDelta.progress()
    log.info("    ModDelta.init: %d 个 delta，耗时 %.3fs", total, elapsed)
    ModDelta.clear()


def perf_delta_cache_hit():
    """ModDelta 缓存命中性能"""
    _, workshop = _require_real_data()
    from src.core.merge.delta import ModDelta
    from src.core.data_manager import DataManager
    from src.core.mod.scanner import scan_all_mods

    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]
    config = UserConfig.load()
    store = DataManager.instance()
    store.init(config, mod_configs)
    mod_ids = [m.mod_id for m in mods]
    ModDelta.init(mod_ids)

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
    from src.core.mod.id_remap import _next_available_id

    # 构造 10000 个连续已占用 ID
    used = {str(i) for i in range(10000)}

    start = time.perf_counter()
    for _ in range(1000):
        _next_available_id(0, used)
    elapsed = time.perf_counter() - start
    log.info("    _next_available_id ×1000（10000 已占用），耗时 %.3fs", elapsed)
    assert_true(elapsed < 1, f"ID 线性查找超时: {elapsed:.3f}s")


def perf_merge_cache_compute():
    """ModManager 合并缓存首次计算 vs 缓存命中"""
    _, workshop = _require_real_data()
    from src.core.mod.conflict import analyze_all_overrides
    from src.core.mod_manager import ModManager

    mod_configs, _ = _init_store_and_delta(workshop)

    overrides = analyze_all_overrides(mod_configs)
    candidates = [o for o in overrides if len(o.mod_chain) >= 2]
    if not candidates:
        skip("没有多 mod 同时修改的文件")
    candidates.sort(key=lambda o: len(o.field_overrides), reverse=True)
    target = candidates[0]
    log.info("    目标文件 %s (%d mods, %d field overrides)",
             target.rel_path, len(target.mod_chain), len(target.field_overrides))

    manager = ModManager(UserConfig.load())
    manager.invalidate_merge_cache()

    start = time.perf_counter()
    manager.get_merge_state(target.rel_path, mod_configs)
    first_elapsed = time.perf_counter() - start
    log.info("    首次计算: %.3fs", first_elapsed)

    start = time.perf_counter()
    for _ in range(100):
        manager.get_merge_state(target.rel_path, mod_configs)
    cached_elapsed = time.perf_counter() - start
    log.info("    缓存命中 ×100: %.3fs（%.6fs/次）",
             cached_elapsed, cached_elapsed / 100)
    assert_true(cached_elapsed < first_elapsed,
                f"缓存命中应快于首次计算: {cached_elapsed:.3f}s vs {first_elapsed:.3f}s")


def perf_compute_all_overlaps():
    """全量 Mod 重叠检测性能"""
    _, workshop = _require_real_data()
    from src.core.data_manager import DataManager
    from src.core.mod.overlap import compute_all_overlaps

    _, mod_ids = _init_store_and_delta(workshop)
    store = DataManager.instance()

    start = time.perf_counter()
    results = compute_all_overlaps(store, mod_ids)
    elapsed = time.perf_counter() - start
    overlap_count = sum(1 for v in results.values() if v)
    log.info("    %d 个 mod 重叠检测，%d 个有重叠，耗时 %.3fs",
             len(results), overlap_count, elapsed)
    assert_true(elapsed < 5, f"重叠检测超时: {elapsed:.3f}s")


def perf_format_delta_json_deep():
    """大型 JsonState 格式化性能（C++ state.format）"""
    import json as _json
    from sultan_core.json import JsonDoc
    from sultan_core.state import JsonState
    from sultan_core.delta import compute_delta, apply_delta

    def _build_nested(prefix: str, depth: int, width: int) -> dict:
        if depth == 0:
            return {f"{prefix}_leaf_{i}": f"val_{i}" for i in range(width)}
        return {
            f"{prefix}_l{depth}_k{i}": _build_nested(
                f"{prefix}_l{depth}_k{i}", depth - 1, width)
            for i in range(width)
        }

    base = _build_nested("r", depth=3, width=10)
    override = copy.deepcopy(base)

    def _modify(d: dict, c: list[int]) -> None:
        for k, v in d.items():
            if isinstance(v, dict):
                _modify(v, c)
            else:
                c[0] += 1
                if c[0] % 3 == 0:
                    d[k] = f"mod_{c[0]}"

    _modify(override, [0])

    base_doc = JsonDoc.parse(_json.dumps(base))
    mod_doc = JsonDoc.parse(_json.dumps(override))
    state = JsonState.from_doc(base_doc)
    delta = compute_delta(base_doc, mod_doc)
    if delta is not None:
        apply_delta(delta, state, version=1)

    start = time.perf_counter()
    for _ in range(100):
        state.format(1)
    elapsed = time.perf_counter() - start
    log.info("    4 层 ×10 JsonState.format ×100，耗时 %.3fs", elapsed)
    assert_true(elapsed < 5, f"JsonState.format 超时: {elapsed:.3f}s")


def perf_smart_allow_deletion():
    """SMART 模式 compute_delta 吞吐（含内部智能删除判断）"""
    import json as _json
    from sultan_core.json import JsonDoc
    from sultan_core.state import MergeMode
    from sultan_core.delta import compute_delta

    base = {"entries": [
        {"id": i, "condition": {"sub1": {"sub2": {"sub3": f"val_{i}"}}}}
        for i in range(100)
    ]}
    mod = {"entries": [e for e in base["entries"] if e["id"] % 2 != 0]}

    base_doc = JsonDoc.parse(_json.dumps(base))
    mod_doc = JsonDoc.parse(_json.dumps(mod))

    start = time.perf_counter()
    for _ in range(1000):
        compute_delta(base_doc, mod_doc, MergeMode.SMART)
    elapsed = time.perf_counter() - start
    log.info("    compute_delta SMART (100-entry array) ×1000，耗时 %.3fs（%.1f us/次）",
             elapsed, elapsed / 1000 * 1e6)
    assert_true(elapsed < 5, f"compute_delta SMART 超时: {elapsed:.3f}s")


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
        ("perf_apply_delta_real", perf_apply_delta_real),
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
