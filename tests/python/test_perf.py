"""
性能测试 - 不启动 GUI，集成 profiler 输出热点报告
"""
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

def perf_data_manager_init():
    """DataManager.init() 完整流程（路径收集 + 批量解析 + 分发）"""
    _, workshop = _require_real_data()
    from src.core.data_manager import DataManager
    from src.core.mod.scanner import scan_all_mods

    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]
    config = UserConfig.load()
    store = DataManager.instance()

    mod_update_times = {m.mod_id: m.update_time for m in mods if m.update_time is not None}
    store.clear()
    start = time.perf_counter()
    store.init(config, mod_configs, mod_update_times=mod_update_times or None)
    elapsed = time.perf_counter() - start

    base_count = len(store.base_rel_paths())
    mod_file_count = sum(len(store.mod_files(m.mod_id)) for m in mods)
    log.info("    DataManager.init: %d base + %d mod 文件，耗时 %.3fs",
             base_count, mod_file_count, elapsed)
    assert_true(elapsed < 30, f"DataManager.init 超时: {elapsed:.3f}s")


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


def perf_delta_init_adaptive():
    """ModDelta.init() ADAPTIVE 模式性能"""
    _, workshop = _require_real_data()
    from src.core.merge.delta import ModDelta
    from src.core.data_manager import DataManager
    from src.core.mod.scanner import scan_all_mods
    from src.core.infra.types import MergeMode

    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]
    config = UserConfig.load()
    store = DataManager.instance()
    mod_update_times = {m.mod_id: m.update_time for m in mods if m.update_time is not None}
    store.init(config, mod_configs, mod_update_times=mod_update_times or None)
    mod_ids = [m.mod_id for m in mods]

    mod_merge_modes: dict[str, MergeMode] = {}
    for k, v in config.mod_merge_modes.items():
        try:
            mod_merge_modes[k] = MergeMode(v)
        except ValueError:
            continue

    ModDelta.clear()
    start = time.perf_counter()
    ModDelta.init(mod_ids, merge_mode=MergeMode.ADAPTIVE,
                  mod_merge_modes=mod_merge_modes or None)
    elapsed = time.perf_counter() - start
    _, total = ModDelta.progress()
    log.info("    ModDelta.init (ADAPTIVE): %d 个 delta，耗时 %.3fs", total, elapsed)
    ModDelta.clear()


def perf_id_remap():
    """ID 冲突检测 + 重分配性能"""
    _, workshop = _require_real_data()
    from src.core.mod.id_remap import remap_mod_configs

    mod_configs, _ = _init_store_and_delta(workshop)

    start = time.perf_counter()
    messages, remap_tables = remap_mod_configs(mod_configs)
    elapsed = time.perf_counter() - start

    remap_mod_count = len(remap_tables)
    log.info("    ID remap: %d 个 mod 需重分配，%d 条消息，耗时 %.3fs",
             remap_mod_count, len(messages), elapsed)


def perf_merge_all_modmanager():
    """ModManager.merge_all_files 性能（缓存路径 + 文件写出）"""
    import tempfile
    from pathlib import Path
    _, workshop = _require_real_data()
    from src.core.mod.scanner import scan_all_mods
    from src.core.data_manager import DataManager
    from src.core.mod_manager import ModManager

    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]
    config = UserConfig.load()
    store = DataManager.instance()
    store.init(config, mod_configs)
    mod_ids = [m.mod_id for m in mods]

    manager = ModManager(config)
    manager.init_delta(mod_ids)

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "config"
        output.mkdir()

        start = time.perf_counter()
        results = manager.merge_all_files(mod_configs, output)
        elapsed = time.perf_counter() - start
        log.info("    ModManager.merge_all_files: %d 个文件，耗时 %.3fs",
                 len(results), elapsed)


def perf_copy_resources():
    """资源文件复制性能"""
    import tempfile
    from pathlib import Path
    _, workshop = _require_real_data()
    from src.core.mod.scanner import scan_all_mods
    from src.core.mod.deployer import copy_resources

    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")

    mod_paths = [(m.name, m.path) for m in mods]

    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir)

        start = time.perf_counter()
        copy_resources(mod_paths, output)
        elapsed = time.perf_counter() - start

        file_count = sum(1 for f in output.rglob("*") if f.is_file())
        log.info("    资源复制: %d 个文件，耗时 %.3fs", file_count, elapsed)


def perf_deploy_workshop():
    """deploy_to_workshop 部署性能"""
    import tempfile
    from pathlib import Path
    _, workshop = _require_real_data()
    from src.core.mod.scanner import scan_all_mods
    from src.core.data_manager import DataManager
    from src.core.mod_manager import ModManager
    from src.core.mod.deployer import deploy_to_workshop

    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]
    config = UserConfig.load()
    store = DataManager.instance()
    store.init(config, mod_configs)
    mod_ids = [m.mod_id for m in mods]

    manager = ModManager(config)
    manager.init_delta(mod_ids)

    with tempfile.TemporaryDirectory() as merge_dir, \
         tempfile.TemporaryDirectory() as fake_workshop:
        merge_output = Path(merge_dir)
        config_out = merge_output / "config"
        config_out.mkdir()
        manager.merge_all_files(mod_configs, config_out)

        mod_names = [m.name for m in mods]
        start = time.perf_counter()
        target = deploy_to_workshop(merge_output, Path(fake_workshop), mod_names)
        elapsed = time.perf_counter() - start

        file_count = sum(1 for f in target.rglob("*") if f.is_file())
        log.info("    deploy_to_workshop: %d 个文件，耗时 %.3fs",
                 file_count, elapsed)


# ==================== 入口 ====================

def run_all(result: TestResult):
    """运行全部性能测试，自动启用 profiler"""
    profiler.enable()
    profiler.reset()

    tests = [
        ("perf_load_schemas", perf_load_schemas),
        ("perf_json_parse", perf_json_parse),
        ("perf_data_manager_init", perf_data_manager_init),
        ("perf_scan_mods", perf_scan_mods),
        ("perf_analyze_all", perf_analyze_all),
        ("perf_apply_delta_real", perf_apply_delta_real),
        ("perf_delta_init", perf_delta_init),
        ("perf_delta_init_adaptive", perf_delta_init_adaptive),
        ("perf_id_remap", perf_id_remap),
        ("perf_diff_dialog_tab_load", perf_diff_dialog_tab_load),
        ("perf_full_pipeline_profile", perf_full_pipeline_profile),
        ("perf_merge_cache_compute", perf_merge_cache_compute),
        ("perf_compute_all_overlaps", perf_compute_all_overlaps),
        ("perf_merge_all_modmanager", perf_merge_all_modmanager),
        ("perf_copy_resources", perf_copy_resources),
        ("perf_deploy_workshop", perf_deploy_workshop),
    ]
    for name, func in tests:
        run_test(name, func, result)

    # 输出 profiler 报告
    log.info("")
    log.info(profiler.get_report(top_n=30))
    profiler.disable()
