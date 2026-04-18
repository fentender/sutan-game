"""
核心功能测试 - 数组匹配算法
"""
import json
import logging
from pathlib import Path

from src.config import UserConfig
from src.core.array_match import match_by_heuristic
from src.core.json_parser import clean_json_text
from tests.test_runner import TestResult, assert_eq, run_test, skip

log = logging.getLogger("test")

ABUDE_MOD_ID = "3497129580"


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


def run_all(result: TestResult) -> None:
    run_test("heuristic_insert_delete", test_heuristic_insert_delete, result)
