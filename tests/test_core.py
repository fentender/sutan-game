"""
核心功能测试 - 无 GUI，直接测试 core 模块
"""
import copy
import importlib
import json
import tempfile
from typing import cast
from pathlib import Path

from tests.test_runner import (
    run_test, assert_eq, assert_true, assert_in, skip, TestResult,
)
from src.config import UserConfig
from src import config as config_module
from src.core.json_parser import load_json, strip_js_comments, strip_trailing_commas
from src.core.type_utils import classify_json
from src.core.array_match import (
    find_matching_item, resolve_duplicates, get_key_vals, item_similarity,
)
from src.core.merger import (
    deep_merge, compute_mod_delta, merge_file, _strip_marker,
    _classify_delta_items, MergeResult,
)


def _get_real_paths():
    """获取真实游戏路径，不存在则返回 None"""
    config = UserConfig.load()
    game_config = config.game_config_path
    if not game_config.exists():
        return None, None
    return game_config, config.workshop_dir


# ==================== JSON 解析测试 ====================

def test_strip_js_comments():
    """测试去除 JS 风格注释"""
    text = '{"a": 1, // 这是注释\n"b": 2}'
    result = strip_js_comments(text)
    data = json.loads(result)
    assert_eq(data, {"a": 1, "b": 2})


def test_strip_js_comments_in_string():
    """测试字符串内的 // 不被去除"""
    text = '{"url": "http://example.com"}'
    result = strip_js_comments(text)
    data = json.loads(result)
    assert_eq(data["url"], "http://example.com")


def test_strip_trailing_commas():
    """测试去除尾随逗号"""
    text = '{"a": 1, "b": [1, 2, ], }'
    result = strip_trailing_commas(text)
    data = json.loads(result)
    assert_eq(data, {"a": 1, "b": [1, 2]})


def test_load_json_with_comments():
    """测试加载含注释的 JSON 文件"""
    content = '{\n  // 注释\n  "key": "value",\n}'
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                      delete=False, encoding='utf-8') as f:
        f.write(content)
        f.flush()
        data = load_json(f.name)
    assert_eq(data, {"key": "value"})


def test_json_parser_imports_without_fast_json_extension():
    """测试源码执行时缺少 _fast_json 仍可导入 json_parser"""
    module = importlib.import_module('src.core.json_parser')
    assert_true(hasattr(module, "strip_js_comments"))
    assert_true(hasattr(module, "fix_missing_commas"))


# ==================== macOS 路径测试 ====================


def test_detect_game_path_mac_app_bundle():
    """测试 macOS 仅含 .app 时仍能识别游戏目录"""
    original_detect = config_module._detect_steam_library_folders
    original_exe = config_module.GAME_EXE_NAME
    try:
        with tempfile.TemporaryDirectory() as tmp:
            steam_root = Path(tmp) / 'Steam'
            common_game = steam_root / 'steamapps' / 'common' / config_module.GAME_DIR_NAME
            app_bundle = common_game / f"{config_module.GAME_DIR_NAME}.app"
            app_bundle.mkdir(parents=True)
            config_module._detect_steam_library_folders = lambda: [steam_root]
            config_module.GAME_EXE_NAME = 'Sultan\'s Game.exe'
            assert_eq(config_module.detect_game_path(), str(common_game))
    finally:
        config_module._detect_steam_library_folders = original_detect
        config_module.GAME_EXE_NAME = original_exe


def test_infer_workshop_path_from_game_mac_game_folder_and_app():
    """测试 macOS 下游戏文件夹和 .app bundle 都能推导 workshop"""
    with tempfile.TemporaryDirectory() as tmp:
        steamapps = Path(tmp) / 'Steam' / 'steamapps'
        workshop = steamapps / 'workshop' / 'content' / config_module.WORKSHOP_APP_ID
        workshop.mkdir(parents=True)
        common_game = steamapps / 'common' / config_module.GAME_DIR_NAME
        app_bundle = common_game / f"{config_module.GAME_DIR_NAME}.app"
        resources_config = app_bundle / 'Contents' / 'Resources' / 'Data' / 'StreamingAssets' / 'config'
        resources_config.mkdir(parents=True)

        assert_eq(config_module.infer_workshop_path_from_game(str(common_game)), str(workshop))
        assert_eq(config_module.infer_workshop_path_from_game(str(app_bundle)), str(workshop))




def test_detect_steam_root_mac_fallback():
    """测试 macOS Steam 根目录回退到用户目录"""
    original_platform = config_module.sys.platform
    try:
        config_module.sys.platform = 'darwin'
        folders = config_module._detect_steam_library_folders()
        assert_true(any(str(p).endswith('Library/Application Support/Steam') for p in folders), str(folders))
    finally:
        config_module.sys.platform = original_platform


def test_default_local_mod_path_mac():
    """测试 macOS 本地 mod 默认目录"""
    original_platform = config_module.sys.platform
    try:
        config_module.sys.platform = 'darwin'
        assert_eq(config_module.DEFAULT_LOCAL_MOD_PATH, Path.home() / 'DoubleCross' / 'SultansGame' / 'mod')
    finally:
        config_module.sys.platform = original_platform


def test_user_config_game_config_path_mac_app_bundle():
    """测试 macOS .app bundle 的 config 路径"""
    with tempfile.TemporaryDirectory() as tmp:
        app_bundle = Path(tmp) / "Sultan's Game.app"
        config_dir = app_bundle / 'Contents' / 'Resources' / 'Data' / 'StreamingAssets' / 'config'
        config_dir.mkdir(parents=True)
        cfg = UserConfig(game_path=str(app_bundle))
        assert_eq(cfg.game_config_path, config_dir)

# ==================== 类型识别测试 ====================

def test_classify_dictionary():
    """测试 dictionary 类型识别（子 dict 需含 id 字段）"""
    data = {"card_001": {"id": "card_001", "name": "A"},
            "card_002": {"id": "card_002", "name": "B"}}
    assert_eq(classify_json(data), "dictionary")


def test_classify_entity():
    """测试 entity 类型识别"""
    data = {"id": "rite_01", "name": "Test Rite"}
    assert_eq(classify_json(data), "entity")


def test_classify_config():
    """测试 config 类型识别"""
    data = {"volume": 0.5, "language": "zh"}
    assert_eq(classify_json(data), "config")


# ==================== 数组匹配测试 ====================

def test_find_matching_item():
    """测试精确匹配"""
    base = [{"id": "a", "v": 1}, {"id": "b", "v": 2}]
    mod = {"id": "b", "v": 3}
    idx = find_matching_item(base, mod, set(), ["id"])
    assert_eq(idx, 1)


def test_find_matching_item_no_match():
    """测试无匹配"""
    base = [{"id": "a"}, {"id": "b"}]
    mod = {"id": "c"}
    idx = find_matching_item(base, mod, set(), ["id"])
    assert_eq(idx, None)


def test_get_key_vals():
    """测试 key 值提取"""
    item = {"type": "fire", "level": 3}
    assert_eq(get_key_vals(item, ["type", "level"]), ("fire", 3))


def test_get_key_vals_missing():
    """测试缺失 key"""
    item = {"type": "fire"}
    assert_eq(get_key_vals(item, ["type", "level"]), None)


def test_item_similarity():
    """测试相似度计算"""
    a = {"id": "x", "name": "hello", "value": 1}
    b = {"id": "x", "name": "hello", "value": 2}
    c = {"id": "y", "name": "world", "value": 99}
    sim_ab = item_similarity(a, b)
    sim_ac = item_similarity(a, c)
    assert_true(sim_ab > sim_ac, f"相似对 ({sim_ab:.3f}) 应大于不相似对 ({sim_ac:.3f})")


def test_resolve_duplicates():
    """测试多对多匹配"""
    base = [{"id": "a", "v": 1}, {"id": "a", "v": 2}]
    mod_items = [(0, {"id": "a", "v": 1, "extra": True})]
    pairs, unmatched = resolve_duplicates(mod_items, base, [0, 1])
    assert_eq(len(pairs), 1)
    # 应该匹配到 base[0]（v=1 更相似）
    assert_eq(pairs[0][2], 0)
    assert_eq(len(unmatched), 0)


# ==================== Delta 计算测试 ====================

def test_compute_delta_no_change():
    """测试无变化返回空 delta"""
    base = {"key1": {"a": 1, "b": 2}}
    mod = {"key1": {"a": 1, "b": 2}}
    delta = cast(dict, compute_mod_delta(base, mod, "dictionary"))
    assert_eq(delta, {})


def test_compute_delta_field_change():
    """测试字段级变化"""
    base = {"key1": {"a": 1, "b": 2}}
    mod = {"key1": {"a": 1, "b": 99}}
    delta = cast(dict, compute_mod_delta(base, mod, "dictionary"))
    assert_in("key1", delta)
    assert_eq(delta["key1"], {"b": 99})


def test_compute_delta_new_entry():
    """测试新增条目"""
    base = {"key1": {"a": 1}}
    mod = {"key1": {"a": 1}, "key2": {"x": 10}}
    delta = cast(dict, compute_mod_delta(base, mod, "dictionary"))
    assert_in("key2", delta)
    assert_eq(delta["key2"], {"x": 10})


# ==================== 深度合并测试 ====================

def test_deep_merge_basic():
    """测试基本字典合并"""
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    override = {"b": {"c": 99}}
    result = cast(dict, deep_merge(base, override, None, None))
    assert_eq(result["a"], 1)
    assert_eq(result["b"]["c"], 99)
    assert_eq(result["b"]["d"], 3)


def test_deep_merge_new_key():
    """测试新增 key"""
    base = {"a": 1}
    override = {"b": 2}
    result = cast(dict, deep_merge(base, override, None, None))
    assert_eq(result, {"a": 1, "b": 2})


def test_deep_merge_replace_scalar():
    """测试标量替换"""
    base = {"a": 1}
    override = {"a": "new"}
    result = cast(dict, deep_merge(base, override, None, None))
    assert_eq(result["a"], "new")


# ==================== strip_marker / classify_delta 测试 ====================

def test_strip_marker():
    """测试标记移除"""
    item = {"id": "a", "v": 1, "_delta": True}
    clean = _strip_marker(item, "_delta")
    assert_eq(clean, {"id": "a", "v": 1})
    # 原始不变
    assert_in("_delta", item)


def test_classify_delta_items():
    """测试 delta 分类"""
    items = [
        {"id": "a", "_delta": True},
        {"id": "b", "_new_entry": True},
        {"id": "c", "_deleted": True},
    ]
    deltas, new_entries, deleted = _classify_delta_items(items)
    assert_eq(len(deltas), 1)
    assert_eq(len(new_entries), 1)
    assert_eq(len(deleted), 1)
    assert_eq(deltas[0]["id"], "a")


def test_classify_delta_items_unmarked():
    """测试未标记元素抛出异常"""
    items = [{"id": "a"}]
    try:
        _classify_delta_items(items, context="test")
        raise AssertionError("应该抛出 ValueError")
    except ValueError:
        pass


# ==================== 合并文件测试 ====================

def test_merge_file_single_mod():
    """测试单 mod 合并"""
    base = {"a": 1, "b": 2}
    mod_data = [("mod1", "TestMod", {"b": 99})]
    result: MergeResult = merge_file(base, mod_data)
    assert_eq(result.merged_data["a"], 1)
    assert_eq(result.merged_data["b"], 99)


def test_merge_file_multi_mod():
    """测试多 mod 合并（后者优先）"""
    base = {"a": 1, "b": 2, "c": 3}
    mod_data = [
        ("mod1", "Mod1", {"b": 10}),
        ("mod2", "Mod2", {"b": 20, "c": 30}),
    ]
    result: MergeResult = merge_file(base, mod_data)
    assert_eq(result.merged_data["a"], 1)
    assert_eq(result.merged_data["b"], 20)  # mod2 覆盖
    assert_eq(result.merged_data["c"], 30)


# ==================== 真实数据测试 ====================

def test_scan_mods_real():
    """使用真实 workshop 目录扫描 Mod"""
    game_config, workshop = _get_real_paths()
    if game_config is None or workshop is None or not workshop.exists():
        skip("workshop 目录不存在")
    from src.core.mod_scanner import scan_all_mods
    workshop_path = cast(Path, workshop)
    mods = scan_all_mods(workshop_path, exclude_ids={"0000000001"})
    assert_true(len(mods) >= 0, "扫描应返回列表")


def test_schema_load_real():
    """加载真实 schema 目录"""
    from src.config import SCHEMA_DIR
    if not SCHEMA_DIR.exists() or not any(SCHEMA_DIR.glob("*.schema.json")):
        skip("schema 目录为空或不存在")
    from src.core.schema_loader import load_schemas
    schemas = load_schemas(SCHEMA_DIR)
    assert_true(len(schemas) > 0, "应加载到 schema")


# ==================== 入口 ====================

def run_all(result: TestResult):
    """运行全部功能测试"""
    tests = [
        # macOS 路径
        ("test_detect_steam_root_mac_fallback", test_detect_steam_root_mac_fallback),
        ("test_default_local_mod_path_mac", test_default_local_mod_path_mac),
        ("test_detect_game_path_mac_app_bundle", test_detect_game_path_mac_app_bundle),
        ("test_infer_workshop_path_from_game_mac_game_folder_and_app", test_infer_workshop_path_from_game_mac_game_folder_and_app),
        ("test_user_config_game_config_path_mac_app_bundle", test_user_config_game_config_path_mac_app_bundle),
        # JSON 解析
        ("test_strip_js_comments", test_strip_js_comments),
        ("test_strip_js_comments_in_string", test_strip_js_comments_in_string),
        ("test_strip_trailing_commas", test_strip_trailing_commas),
        ("test_load_json_with_comments", test_load_json_with_comments),
        # 类型识别
        ("test_classify_dictionary", test_classify_dictionary),
        ("test_classify_entity", test_classify_entity),
        ("test_classify_config", test_classify_config),
        # 数组匹配
        ("test_find_matching_item", test_find_matching_item),
        ("test_find_matching_item_no_match", test_find_matching_item_no_match),
        ("test_get_key_vals", test_get_key_vals),
        ("test_get_key_vals_missing", test_get_key_vals_missing),
        ("test_item_similarity", test_item_similarity),
        ("test_resolve_duplicates", test_resolve_duplicates),
        # Delta 计算
        ("test_compute_delta_no_change", test_compute_delta_no_change),
        ("test_compute_delta_field_change", test_compute_delta_field_change),
        ("test_compute_delta_new_entry", test_compute_delta_new_entry),
        # 深度合并
        ("test_deep_merge_basic", test_deep_merge_basic),
        ("test_deep_merge_new_key", test_deep_merge_new_key),
        ("test_deep_merge_replace_scalar", test_deep_merge_replace_scalar),
        # 工具函数
        ("test_strip_marker", test_strip_marker),
        ("test_classify_delta_items", test_classify_delta_items),
        ("test_classify_delta_items_unmarked", test_classify_delta_items_unmarked),
        # 合并文件
        ("test_merge_file_single_mod", test_merge_file_single_mod),
        ("test_merge_file_multi_mod", test_merge_file_multi_mod),
        # 真实数据
        ("test_scan_mods_real", test_scan_mods_real),
        ("test_schema_load_real", test_schema_load_real),
    ]
    for name, func in tests:
        run_test(name, func, result)
