"""
核心功能测试 - 数组匹配算法 + Override 保存机制
"""
import copy
import json
import logging
from pathlib import Path

from src.config import SCHEMA_DIR, UserConfig
from src.core.array_match import match_by_heuristic
from src.core.delta_store import ModDelta, compute_delta
from src.core.json_parser import _pairs_hook, clean_json_text
from src.core.json_store import JsonStore
from src.core.merge_cache import MergeCache
from src.core.types import MergeMode
from tests.test_runner import TestResult, assert_eq, assert_true, run_test, skip

log = logging.getLogger("test")

ABUDE_MOD_ID = "3497129580"
RITE_REL_PATH = "rite/5000003.json"
# 「我全都要」系MOD整合版
WQDYMOD_ID = "3489112041"


def _load_settlement(path: Path) -> list[object]:
    """加载 rite/5000003.json 的 settlement 数组"""
    text = path.read_text(encoding="utf-8-sig")
    data = json.loads(clean_json_text(text))
    assert isinstance(data, dict), f"期望 dict，实际 {type(data)}"
    settlement = data.get("settlement")
    assert isinstance(settlement, list), "缺少 settlement 数组"
    return settlement


def _require_rite_data() -> tuple[list[object], list[object]]:
    """加载本体和阿卜德的游戏 Mod 的 settlement 数组"""
    config = UserConfig.load()
    game_config = config.game_config_path
    if not game_config.exists():
        skip("游戏本体数据不可用")

    base_file = game_config / "rite" / "5000003.json"
    if not base_file.exists():
        skip(f"本体文件不存在: {base_file}")

    mod_file = config.workshop_dir / ABUDE_MOD_ID / "config" / "rite" / "5000003.json"
    if not mod_file.exists():
        skip(f"阿卜德的游戏 Mod 不存在: {mod_file}")

    return _load_settlement(base_file), _load_settlement(mod_file)


def test_heuristic_insert_delete() -> None:
    """match_by_heuristic：中间插入+删除场景不应级联错配

    阿卜德的游戏 Mod 对 rite/5000003.json settlement 数组：
    - 在位置 14 插入了新元素（无 guid）
    - 删除了 base[16]（guid=95077769...）

    期望：base[16] → unmatched_base，mod[14] → unmatched_mod，
    其余元素通过 guid 精确匹配。
    """
    base_arr, mod_arr = _require_rite_data()

    # 期望映射：
    # base[0..13] → mod[0..13]
    # base[14..15] → mod[15..16]
    # base[16] → unmatched（被删除）
    # base[17..27] → mod[17..27]
    # mod[14] → unmatched（新插入）
    expected_pairs: dict[int, int] = {}
    for i in range(14):
        expected_pairs[i] = i
    expected_pairs[14] = 15
    expected_pairs[15] = 16
    # base[16] 无对应
    for i in range(17, len(base_arr)):
        expected_pairs[i] = i
    expected_unmatched_base = [16]
    expected_unmatched_mod = [14]

    result = match_by_heuristic(base_arr, mod_arr)

    # 打印完整匹配结果
    def _guid8(arr: list[object], idx: int) -> str:
        e = arr[idx]
        if isinstance(e, dict):
            g = e.get("guid", "")
            return str(g)[:8] if g else "NO_GUID"
        return "?"

    actual_map: dict[int, int] = {bi: mi for bi, mi in result.pairs}

    log.info("    匹配对 (%d 组):", len(result.pairs))
    for bi, mi in sorted(result.pairs):
        bg = _guid8(base_arr, bi)
        mg = _guid8(mod_arr, mi)
        tag = "OK" if expected_pairs.get(bi) == mi else "WRONG"
        log.info("      base[%2d](guid=%s) -> mod[%2d](guid=%s)  %s", bi, bg, mi, mg, tag)
    log.info("    unmatched_base: %s", result.unmatched_base)
    log.info("    unmatched_mod:  %s", result.unmatched_mod)
    log.info("    confidence:     %s", result.confidence)

    # 断言
    assert_eq(result.unmatched_base, expected_unmatched_base, "unmatched_base")
    assert_eq(result.unmatched_mod, expected_unmatched_mod, "unmatched_mod")
    for bi, expected_mi in expected_pairs.items():
        assert_eq(actual_map.get(bi), expected_mi, f"base[{bi}] 应匹配 mod[{expected_mi}]")


def _init_merge_env() -> tuple[
    list[tuple[str, str, Path]], JsonStore, MergeCache,
]:
    """初始化合并环境：加载 store、计算 delta、返回 mod_configs"""
    config = UserConfig.load()
    game_config = config.game_config_path
    if not game_config.exists():
        skip("游戏本体数据不可用")

    workshop = config.workshop_dir
    mod_order = config.mod_order
    enabled = set(config.enabled_mods) if config.enabled_mods else set(mod_order)

    mod_configs: list[tuple[str, str, Path]] = []
    for mod_id in mod_order:
        if mod_id not in enabled:
            continue
        for base_dir in [workshop, config.local_mod_dir]:
            mod_dir = base_dir / mod_id
            config_dir = mod_dir / "config"
            if config_dir.exists():
                mod_configs.append((mod_id, mod_id, config_dir))
                break

    store = JsonStore.instance()
    store.init(game_config, mod_configs)
    ModDelta.init([mc[0] for mc in mod_configs], SCHEMA_DIR)

    cache = MergeCache.instance()
    return mod_configs, store, cache


def _get_step_right_json(
    cache: MergeCache,
    mod_configs: list[tuple[str, str, Path]],
    rel_path: str,
    target_mod: str,
) -> dict[str, object]:
    """获取指定 mod step 的右侧展开 JSON（无填充行）"""
    state = cache.get(rel_path, mod_configs, SCHEMA_DIR)
    for step in state.steps:
        if step.mod_id == target_mod:
            text = "\n".join(
                line for line, rk in zip(step.right_lines, step.right_kinds,
                                         strict=True)
                if rk is not None
            )
            result: dict[str, object] = json.loads(
                clean_json_text(text), object_pairs_hook=_pairs_hook,
            )
            return result
    skip(f"mod {target_mod} 不在合并步骤中")
    raise AssertionError("unreachable")  # 让 mypy 满意


def _apply_override_and_verify(
    store: JsonStore,
    cache: MergeCache,
    mod_configs: list[tuple[str, str, Path]],
    rel_path: str,
    target_mod: str,
    old_json: dict[str, object],
    expected_json: dict[str, object],
    label: str,
) -> None:
    """计算 override delta、保存、重新合并、验证结果一致"""
    delta = compute_delta(old_json, expected_json, "config",
                          merge_mode=MergeMode.NORMAL)
    if delta is None:
        raise AssertionError(f"{label}: compute_delta 返回 None，编辑未产生差异")

    try:
        store.set_override(target_mod, rel_path, delta)
        cache.invalidate(rel_path)
        actual_json = _get_step_right_json(cache, mod_configs, rel_path,
                                           target_mod)
        assert_true(
            actual_json == expected_json,
            f"{label}: override 应用后结果与编辑不一致",
        )
    finally:
        store.remove_override(target_mod, rel_path)
        cache.invalidate(rel_path)


def test_override_single_field() -> None:
    """Override 保存：修改 settlement 中单个元素的单个字段后能正确还原

    模拟 diff_dialog._save_override 的流程：
    1. 从合并结果中提取右侧文本 → 解析为 old_json
    2. 用户编辑一个字段 → new_json
    3. compute_delta(old, new) → override delta
    4. 保存 override → 重新合并 → 验证结果 == new_json
    """
    mod_configs, store, cache = _init_merge_env()

    # 确认目标 mod 存在
    if not store.has_mod(WQDYMOD_ID, RITE_REL_PATH):
        skip(f"mod {WQDYMOD_ID} 无 {RITE_REL_PATH}")

    old_json = _get_step_right_json(cache, mod_configs, RITE_REL_PATH,
                                    WQDYMOD_ID)
    new_json = copy.deepcopy(old_json)

    # 找到第一个含 !s3.纵欲的痕迹 的 settlement 元素并修改
    settlement = new_json.get("settlement")
    assert isinstance(settlement, list), "缺少 settlement 数组"
    edited = False
    for item in settlement:
        if isinstance(item, dict):
            cond = item.get("condition")
            if isinstance(cond, dict) and "!s3.纵欲的痕迹" in cond:
                cond["!s3.纵欲的痕迹"] = 2
                edited = True
                break
    assert_true(edited, "未找到可编辑的 condition 字段")

    _apply_override_and_verify(
        store, cache, mod_configs, RITE_REL_PATH, WQDYMOD_ID,
        old_json, new_json, "单字段修改",
    )


def test_override_multi_element() -> None:
    """Override 保存：同时修改多个 settlement 元素的字段后能正确还原

    回归测试：之前 apply_array_delta 在 is_override 时错误地用
    override delta 的 order 重建全状态 order，导致数组元素错位。
    """
    mod_configs, store, cache = _init_merge_env()

    if not store.has_mod(WQDYMOD_ID, RITE_REL_PATH):
        skip(f"mod {WQDYMOD_ID} 无 {RITE_REL_PATH}")

    old_json = _get_step_right_json(cache, mod_configs, RITE_REL_PATH,
                                    WQDYMOD_ID)
    new_json = copy.deepcopy(old_json)

    settlement = new_json.get("settlement")
    assert isinstance(settlement, list), "缺少 settlement 数组"
    assert isinstance(settlement, list)
    edit_count = 0
    for item in settlement:
        if isinstance(item, dict):
            cond = item.get("condition")
            if isinstance(cond, dict) and "!s3.纵欲的痕迹" in cond:
                cond["!s3.纵欲的痕迹"] = 999
                edit_count += 1
                if edit_count >= 3:
                    break
    assert_true(edit_count >= 2, f"可编辑元素不足，仅找到 {edit_count} 个")

    _apply_override_and_verify(
        store, cache, mod_configs, RITE_REL_PATH, WQDYMOD_ID,
        old_json, new_json, "多元素修改",
    )


def test_override_after_mod_insert() -> None:
    """Override 保存：修改位于 mod 新增元素之后的 base 元素时能正确定位

    回归测试：override delta 的 indices 是 flat position（基于展开后数组），
    而全状态 ArrayFieldDiff 的 indices 是 element ID。当 mod 在数组中间
    插入了新元素，flat position 和 element ID 产生偏移，导致修改应用到
    错误的元素上。
    """
    mod_configs, store, cache = _init_merge_env()

    if not store.has_mod(WQDYMOD_ID, RITE_REL_PATH):
        skip(f"mod {WQDYMOD_ID} 无 {RITE_REL_PATH}")

    old_json = _get_step_right_json(cache, mod_configs, RITE_REL_PATH,
                                    WQDYMOD_ID)
    new_json = copy.deepcopy(old_json)

    # 修改 settlement 中 s3.is == 2000062 的第一个元素（位于 mod 新增元素之后）
    settlement = new_json.get("settlement")
    assert isinstance(settlement, list), "缺少 settlement 数组"
    edited = False
    for item in settlement:
        if isinstance(item, dict):
            cond = item.get("condition")
            if isinstance(cond, dict) and cond.get("s3.is") == 2000062:
                cond["__test_override__"] = 777
                edited = True
                break
    assert_true(edited, "未找到 s3.is==2000062 的 condition")

    _apply_override_and_verify(
        store, cache, mod_configs, RITE_REL_PATH, WQDYMOD_ID,
        old_json, new_json, "mod 新增元素之后的编辑",
    )


def run_all(result: TestResult) -> None:
    run_test("heuristic_insert_delete", test_heuristic_insert_delete, result)
    run_test("override_single_field", test_override_single_field, result)
    run_test("override_multi_element", test_override_multi_element, result)
    run_test("override_after_mod_insert", test_override_after_mod_insert, result)
