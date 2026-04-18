"""
核心功能测试 - 无 GUI，直接测试 core 模块
"""
import json
import tempfile

from src.config import SCHEMA_DIR, UserConfig
from src.core.array_match import (
    find_matching_item,
    get_key_vals,
    item_similarity,
    resolve_duplicates,
)
from src.core.json_parser import strip_js_comments, strip_trailing_commas
from src.core.delta_store import ModDelta, compute_delta
from src.core.json_store import JsonStore
from src.core.merger import (
    apply_delta,
    merge_file,
)
from src.core.type_utils import classify_json
from src.core.types import ChangeKind, DictDelta, FieldDiff
from tests.test_runner import (
    TestResult,
    assert_eq,
    assert_in,
    assert_true,
    run_test,
    skip,
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
        data = JsonStore.parse_file(f.name)
    assert_eq(data, {"key": "value"})


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
    """测试无变化返回 None"""
    base = {"key1": {"a": 1, "b": 2}}
    mod = {"key1": {"a": 1, "b": 2}}
    delta = compute_delta(base, mod, "dictionary")
    assert_eq(delta, None)


def test_compute_delta_field_change():
    """测试字段级变化"""
    base = {"key1": {"a": 1, "b": 2}}
    mod = {"key1": {"a": 1, "b": 99}}
    delta = compute_delta(base, mod, "dictionary")
    assert_true(delta is not None, "应有变化")
    assert_true(isinstance(delta, DictDelta), "应返回 DictDelta")
    assert_in("key1", delta.items)
    # key1 的变化应是一个 DictDelta，包含 b 字段的 CHANGED
    key1_diff = delta.items["key1"]
    assert_true(isinstance(key1_diff, DictDelta), "key1 的变化应是 DictDelta")
    assert_in("b", key1_diff.items)
    b_diff = key1_diff.items["b"]
    assert_true(isinstance(b_diff, FieldDiff), "b 的变化应是 FieldDiff")
    assert_eq(b_diff.kind, ChangeKind.CHANGED)
    assert_eq(b_diff.value, 99)


def test_compute_delta_new_entry():
    """测试新增条目"""
    base = {"key1": {"a": 1}}
    mod = {"key1": {"a": 1}, "key2": {"x": 10}}
    delta = compute_delta(base, mod, "dictionary")
    assert_true(delta is not None, "应有变化")
    assert_in("key2", delta.items)
    key2_diff = delta.items["key2"]
    assert_true(isinstance(key2_diff, FieldDiff), "key2 应是 FieldDiff")
    assert_eq(key2_diff.kind, ChangeKind.ADDED)
    assert_eq(key2_diff.value, {"x": 10})


# ==================== apply_delta 测试 ====================

def test_apply_delta_basic():
    """测试基本字典合并"""
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    delta = DictDelta(items={
        "b": DictDelta(items={
            "c": FieldDiff(ChangeKind.CHANGED, 99),
        }),
    })
    result = apply_delta(base, delta)
    assert_eq(result["a"], 1)
    assert_eq(result["b"]["c"], 99)
    assert_eq(result["b"]["d"], 3)


def test_apply_delta_new_key():
    """测试新增 key"""
    base = {"a": 1}
    delta = DictDelta(items={
        "b": FieldDiff(ChangeKind.ADDED, 2),
    })
    result = apply_delta(base, delta)
    assert_eq(result, {"a": 1, "b": 2})


def test_apply_delta_replace_scalar():
    """测试标量替换"""
    base = {"a": 1}
    delta = DictDelta(items={
        "a": FieldDiff(ChangeKind.CHANGED, "new"),
    })
    result = apply_delta(base, delta)
    assert_eq(result["a"], "new")


# ==================== 合并文件测试 ====================

def test_merge_file_single_mod():
    """测试单 mod 合并"""
    base = {"a": 1, "b": 2}
    mod_data = {"b": 99}
    delta = compute_delta(base, mod_data, "config")
    assert_true(delta is not None, "应有变化")
    result = merge_file(base, [("mod1", "TestMod", delta, "test.json")])
    assert_eq(result.merged_data["a"], 1)
    assert_eq(result.merged_data["b"], 99)


def test_merge_file_multi_mod():
    """测试多 mod 合并（后者优先）"""
    base = {"a": 1, "b": 2, "c": 3}
    delta1 = compute_delta(base, {"a": 1, "b": 10, "c": 3}, "config")
    delta2 = compute_delta(base, {"a": 1, "b": 20, "c": 30}, "config")
    assert_true(delta1 is not None and delta2 is not None, "应有变化")
    result = merge_file(base, [
        ("mod1", "Mod1", delta1, "test1.json"),
        ("mod2", "Mod2", delta2, "test2.json"),
    ])
    assert_eq(result.merged_data["a"], 1)
    assert_eq(result.merged_data["b"], 20)  # mod2 覆盖
    assert_eq(result.merged_data["c"], 30)


# ==================== 真实数据测试 ====================

def test_scan_mods_real():
    """使用真实 workshop 目录扫描 Mod"""
    game_config, workshop = _get_real_paths()
    if not workshop or not workshop.exists():
        skip("workshop 目录不存在")
    from src.core.mod_scanner import scan_all_mods
    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    assert_true(len(mods) >= 0, "扫描应返回列表")


def test_schema_load_real():
    """加载真实 schema 目录"""
    from src.config import SCHEMA_DIR
    if not SCHEMA_DIR.exists() or not any(SCHEMA_DIR.glob("*.schema.json")):
        skip("schema 目录为空或不存在")
    from src.core.schema_loader import load_schemas
    schemas = load_schemas(SCHEMA_DIR)
    assert_true(len(schemas) > 0, "应加载到 schema")


# ==================== ModDelta 缓存测试 ====================

def _init_store_and_delta():
    """初始化 JsonStore 和 ModDelta，返回 mod_ids。不可用则 skip。"""
    game_config, workshop = _get_real_paths()
    if not game_config or not workshop or not workshop.exists():
        skip("真实游戏/workshop 数据不可用")
    from src.core.mod_scanner import scan_all_mods
    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]
    store = JsonStore.instance()
    store.init(game_config, mod_configs)
    mod_ids = [m.mod_id for m in mods]
    ModDelta.init(mod_ids, schema_dir=SCHEMA_DIR)
    return mod_ids


def test_mod_delta_init_and_get():
    """测试 ModDelta 初始化后 has/get 正常工作"""
    mod_ids = _init_store_and_delta()
    store = JsonStore.instance()
    # 找一个有文件的 mod
    found = False
    for mod_id in mod_ids:
        for rel_path in store.mod_files(mod_id):
            assert_true(ModDelta.has(mod_id, rel_path),
                        f"ModDelta 应有缓存: ({mod_id}, {rel_path})")
            # get 不应抛异常
            ModDelta.get(mod_id, rel_path)
            found = True
            break
        if found:
            break
    assert_true(found, "应至少找到一个有文件的 mod")
    ModDelta.clear()


def test_mod_delta_progress():
    """测试 init 完成后 progress 返回正确值"""
    mod_ids = _init_store_and_delta()
    completed, total = ModDelta.progress()
    assert_true(total > 0, f"total 应 > 0，实际 {total}")
    assert_eq(completed, total)
    ModDelta.clear()


def test_mod_delta_invalidate():
    """测试 invalidate 后缓存被清空"""
    mod_ids = _init_store_and_delta()
    store = JsonStore.instance()
    # 记录一个已缓存的 key
    cached_key: tuple[str, str] | None = None
    for mod_id in mod_ids:
        files = store.mod_files(mod_id)
        if files:
            cached_key = (mod_id, files[0])
            break
    assert_true(cached_key is not None, "应有已缓存的 key")
    assert cached_key is not None
    assert_true(ModDelta.has(*cached_key), "invalidate 前应有缓存")
    ModDelta.invalidate()
    assert_true(not ModDelta.has(*cached_key), "invalidate 后应无缓存")
    ModDelta.clear()


def test_mod_delta_progress_callback():
    """测试 progress_cb 被正确调用"""
    game_config, workshop = _get_real_paths()
    if not game_config or not workshop or not workshop.exists():
        skip("真实游戏/workshop 数据不可用")
    from src.core.mod_scanner import scan_all_mods
    mods = scan_all_mods(workshop, exclude_ids={"0000000001"})
    if not mods:
        skip("没有可用的 Mod")
    mod_configs = [(m.mod_id, m.name, m.path / "config") for m in mods]
    store = JsonStore.instance()
    store.init(game_config, mod_configs)
    mod_ids = [m.mod_id for m in mods]

    cb_calls: list[tuple[int, int]] = []
    ModDelta.init(mod_ids, schema_dir=SCHEMA_DIR,
                  progress_cb=lambda c, t: cb_calls.append((c, t)))
    assert_true(len(cb_calls) > 0, "progress_cb 应被调用")
    last_completed, last_total = cb_calls[-1]
    assert_eq(last_completed, last_total)
    ModDelta.clear()


# ==================== 入口 ====================

def run_all(result: TestResult):
    """运行全部功能测试"""
    tests = [
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
        # apply_delta
        ("test_apply_delta_basic", test_apply_delta_basic),
        ("test_apply_delta_new_key", test_apply_delta_new_key),
        ("test_apply_delta_replace_scalar", test_apply_delta_replace_scalar),
        # 合并文件
        ("test_merge_file_single_mod", test_merge_file_single_mod),
        ("test_merge_file_multi_mod", test_merge_file_multi_mod),
        # 真实数据
        ("test_scan_mods_real", test_scan_mods_real),
        ("test_schema_load_real", test_schema_load_real),
        # ModDelta 缓存
        ("test_mod_delta_init_and_get", test_mod_delta_init_and_get),
        ("test_mod_delta_progress", test_mod_delta_progress),
        ("test_mod_delta_invalidate", test_mod_delta_invalidate),
        ("test_mod_delta_progress_callback", test_mod_delta_progress_callback),
    ]
    for name, func in tests:
        run_test(name, func, result)
