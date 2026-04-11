"""
性能测试 - 不启动 GUI，集成 profiler 输出热点报告
"""
import copy
import json
import logging
import time

from tests.test_runner import run_test, assert_true, skip, TestResult
from src.config import UserConfig, SCHEMA_DIR
from src.core import profiler

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
    from src.core.mod_scanner import scan_all_mods, collect_mod_files
    from src.core.conflict import analyze_all_overrides

    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]

    start = time.perf_counter()
    overrides = analyze_all_overrides(game_config, mod_configs,
                                       schema_dir=SCHEMA_DIR)
    elapsed = time.perf_counter() - start
    log.info("    分析 %d 个文件，耗时 %.3fs", len(overrides), elapsed)


def perf_deep_merge_large():
    """大对象递归合并性能"""
    from src.core.merger import deep_merge

    # 构造 1000 key 的嵌套字典
    base = {f"key_{i}": {"sub_a": i, "sub_b": f"value_{i}",
                          "nested": {"x": i * 2, "y": i * 3}}
            for i in range(1000)}
    override = {f"key_{i}": {"sub_a": i * 10}
                for i in range(0, 1000, 2)}  # 修改偶数 key

    start = time.perf_counter()
    for _ in range(10):
        deep_merge(copy.deepcopy(base), override, None, None)
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
    from src.core.mod_scanner import scan_all_mods
    from src.core.conflict import analyze_all_overrides

    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]

    # 挑选最坏情况：被最多 mod 同时修改、且字段 override 数最多的文件
    overrides = analyze_all_overrides(game_config, mod_configs, schema_dir=SCHEMA_DIR)
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
    dialog = DiffDialog(target.rel_path, game_config, target_mods)
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
    from src.core.mod_scanner import scan_all_mods
    from src.core.merger import merge_all_files

    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]

    with tempfile.TemporaryDirectory() as tmpdir:
        from pathlib import Path
        output = Path(tmpdir) / "config"
        output.mkdir()

        start = time.perf_counter()
        results = merge_all_files(
            game_config, mod_configs, output, schema_dir=SCHEMA_DIR,
        )
        elapsed = time.perf_counter() - start
        log.info("    合并 %d 个文件，耗时 %.3fs", len(results), elapsed)


def perf_json_parse():
    """JSON 解析性能（含逗号修复）"""
    from src.core.json_parser import load_json, clear_json_cache

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

    clear_json_cache()

    start = time.perf_counter()
    repair_count = 0
    for f in all_files:
        try:
            load_json(f)
        except Exception:
            repair_count += 1
    elapsed = time.perf_counter() - start
    log.info("    解析 %d 个 JSON（%d 本体 + %d mod），耗时 %.3fs",
             len(all_files), len(base_files), len(json_files), elapsed)


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
        ("perf_deep_merge_large", perf_deep_merge_large),
        ("perf_resolve_duplicates", perf_resolve_duplicates),
        ("perf_merge_all", perf_merge_all),
        ("perf_diff_dialog_tab_load", perf_diff_dialog_tab_load),
    ]
    for name, func in tests:
        run_test(name, func, result)

    # 输出 profiler 报告
    log.info("")
    log.info(profiler.get_report())
    profiler.disable()
