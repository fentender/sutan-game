"""
DupList 合并测试 - 验证重复键字段在合并管道中类型和结构的正确性

复现场景：游戏 rite 文件 settlement_prior 条目的 result 中含重复 card 键，
如 "card":[2000123,"追随者+1"], "card":[2000808,"已拥有+1"]。
当 base 只有一个 card（普通 list）而 mod 有两个 card（DupList）时，
合并应输出正确的 DupList 结构，而非产生嵌套数组。
"""
import copy
import json as _json

from sultan_core.json import JsonDoc
from sultan_core.state import MergeMode as CppMergeMode
from sultan_core.delta import compute_delta, serialize_delta

from src.core.json.parser import DupList
from src.core.merge.merger import apply_dict_delta, merge_file
from src.core.infra.types import (
    ArrayFieldDiff,
    ChangeKind,
    DictFieldDiff,
    FieldDiff,
    JsonObject,
    MergeMode,
)
from tests.python.test_runner import TestResult, assert_eq, assert_true, run_test


_CPP_MODE: dict[MergeMode, CppMergeMode] = {
    MergeMode.NORMAL: CppMergeMode.NORMAL,
    MergeMode.SMART: CppMergeMode.SMART,
}


def _compute_delta(
    base_data: JsonObject,
    mod_data: JsonObject,
    file_type: str,
    merge_mode: MergeMode = MergeMode.NORMAL,
) -> DictFieldDiff | None:
    """compute_delta 的 C++ API 替代"""
    base_doc = JsonDoc.parse(_json.dumps(base_data))
    mod_doc = JsonDoc.parse(_json.dumps(mod_data))
    is_dict = file_type == "dictionary"
    delta = compute_delta(base_doc, mod_doc, _CPP_MODE[merge_mode], is_dict)
    if delta is not None:
        doc = serialize_delta(delta)
        return DictFieldDiff.from_delta_dict(_json.loads(doc.to_string()))
    return None


def _make_base_settlement_entry() -> JsonObject:
    """构造一个包含单 card 键的 settlement_prior 条目（模拟游戏本体）"""
    return {
        "guid": "test-guid-0001",
        "condition": {"s3.奢靡": 1, "s4": 1},
        "result_title": "测试标题",
        "result_text": "测试文本",
        "result": {
            "clean.s1": 1,
            "card": [2000123, "追随者+1", "魅力+1"],
            "s2+已拥有": 1,
        },
        "action": {},
    }


def _make_mod_settlement_entry() -> JsonObject:
    """构造一个包含重复 card 键的 settlement_prior 条目（模拟 Mod）"""
    return {
        "guid": "test-guid-0001",
        "condition": {"s3.奢靡": 1, "s4": 1},
        "result_title": "测试标题",
        "result_text": "测试文本",
        "result": {
            "clean.s1": 1,
            "card": DupList([
                [2000123, "追随者+1"],
                [2000808, "已拥有+1"],
            ]),
            "s2+已拥有": 1,
        },
        "action": {},
    }


def test_duplist_delta_apply() -> None:
    """DupList 字段合并：flat list → DupList 时应正确转换结构

    base.result.card = [2000123, "追随者+1", "魅力+1"]  (普通 list)
    mod.result.card  = DupList([[2000123, "追随者+1"], [2000808, "已拥有+1"]])

    合并后 card 应为 DupList，包含两个子列表，不应出现嵌套数组。
    """
    base_data: JsonObject = {
        "id": 9999999,
        "name": "测试仪式",
        "settlement_prior": [_make_base_settlement_entry()],
        "settlement": [],
    }
    mod_data: JsonObject = {
        "id": 9999999,
        "name": "测试仪式",
        "settlement_prior": [_make_mod_settlement_entry()],
        "settlement": [],
    }

    delta = _compute_delta(base_data, mod_data, "entity",
                          merge_mode=MergeMode.NORMAL)
    assert_true(delta is not None, "delta 不应为 None")
    assert isinstance(delta, DictFieldDiff)

    full_state = DictFieldDiff.from_dict(base_data)
    apply_dict_delta(full_state, delta, None, version=1)
    result_dict = full_state.to_dict()

    # 验证 settlement_prior 结构
    sp = result_dict.get("settlement_prior")
    assert_true(isinstance(sp, list), "settlement_prior 应为 list")
    assert isinstance(sp, list)
    assert_eq(len(sp), 1, "settlement_prior 应有 1 个条目")

    entry = sp[0]
    assert_true(isinstance(entry, dict), "条目应为 dict")
    assert isinstance(entry, dict)

    card = entry.get("result", {})
    assert_true(isinstance(card, dict), "result 应为 dict")
    assert isinstance(card, dict)
    card_val = card.get("card")

    # 核心断言：card 应为 DupList，包含两个子列表
    assert_true(
        isinstance(card_val, DupList),
        f"card 应为 DupList，实际类型: {type(card_val).__name__}，值: {card_val!r}",
    )
    assert isinstance(card_val, DupList)
    assert_eq(len(card_val), 2, "card DupList 应有 2 个元素")

    # 验证每个子列表的内容
    assert_true(
        isinstance(card_val[0], list),
        f"card[0] 应为 list，实际: {type(card_val[0]).__name__}",
    )
    assert_true(
        isinstance(card_val[1], list),
        f"card[1] 应为 list，实际: {type(card_val[1]).__name__}",
    )
    assert_eq(card_val[0][0], 2000123, "card[0] 第一个元素应为 2000123")
    assert_eq(card_val[1][0], 2000808, "card[1] 第一个元素应为 2000808")


def test_duplist_merge_file() -> None:
    """通过 merge_file 端到端验证 DupList 字段合并

    模拟两个 Mod 同时修改一个 rite 文件：
    - Mod A 修改了 tips_text（与 card 无关）
    - Mod B 将 card 从普通 list 改为 DupList
    合并后 card 应为 DupList。
    """
    base_data: JsonObject = {
        "id": 9999999,
        "name": "测试仪式",
        "settlement_prior": [_make_base_settlement_entry()],
        "settlement": [],
        "tips_text": ["原始提示"],
    }

    # Mod A: 只修改 tips_text
    mod_a_data = copy.deepcopy(base_data)
    assert isinstance(mod_a_data.get("tips_text"), list)
    mod_a_tips = mod_a_data["tips_text"]
    assert isinstance(mod_a_tips, list)
    mod_a_tips.append("新增提示")

    # Mod B: 修改 card 为 DupList
    mod_b_data = copy.deepcopy(base_data)
    mod_b_sp = mod_b_data["settlement_prior"]
    assert isinstance(mod_b_sp, list)
    entry = mod_b_sp[0]
    assert isinstance(entry, dict)
    result = entry["result"]
    assert isinstance(result, dict)
    result["card"] = DupList([
        [2000123, "追随者+1"],
        [2000808, "已拥有+1"],
    ])
    result["loot"] = 6000005

    delta_a = _compute_delta(base_data, mod_a_data, "entity",
                             merge_mode=MergeMode.NORMAL)
    delta_b = _compute_delta(base_data, mod_b_data, "entity",
                             merge_mode=MergeMode.NORMAL)
    assert_true(delta_a is not None, "delta_a 不应为 None")
    assert_true(delta_b is not None, "delta_b 不应为 None")
    assert isinstance(delta_a, DictFieldDiff)
    assert isinstance(delta_b, DictFieldDiff)

    mod_list: list[tuple[str, str, DictFieldDiff, str]] = [
        ("mod_a", "ModA", delta_a, "mod_a/5002009.json"),
        ("mod_b", "ModB", delta_b, "mod_b/5002009.json"),
    ]

    merge_result = merge_file(base_data, mod_list, "rite/test.json")
    merged = merge_result.merged_data

    # 验证 tips_text 被 Mod A 修改
    tips = merged.get("tips_text")
    assert_true(isinstance(tips, list), "tips_text 应为 list")
    assert isinstance(tips, list)
    assert_eq(len(tips), 2, "tips_text 应有 2 项")

    # 验证 card 被 Mod B 修改为 DupList
    sp = merged.get("settlement_prior")
    assert_true(isinstance(sp, list), "settlement_prior 应为 list")
    assert isinstance(sp, list)
    entry = sp[0]
    assert_true(isinstance(entry, dict), "条目应为 dict")
    assert isinstance(entry, dict)
    result = entry.get("result", {})
    assert isinstance(result, dict)
    card_val = result.get("card")

    assert_true(
        isinstance(card_val, DupList),
        f"card 应为 DupList，实际: {type(card_val).__name__}，值: {card_val!r}",
    )
    assert isinstance(card_val, DupList)
    assert_eq(len(card_val), 2, "card DupList 应有 2 个元素")
    assert_eq(card_val[0][0], 2000123, "card[0][0] 应为 2000123")
    assert_eq(card_val[1][0], 2000808, "card[1][0] 应为 2000808")

    # 验证 loot 被 Mod B 新增
    assert_eq(result.get("loot"), 6000005, "loot 应为 6000005")


def test_duplist_no_nested_arrays_in_output() -> None:
    """验证合并输出中不会出现嵌套数组（游戏解析器会报错）

    这是 rite/5002009.json 的实际报错场景的精简复现：
    游戏 SingleOrListValuesJsonConverter 在读取 card 时
    期望值 token 但拿到了 BEGIN_ARRAY。
    """
    base_data: JsonObject = {
        "id": 9999999,
        "name": "测试",
        "result": {
            "card": [2000123, "追随者+1", "魅力+1"],
        },
    }
    mod_data: JsonObject = {
        "id": 9999999,
        "name": "测试",
        "result": {
            "card": DupList([
                [2000123, "追随者+1"],
                [2000808, "已拥有+1"],
            ]),
        },
    }

    delta = _compute_delta(base_data, mod_data, "entity",
                          merge_mode=MergeMode.NORMAL)
    assert_true(delta is not None, "delta 不应为 None")
    assert isinstance(delta, DictFieldDiff)

    full_state = DictFieldDiff.from_dict(base_data)
    apply_dict_delta(full_state, delta, None, version=1)
    result_dict = full_state.to_dict()

    result = result_dict.get("result", {})
    assert isinstance(result, dict)
    card_val = result.get("card")

    # DupList 的元素是子列表（正确：序列化为重复键）
    # 普通 list 不应有嵌套数组（会导致游戏 BEGIN_ARRAY 错误）
    assert_true(
        isinstance(card_val, DupList),
        f"card 应为 DupList（序列化为重复键），实际: {type(card_val).__name__}，值: {card_val!r}",
    )
    assert isinstance(card_val, DupList)
    assert_eq(len(card_val), 2, "card DupList 应有 2 个元素")
    assert_eq(card_val[0][0], 2000123, "card[0][0] 应为 2000123")
    assert_eq(card_val[1][0], 2000808, "card[1][0] 应为 2000808")


def run_all(result: TestResult) -> None:
    run_test("duplist_delta_apply", test_duplist_delta_apply, result)
    run_test("duplist_merge_file", test_duplist_merge_file, result)
    run_test("duplist_no_nested_arrays", test_duplist_no_nested_arrays_in_output, result)
