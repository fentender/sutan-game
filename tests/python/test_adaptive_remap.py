"""
ADAPTIVE 模式 delta 重映射测试

复现 bug：ADAPTIVE 模式下 delta 基于历史 base 计算，但应用到当前 base 时
索引/key 不匹配，导致：
1. dict 字段：DELETED 一个当前 base 中不存在的 key 会凭空创建 null 条目
2. 数组：CHANGED 元素的 ID 对不上当前 base 的元素

通过 _remap_delta_to_current 把 delta 从历史 base 坐标系转换到当前 base 坐标系。
"""
import json as _json
import logging

from sultan_core.json import JsonDoc
from sultan_core.state import MergeMode as CppMergeMode
from sultan_core.delta import compute_and_serialize, remap_delta

from src.core.merge.delta import _is_valid_delta
from src.core.infra.types import ArrayFieldDiff, ChangeKind, DictFieldDiff, FieldDiff, MergeMode
from tests.python.test_runner import TestResult, assert_eq, assert_true, run_test

log = logging.getLogger("test")


_CPP_MODE: dict[MergeMode, CppMergeMode] = {
    MergeMode.NORMAL: CppMergeMode.NORMAL,
    MergeMode.SMART: CppMergeMode.SMART,
}


def _compute_delta(
    base_data: dict[str, object],
    mod_data: dict[str, object],
    file_type: str,
    merge_mode: MergeMode = MergeMode.NORMAL,
) -> DictFieldDiff | None:
    """compute_delta 的 C++ API 替代"""
    base_doc = JsonDoc.parse(_json.dumps(base_data))
    mod_doc = JsonDoc.parse(_json.dumps(mod_data))
    is_dict = file_type == "dictionary"
    delta_doc = compute_and_serialize(base_doc, mod_doc, _CPP_MODE[merge_mode], is_dict)
    if _is_valid_delta(delta_doc):
        return DictFieldDiff.from_delta_dict(_json.loads(delta_doc.to_string()))
    return None


def _remap_delta_to_current(
    delta: DictFieldDiff,
    hist_base: dict[str, object],
    current_base: dict[str, object],
) -> DictFieldDiff | None:
    """_remap_delta_to_current 的 C++ API 替代"""
    delta_doc = JsonDoc.parse(_json.dumps(delta.to_delta_dict()))
    hist_doc = JsonDoc.parse(_json.dumps(hist_base))
    current_doc = JsonDoc.parse(_json.dumps(current_base))
    remapped_doc = remap_delta(delta_doc, hist_doc, current_doc)
    if remapped_doc.valid() and remapped_doc.to_string(True) != "null":
        return DictFieldDiff.from_delta_dict(_json.loads(remapped_doc.to_string()))
    return None


def _condition_diff_for_guid(delta: DictFieldDiff, guid: str) -> DictFieldDiff | None:
    """从 settlement 数组 delta 中提取 CHANGED 元素的 condition 子 delta（取第一个）"""
    settlement = delta.items.get("settlement")
    if not isinstance(settlement, ArrayFieldDiff):
        return None
    for fd in settlement.diffs:
        if not isinstance(fd, DictFieldDiff):
            continue
        cond = fd.items.get("condition")
        if isinstance(cond, DictFieldDiff):
            return cond
    return None


def test_dict_deleted_missing_in_current() -> None:
    """dict 字段：delta 删除的 key 在当前 base 已不存在 → 应被丢弃"""
    hist = {"a": 1, "b": 2}
    mod = {"a": 1}  # 删除了 b
    current = {"a": 1}  # 当前 base 也没有 b（游戏已自然移除）

    delta = _compute_delta(hist, mod, "config", merge_mode=MergeMode.NORMAL)
    assert_true(delta is not None, "delta 应非空")
    assert delta is not None  # mypy
    assert_true("b" in delta.items, "原始 delta 应含 b: DELETED")

    remapped = _remap_delta_to_current(delta, hist, current)
    if remapped is None:
        return  # 全部清空也算正确
    assert_true("b" not in remapped.items,
                "重映射后 b 应被丢弃（当前 base 中不存在）")


def test_dict_added_already_present() -> None:
    """dict 字段：delta 新增的 key 在当前 base 已存在且值相同 → 应被丢弃"""
    hist = {"a": 1}
    mod = {"a": 1, "b": 2}  # 新增 b
    current = {"a": 1, "b": 2}  # 当前 base 已有 b 且值相同

    delta = _compute_delta(hist, mod, "config", merge_mode=MergeMode.SMART)
    assert_true(delta is not None, "delta 应非空")
    assert delta is not None
    assert_true("b" in delta.items, "原始 delta 应含 b: ADDED")

    remapped = _remap_delta_to_current(delta, hist, current)
    if remapped is None:
        return
    assert_true("b" not in remapped.items,
                "重映射后 b 应被丢弃（当前 base 中已存在且值相同）")


def test_dict_added_present_diff_value() -> None:
    """dict 字段：delta 新增的 key 在当前 base 已存在但值不同 → 改为 CHANGED"""
    hist = {"a": 1}
    mod = {"a": 1, "b": 2}
    current = {"a": 1, "b": 99}

    delta = _compute_delta(hist, mod, "config", merge_mode=MergeMode.SMART)
    assert delta is not None

    remapped = _remap_delta_to_current(delta, hist, current)
    assert remapped is not None
    entry = remapped.items.get("b")
    assert isinstance(entry, FieldDiff), f"b 应为 FieldDiff，实际 {type(entry)}"
    assert_eq(entry.kind.base_kind, ChangeKind.CHANGED, "b 应改为 CHANGED")
    assert_eq(entry.value, 2, "新值应为 2")


def test_dict_changed_already_equal() -> None:
    """dict 字段：delta CHANGED 的 key 在当前 base 中已等于新值 → 丢弃"""
    hist = {"a": 1}
    mod = {"a": 2}
    current = {"a": 2}  # 游戏已经把 a 改成 2

    delta = _compute_delta(hist, mod, "config", merge_mode=MergeMode.SMART)
    assert delta is not None

    remapped = _remap_delta_to_current(delta, hist, current)
    if remapped is None:
        return
    assert_true("a" not in remapped.items, "a 已等值，应丢弃")


def test_real_bug_condition_field() -> None:
    """复现真实 bug：rite/5000002.json guid=f2dde237 的 condition 字段

    历史 base:  {s1.is, have.2000056}
    Mod:        {s1.is, have.2000056.追随者}
    当前 base:  {s1.is, table_have.2000056, have.2000056.追随者}

    Bug: 直接应用历史 base delta 会产生 have.2000056: DELETED(null) 和
         have.2000056.追随者: ADDED 的幽灵变更。
    重映射后：两个变更都应消失（当前 base 中前者已不存在、后者已存在且值相同）。
    """
    guid = "f2dde237-b19d-40ab-94f2-8292381277aa"

    hist_elem = {
        "guid": guid,
        "condition": {
            "s1.is": 2000757,
            "have.2000056": 1,
        },
        "result_title": "",
        "result_text": "",
        "result": {},
        "action": {"event_on": 5321035},
    }
    mod_elem = {
        "guid": guid,
        "condition": {
            "s1.is": 2000757,
            "have.2000056.追随者": 1,
        },
        "result_title": "",
        "result_text": "",
        "result": {},
        "action": {"event_on": 5321035},
    }
    current_elem = {
        "guid": guid,
        "condition": {
            "s1.is": 2000757,
            "table_have.2000056": 1,
            "have.2000056.追随者": 1,
        },
        "result_title": "",
        "result_text": "",
        "result": {},
        "action": {"event_on": 5321035},
    }

    # 包装在 settlement 数组里，模拟 entity 文件结构
    hist = {"id": "5000002", "settlement": [hist_elem]}
    mod = {"id": "5000002", "settlement": [mod_elem]}
    current = {"id": "5000002", "settlement": [current_elem]}

    delta = _compute_delta(hist, mod, "entity", merge_mode=MergeMode.NORMAL)
    assert delta is not None, "原始 delta 应非空"

    cond_delta = _condition_diff_for_guid(delta, guid)
    assert cond_delta is not None, "原始 delta 中应含 condition 子 delta"
    assert_true("have.2000056" in cond_delta.items,
                "原始 delta condition 含 have.2000056: DELETED")
    assert_true("have.2000056.追随者" in cond_delta.items,
                "原始 delta condition 含 have.2000056.追随者: ADDED")

    remapped = _remap_delta_to_current(delta, hist, current)
    if remapped is None:
        return  # 整个 delta 清空也算修复

    cond_remap = _condition_diff_for_guid(remapped, guid)
    if cond_remap is None:
        return  # condition 子 delta 全部清空也是修复

    assert_true("have.2000056" not in cond_remap.items,
                f"have.2000056 应被丢弃（当前 base 不存在），实际 {cond_remap.items}")
    assert_true("have.2000056.追随者" not in cond_remap.items,
                f"have.2000056.追随者 应被丢弃（当前 base 已存在），实际 {cond_remap.items}")


def test_array_index_remap() -> None:
    """数组：当前 base 中元素位置变化，element ID 应被重映射

    历史 base:  [A, B, C]
    Mod:        [A, B', C]  （改了 B）
    当前 base:  [A, X, B, C]  （游戏在 B 前插入了 X）

    历史 delta 中 B 的 ID = 2，应重映射为当前 base 中 B 的 ID = 3
    """
    hist = {
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "b", "v": 2},
            {"guid": "c", "v": 3},
        ],
    }
    mod = {
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "b", "v": 99},  # 改了
            {"guid": "c", "v": 3},
        ],
    }
    current = {
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "x", "v": 50},  # 游戏插入的新元素
            {"guid": "b", "v": 2},
            {"guid": "c", "v": 3},
        ],
    }

    delta = _compute_delta(hist, mod, "entity", merge_mode=MergeMode.SMART)
    assert delta is not None
    settlement_delta = delta.items.get("settlement")
    assert isinstance(settlement_delta, ArrayFieldDiff)
    # 历史 delta 中 B 的 ID 应为 2 (1-based, 历史 base 第 2 个)
    log.info("    历史 delta indices: %s", settlement_delta.indices)
    assert_eq(settlement_delta.indices, [2], "历史 delta 应只含 B 的 CHANGED, ID=2")

    remapped = _remap_delta_to_current(delta, hist, current)
    assert remapped is not None
    settlement_remap = remapped.items.get("settlement")
    assert isinstance(settlement_remap, ArrayFieldDiff)
    log.info("    重映射后 indices: %s", settlement_remap.indices)
    log.info("    重映射后 base_count: %s", settlement_remap.base_count)
    # 当前 base 中 B 的位置是 index 2 (0-based) → 1-based ID = 3
    assert_eq(settlement_remap.indices, [3],
              "重映射后 B 的 ID 应为 3（当前 base 中 B 的位置）")
    assert_eq(settlement_remap.base_count, 4,
              "base_count 应更新为当前数组长度 4")


def test_array_changed_element_disappeared() -> None:
    """数组：mod CHANGED 的元素在当前 base 中已被游戏删除 → 丢弃此项"""
    hist = {
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "b", "v": 2},
        ],
    }
    mod = {
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "b", "v": 99},
        ],
    }
    current = {
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},  # b 被游戏删了
        ],
    }

    delta = _compute_delta(hist, mod, "entity", merge_mode=MergeMode.SMART)
    assert delta is not None

    remapped = _remap_delta_to_current(delta, hist, current)
    if remapped is None:
        return
    settlement_remap = remapped.items.get("settlement")
    if settlement_remap is None:
        return
    assert isinstance(settlement_remap, ArrayFieldDiff)
    # b 已不存在，对它的 CHANGED 应被丢弃
    assert_eq(len(settlement_remap.diffs), 0,
              "b 已被当前 base 删除，CHANGED 应被丢弃")


def test_array_order_preserves_origin() -> None:
    """order 重建必须保留 ORIGIN 元素：mod 只改 B，A/C/D 仍应出现在重映射后的 order 中"""
    hist = {
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "b", "v": 2},
            {"guid": "c", "v": 3},
            {"guid": "d", "v": 4},
        ],
    }
    mod = {
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "b", "v": 99},  # 改 B
            {"guid": "c", "v": 3},
            {"guid": "d", "v": 4},
        ],
    }
    current = {
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "x", "v": 50},  # 游戏插入的新元素
            {"guid": "b", "v": 2},
            {"guid": "c", "v": 3},
            {"guid": "d", "v": 4},
        ],
    }

    delta = _compute_delta(hist, mod, "entity", merge_mode=MergeMode.SMART)
    assert delta is not None
    remapped = _remap_delta_to_current(delta, hist, current)
    assert remapped is not None
    settlement_remap = remapped.items.get("settlement")
    assert isinstance(settlement_remap, ArrayFieldDiff)

    log.info("    order: %s", settlement_remap.order)
    # order 应包含 A(1), B(3), C(4), D(5)，以及边界 0/-1；X(2) 不含
    inner = [x for x in settlement_remap.order if x not in (0, -1)]
    assert_eq(inner, [1, 3, 4, 5], "order 应按 mod 顺序保留所有配对元素的当前 ID")


def test_array_large_origin_preserved() -> None:
    """大数组：10 个元素只改 1 个，order 长度应保留（不被丢弃）"""
    hist_items = [{"guid": f"g{i}", "v": i} for i in range(10)]
    mod_items = [{"guid": f"g{i}", "v": i} for i in range(10)]
    mod_items[5]["v"] = 999  # 只改第 6 个

    hist = {"id": "x", "settlement": hist_items}
    mod = {"id": "x", "settlement": mod_items}
    # current 在位置 3 插入一个新元素
    current_items = [{"guid": f"g{i}", "v": i} for i in range(10)]
    current_items.insert(3, {"guid": "new", "v": -1})
    current = {"id": "x", "settlement": current_items}

    delta = _compute_delta(hist, mod, "entity", merge_mode=MergeMode.SMART)
    assert delta is not None
    remapped = _remap_delta_to_current(delta, hist, current)
    assert remapped is not None
    settlement_remap = remapped.items.get("settlement")
    assert isinstance(settlement_remap, ArrayFieldDiff)

    inner = [x for x in settlement_remap.order if x not in (0, -1)]
    log.info("    inner order len=%d: %s", len(inner), inner)
    assert_eq(len(inner), 10, "10 个历史元素应全部出现在重映射后的 order 中")


def run_all(result: TestResult) -> None:
    run_test("dict_deleted_missing_in_current",
             test_dict_deleted_missing_in_current, result)
    run_test("dict_added_already_present",
             test_dict_added_already_present, result)
    run_test("dict_added_present_diff_value",
             test_dict_added_present_diff_value, result)
    run_test("dict_changed_already_equal",
             test_dict_changed_already_equal, result)
    run_test("real_bug_condition_field",
             test_real_bug_condition_field, result)
    run_test("array_index_remap",
             test_array_index_remap, result)
    run_test("array_changed_element_disappeared",
             test_array_changed_element_disappeared, result)
    run_test("array_order_preserves_origin",
             test_array_order_preserves_origin, result)
    run_test("array_large_origin_preserved",
             test_array_large_origin_preserved, result)
