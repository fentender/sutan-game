"""
嵌套数组（Vector2 anchors）合并测试

复现 bug：rite/5002004.json 中 begin_guide.anchors = [[0.5,0.5],[0.5,0.5],[0.5,0.5]]
经 ADAPTIVE 模式合并后变为 [[0.5],[0.5,0.5],[0.5,0.5]]——第一个内层数组丢失元素。
"""
import copy
import json
import logging
from pathlib import Path

from src.config import SCHEMA_DIR
from src.core.merge.delta import ModDelta, compute_delta, _remap_delta_to_current
from src.core.merge.merger import apply_dict_delta
from src.core.infra.types import DictFieldDiff, MergeMode
from src.core.schema.loader import load_schemas, resolve_schema, get_schema_root_key
from tests.test_runner import TestResult, assert_eq, assert_true, run_test, skip

FIXTURES_DIR = Path(__file__).parent / "fixtures"

log = logging.getLogger("test")


def _make_base() -> dict:
    """构造 5002004.json 简化结构（entity 文件，含嵌套数组 anchors）"""
    return {
        "id": "5002004",
        "cards_slot": {
            "s4": {
                "pops": [
                    {
                        "guid": "pop-0",
                        "condition": {"s1.is": 100},
                        "action": {"event_on": 9000},
                    },
                    {
                        "guid": "pop-1",
                        "condition": {"s1.is": 200},
                        "action": {
                            "event_on": 9001,
                            "begin_guide": {
                                "type": "FILL_COIN",
                                "anim_type": "MouseRightClick",
                                "bind": "UI/Submit",
                                "pos": [-1024, -404],
                                "is_show_ring": True,
                                "ring_pos": [1182, -249.5],
                                "anchors": [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]],
                                "is_on_in_mobile": True,
                            },
                        },
                    },
                ],
            },
        },
    }


def _make_mod(base: dict) -> dict:
    """构造 mod 版本：改动 begin_guide 中的其他字段，anchors 不变"""
    mod = copy.deepcopy(base)
    guide = mod["cards_slot"]["s4"]["pops"][1]["action"]["begin_guide"]
    guide["anim_type"] = "MouseLeftClick"
    guide["pos"] = [-800, -300]
    guide["is_show_ring"] = False
    return mod


def test_nested_array_anchors_normal() -> None:
    """NORMAL 模式：合并后 anchors 应保持 [[0.5,0.5],[0.5,0.5],[0.5,0.5]]"""
    base_data = _make_base()
    mod_data = _make_mod(base_data)

    delta = compute_delta(base_data, mod_data, "entity", merge_mode=MergeMode.NORMAL)
    assert_true(delta is not None, "delta 应非空")
    assert delta is not None

    full_state = DictFieldDiff.from_dict(base_data)
    apply_dict_delta(full_state, delta, version=1)
    result = full_state.to_dict()

    anchors = result["cards_slot"]["s4"]["pops"][1]["action"]["begin_guide"]["anchors"]
    log.info("    NORMAL anchors: %s", anchors)
    assert_eq(anchors, [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]],
              "NORMAL 模式 anchors 应保持不变")


def test_nested_array_anchors_smart() -> None:
    """SMART 模式：合并后 anchors 应保持 [[0.5,0.5],[0.5,0.5],[0.5,0.5]]"""
    base_data = _make_base()
    mod_data = _make_mod(base_data)

    delta = compute_delta(base_data, mod_data, "entity", merge_mode=MergeMode.SMART)
    assert_true(delta is not None, "delta 应非空")
    assert delta is not None

    full_state = DictFieldDiff.from_dict(base_data)
    apply_dict_delta(full_state, delta, version=1)
    result = full_state.to_dict()

    anchors = result["cards_slot"]["s4"]["pops"][1]["action"]["begin_guide"]["anchors"]
    log.info("    SMART anchors: %s", anchors)
    assert_eq(anchors, [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]],
              "SMART 模式 anchors 应保持不变")


def test_nested_array_anchors_adaptive() -> None:
    """ADAPTIVE 全流程：compute_delta(SMART) → remap → apply → to_dict

    复现 bug：hist_base 和 current_base 的 anchors 完全相同，
    mod 只改了 begin_guide 的其他字段，合并后 anchors 应完整保留。
    """
    hist_base = _make_base()
    mod_data = _make_mod(hist_base)
    current_base = copy.deepcopy(hist_base)

    # ADAPTIVE 第一步：基于历史 base 计算 SMART delta
    delta = compute_delta(hist_base, mod_data, "entity", merge_mode=MergeMode.SMART)
    assert_true(delta is not None, "delta 应非空")
    assert delta is not None

    # ADAPTIVE 第二步：重映射到当前 base
    remapped = _remap_delta_to_current(delta, hist_base, current_base)
    assert_true(remapped is not None, "重映射后 delta 应非空")
    assert remapped is not None

    # ADAPTIVE 第三步：应用到当前 base
    full_state = DictFieldDiff.from_dict(current_base)
    apply_dict_delta(full_state, remapped, version=1)
    result = full_state.to_dict()

    anchors = result["cards_slot"]["s4"]["pops"][1]["action"]["begin_guide"]["anchors"]
    log.info("    ADAPTIVE anchors: %s", anchors)
    assert_eq(len(anchors), 3, "anchors 应有 3 个元素")
    for i, vec in enumerate(anchors):
        assert_eq(len(vec), 2, f"anchors[{i}] 应有 2 个元素（Vector2）")
        assert_eq(vec, [0.5, 0.5], f"anchors[{i}] 应为 [0.5, 0.5]")


def test_nested_array_anchors_adaptive_mod_changes_anchors() -> None:
    """ADAPTIVE：mod 修改了 anchors 的值，合并后应反映 mod 的修改"""
    hist_base = _make_base()
    mod_data = copy.deepcopy(hist_base)
    guide = mod_data["cards_slot"]["s4"]["pops"][1]["action"]["begin_guide"]
    guide["anchors"] = [[0.0, 1.0], [1.0, 0.0], [0.5, 0.5]]
    guide["anim_type"] = "MouseLeftClick"
    current_base = copy.deepcopy(hist_base)

    delta = compute_delta(hist_base, mod_data, "entity", merge_mode=MergeMode.NORMAL)
    assert_true(delta is not None, "delta 应非空")
    assert delta is not None

    remapped = _remap_delta_to_current(delta, hist_base, current_base)
    assert_true(remapped is not None, "重映射后 delta 应非空")
    assert remapped is not None

    full_state = DictFieldDiff.from_dict(current_base)
    apply_dict_delta(full_state, remapped, version=1)
    result = full_state.to_dict()

    anchors = result["cards_slot"]["s4"]["pops"][1]["action"]["begin_guide"]["anchors"]
    log.info("    ADAPTIVE mod-changed anchors: %s", anchors)
    assert_eq(len(anchors), 3, "anchors 应有 3 个元素")
    assert_eq(anchors[0], [0.0, 1.0], "anchors[0] 应为 mod 修改值")
    assert_eq(anchors[1], [1.0, 0.0], "anchors[1] 应为 mod 修改值")
    assert_eq(anchors[2], [0.5, 0.5], "anchors[2] 应保持不变")
    for i, vec in enumerate(anchors):
        assert_eq(len(vec), 2, f"anchors[{i}] 应有 2 个元素（Vector2）")


def _load_fixture(name: str) -> dict:
    """加载 tests/fixtures/ 下的 JSON fixture 文件"""
    path = FIXTURES_DIR / name
    if not path.exists():
        skip(f"fixture 文件不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _get_anchors(data: dict) -> list:
    """从 5002004.json 结构中提取 begin_guide.anchors（遍历 pops 找第一个含 anchors 的）"""
    pops = data["cards_slot"]["s4"]["pops"]
    for pop in pops:
        if isinstance(pop, dict):
            guide = pop.get("action", {}).get("begin_guide")
            if isinstance(guide, dict) and "anchors" in guide:
                return guide["anchors"]
    raise AssertionError("未找到 begin_guide.anchors")


def test_real_5002004_adaptive() -> None:
    """真实 5002004.json：历史 base 无 anchors，mod 无 anchors → anchors 应保留

    历史 base（config_2025.12.5）：begin_guide 有 ring_rect，无 anchors
    Mod 3532410790：begin_guide 与历史 base 相同（未改 begin_guide）
    当前 base：begin_guide 删了 ring_rect，新增 anchors/is_on_in_mobile/mobile_properties
    """
    hist_base = _load_fixture("5002004_hist.json")
    mod_data = _load_fixture("5002004_mod.json")
    current_base = _load_fixture("5002004_current.json")

    expected_anchors = _get_anchors(current_base)
    log.info("    当前 base anchors: %s", expected_anchors)

    delta = compute_delta(hist_base, mod_data, "entity", merge_mode=MergeMode.SMART)
    assert_true(delta is not None, "delta 应非空")
    assert delta is not None

    remapped = _remap_delta_to_current(delta, hist_base, current_base)
    if remapped is None:
        return

    full_state = DictFieldDiff.from_dict(current_base)
    apply_dict_delta(full_state, remapped, version=1)
    result = full_state.to_dict()

    anchors = _get_anchors(result)
    log.info("    合并后 anchors: %s", anchors)
    assert_eq(len(anchors), 3, "合并后 anchors 应有 3 个元素")
    for i, vec in enumerate(anchors):
        assert_eq(len(vec), 2, f"合并后 anchors[{i}] 应有 2 个元素（Vector2），实际 {vec}")


def test_real_5002004_adaptive_hist_with_anchors() -> None:
    """真实 5002004.json 复现 bug：历史 base 有 anchors，mod 无 anchors

    历史 base（config_2026.04.20）：begin_guide 有 anchors
    Mod 3532410790：begin_guide 无 anchors（基于更早版本制作）
    当前 base：begin_guide 有 anchors

    Bug: SMART delta 将 anchors 的内层数组 [0.5,0.5] 做 match_by_heuristic，
    导致部分内层元素被判为 DELETED，合并后 [[0.5,0.5],...] → [[0.5],...]
    """
    hist_base = _load_fixture("5002004_hist_new.json")
    mod_data = _load_fixture("5002004_mod.json")
    current_base = _load_fixture("5002004_current.json")

    schemas = load_schemas(SCHEMA_DIR)
    schema = resolve_schema("rite/5002004.json", schemas)
    root_key = get_schema_root_key(schema) if schema else None

    expected_anchors = _get_anchors(current_base)
    log.info("    当前 base anchors: %s", expected_anchors)

    delta = compute_delta(
        hist_base, mod_data, "entity",
        root_key=root_key, merge_mode=MergeMode.SMART,
    )
    assert_true(delta is not None, "delta 应非空")
    assert delta is not None

    remapped = _remap_delta_to_current(delta, hist_base, current_base)
    assert_true(remapped is not None, "重映射后 delta 应非空")
    assert remapped is not None

    full_state = DictFieldDiff.from_dict(current_base)
    fp: tuple[str, ...] | None = (root_key,) if root_key else None
    apply_dict_delta(full_state, remapped, fp, version=1)
    result = full_state.to_dict()

    pops = result["cards_slot"]["s4"]["pops"]
    has_anchors = False
    for pop in pops:
        if isinstance(pop, dict):
            guide = pop.get("action", {}).get("begin_guide")
            if isinstance(guide, dict) and "anchors" in guide:
                has_anchors = True
                break
    assert_true(not has_anchors, "anchors 应被正确删除（mod 中不含此字段）")


def run_all(result: TestResult) -> None:
    run_test("nested_array_anchors_normal",
             test_nested_array_anchors_normal, result)
    run_test("nested_array_anchors_smart",
             test_nested_array_anchors_smart, result)
    run_test("nested_array_anchors_adaptive",
             test_nested_array_anchors_adaptive, result)
    run_test("nested_array_anchors_adaptive_mod_changes",
             test_nested_array_anchors_adaptive_mod_changes_anchors, result)
    run_test("real_5002004_adaptive",
             test_real_5002004_adaptive, result)
    run_test("real_5002004_adaptive_hist_with_anchors",
             test_real_5002004_adaptive_hist_with_anchors, result)

if __name__ == "__main__":
    from tests.test_runner import TestResult
    result = TestResult()
    run_test("real_5002004_adaptive_hist_with_anchors",
             test_real_5002004_adaptive_hist_with_anchors, result)
    print(result.summary)
