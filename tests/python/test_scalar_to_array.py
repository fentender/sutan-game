"""
标量→数组合并测试 - 验证字段从标量值变为数组时合并和展示的正确性

复现场景：游戏本体 cards.json card "2000523" 的 resource 字段为字符串
"cards/2000523"，Mod 将其修改为数组 ["cards/2000523", "cards/2000523_1"]。
合并后应正确体现为数组，diff 面板应显示新增元素。
"""
import json as _json

from sultan_core.json import JsonDoc
from sultan_core.state import MergeMode as CppMergeMode
from sultan_core.delta import compute_delta, remap_delta, serialize_delta, deserialize_delta

from src.core.merge.formatter import format_delta_json
from src.core.merge.merger import apply_dict_delta
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
    base_data: dict[str, object],
    mod_data: dict[str, object],
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


def _remap_delta_to_current(
    delta: DictFieldDiff,
    hist_base: dict[str, object],
    current_base: dict[str, object],
) -> DictFieldDiff | None:
    """_remap_delta_to_current 的 C++ API 替代"""
    delta_doc = JsonDoc.parse(_json.dumps(delta.to_delta_dict()))
    delta_node = deserialize_delta(delta_doc)
    if delta_node is None:
        return None
    hist_doc = JsonDoc.parse(_json.dumps(hist_base))
    current_doc = JsonDoc.parse(_json.dumps(current_base))
    remapped_node = remap_delta(delta_node, hist_doc, current_doc)
    if remapped_node is not None:
        doc = serialize_delta(remapped_node)
        return DictFieldDiff.from_delta_dict(_json.loads(doc.to_string()))
    return None


def _make_base() -> JsonObject:
    """游戏本体 card 条目：resource 为字符串"""
    return {
        "id": 2000523,
        "name": "法拉杰",
        "resource": "cards/2000523",
        "rare": 4,
    }


def _make_mod() -> JsonObject:
    """Mod 修改后的 card 条目：resource 为数组"""
    return {
        "id": 2000523,
        "name": "法拉杰",
        "resource": ["cards/2000523", "cards/2000523_1"],
        "rare": 4,
    }


def test_scalar_to_array_delta() -> None:
    """compute_delta 对标量→数组字段应产出 ArrayFieldDiff"""
    base = {"2000523": _make_base()}
    mod = {"2000523": _make_mod()}
    delta = _compute_delta(base, mod, file_type="dictionary", merge_mode=MergeMode.NORMAL)

    assert_true(delta is not None, "delta 不应为 None")
    assert_true(isinstance(delta, DictFieldDiff), "delta 应为 DiffDict")

    # 顶层 key "2000523" 的 delta
    entry_diff = delta.items.get("2000523")
    assert_true(isinstance(entry_diff, DictFieldDiff), "条目 delta 应为 DiffDict")
    assert isinstance(entry_diff, DictFieldDiff)

    # resource 字段的 delta 应为 ArrayFieldDiff
    res_diff = entry_diff.items.get("resource")
    assert_true(
        isinstance(res_diff, ArrayFieldDiff),
        f"resource delta 应为 ArrayFieldDiff，实际为 {type(res_diff).__name__}",
    )
    assert isinstance(res_diff, ArrayFieldDiff)

    # base_count=1（原标量归一化为单元素数组）
    assert_eq(res_diff.base_count, 1, "base_count")

    # 应有一个 ADDED 元素
    added = [d for d in res_diff.diffs if d.kind == ChangeKind.ADDED]
    assert_eq(len(added), 1, "ADDED 元素数量")
    assert_eq(added[0].value, "cards/2000523_1", "ADDED 元素值")


def test_scalar_to_array_apply() -> None:
    """apply_delta 后全状态的 resource 应为含 2 个元素的 ArrayFieldDiff"""
    base_data = {"2000523": _make_base()}
    mod_data = {"2000523": _make_mod()}
    delta = _compute_delta(base_data, mod_data, file_type="dictionary", merge_mode=MergeMode.NORMAL)
    assert_true(delta is not None, "delta 不应为 None")
    assert isinstance(delta, DictFieldDiff)

    # 创建全状态并应用 delta
    full_state = DictFieldDiff.from_dict(base_data)
    apply_dict_delta(full_state, delta, version=1)

    # 提取 resource 字段
    entry = full_state.items.get("2000523")
    assert_true(isinstance(entry, DictFieldDiff), "全状态条目应为 DiffDict")
    assert isinstance(entry, DictFieldDiff)

    resource = entry.items.get("resource")
    assert_true(
        isinstance(resource, ArrayFieldDiff),
        f"合并后 resource 应为 ArrayFieldDiff，实际为 {type(resource).__name__}",
    )
    assert isinstance(resource, ArrayFieldDiff)

    # 还原为 list 验证内容
    result_list = resource.to_list()
    assert_eq(len(result_list), 2, "合并后数组元素数")
    assert_eq(result_list[0], "cards/2000523", "第一个元素")
    assert_eq(result_list[1], "cards/2000523_1", "第二个元素")

    # 验证原始元素为 ORIGIN，新增元素为 ADDED
    id_to_diff = dict(zip(resource.indices, resource.diffs))
    origins = [d for d in id_to_diff.values() if d.kind.base_kind == ChangeKind.ORIGIN]
    addeds = [d for d in id_to_diff.values() if d.kind.base_kind == ChangeKind.ADDED]
    assert_eq(len(origins), 1, "ORIGIN 元素数")
    assert_eq(len(addeds), 1, "ADDED 元素数")


def test_scalar_to_array_format() -> None:
    """format_delta_json 应正确标记标量→数组的变更"""
    base_data = {"2000523": _make_base()}
    mod_data = {"2000523": _make_mod()}
    delta = _compute_delta(base_data, mod_data, file_type="dictionary", merge_mode=MergeMode.NORMAL)
    assert_true(delta is not None, "delta 不应为 None")
    assert isinstance(delta, DictFieldDiff)

    full_state = DictFieldDiff.from_dict(base_data)
    apply_dict_delta(full_state, delta, version=1)

    # 格式化输出
    left_lines, right_lines, left_kinds, right_kinds = format_delta_json(full_state, highlight_version=1)

    # 右侧应包含新增元素文本
    right_text = '\n'.join(right_lines)
    assert_true(
        "cards/2000523_1" in right_text,
        "右侧输出应包含新增元素 cards/2000523_1",
    )

    # 应有至少一行标记为 ADDED
    has_added = any(k is not None and k.is_added for k in right_kinds)
    assert_true(has_added, "应有 ADDED 高亮行")


def test_scalar_to_array_adaptive_remap() -> None:
    """ADAPTIVE 模式：_remap_delta_to_current 不应丢弃标量→数组的 ArrayFieldDiff"""
    hist_base = {"2000523": _make_base()}   # resource = "cards/2000523" (字符串)
    current_base = {"2000523": _make_base()}  # 当前 base 相同
    mod_data = {"2000523": _make_mod()}       # resource = ["cards/2000523", "cards/2000523_1"]

    # 基于历史 base 计算 delta（模拟 ADAPTIVE 流程）
    delta = _compute_delta(hist_base, mod_data, file_type="dictionary", merge_mode=MergeMode.SMART)
    assert_true(delta is not None, "delta 不应为 None")
    assert isinstance(delta, DictFieldDiff)

    # 重映射到当前 base
    remapped = _remap_delta_to_current(delta, hist_base, current_base)
    assert_true(remapped is not None, "重映射后 delta 不应为 None（标量→数组不应被丢弃）")
    assert isinstance(remapped, DictFieldDiff)

    entry_r = remapped.items.get("2000523")
    assert_true(
        isinstance(entry_r, DictFieldDiff),
        f"重映射后条目应为 DiffDict，实际为 {type(entry_r).__name__ if entry_r else 'None'}",
    )
    assert isinstance(entry_r, DictFieldDiff)

    res_r = entry_r.items.get("resource")
    assert_true(
        isinstance(res_r, ArrayFieldDiff),
        f"重映射后 resource 应为 ArrayFieldDiff，实际为 {type(res_r).__name__ if res_r else 'None (被丢弃)'}",
    )
    assert isinstance(res_r, ArrayFieldDiff)

    # 验证 ADDED 元素保留
    added = [d for d in res_r.diffs if d.kind == ChangeKind.ADDED]
    assert_eq(len(added), 1, "ADDED 元素数量")
    assert_eq(added[0].value, "cards/2000523_1", "ADDED 元素值")


def run_all(result: TestResult) -> None:
    run_test("标量→数组: delta 计算", test_scalar_to_array_delta, result)
    run_test("标量→数组: apply 应用", test_scalar_to_array_apply, result)
    run_test("标量→数组: format 渲染", test_scalar_to_array_format, result)
    run_test("标量→数组: ADAPTIVE remap", test_scalar_to_array_adaptive_remap, result)
