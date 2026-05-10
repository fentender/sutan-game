#include "apply_delta.h"
#include "json_state.h"
#include "perf.h"
#include <algorithm>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

namespace sultan {

using std::make_unique;
using std::string;
using std::vector;
using std::unordered_map;
using std::unordered_set;

// ── apply_field_delta ──

StateNodePtr apply_field_delta(
    const DeltaElement& diff,
    JsonElementState* existing,
    int version,
    bool is_override)
{
    ChangeKind base_kind = sultan::base_kind(diff.kind_);

    ChangeKind modifier = ChangeKind::Origin;
    if (is_override)
        modifier |= ChangeKind::Override;

    ScalarValue old_val = nullptr;
    if (existing) {
        old_val = existing->value;
        if (existing->is_modified() && existing->version != version)
            modifier |= ChangeKind::MultiMod;
        modifier |= change_flags(existing->kind_);
    }

    ChangeKind kind = base_kind | modifier;

    auto result = make_unique<JsonElementState>();
    result->kind_ = kind;
    result->value = diff.value;
    result->old_value = std::move(old_val);
    result->version = version;
    return result;
}

// ── _prepare_array_delta ──

struct PreparedDelta {
    vector<DeltaNodePtr> diffs;
    vector<int> indices;
    vector<int> order;
};

static PreparedDelta prepare_array_delta(
    const JsonArrayState& base,
    const DeltaArray& delta,
    bool is_override)
{
    unordered_map<int, int> id_remap;

    if (is_override) {
        vector<int> flat_order;
        for (int eid : base.order)
            if (eid != 0 && eid != -1) flat_order.push_back(eid);
        for (int pos = 0; pos < static_cast<int>(flat_order.size()); ++pos)
            id_remap[pos + 1] = flat_order[pos];
    } else {
        int cur_max = 0;
        for (int id : base.indices)
            if (id > cur_max) cur_max = id;
        int next_id = cur_max + 1;

        for (size_t i = 0; i < delta.diffs.size(); ++i) {
            int orig_id = delta.indices[i];
            auto& d = delta.diffs[i];
            if (d->type() == DeltaType::Element &&
                sultan::base_kind(d->kind()) == ChangeKind::Added) {
                id_remap[orig_id] = next_id++;
            } else if (orig_id > delta.base_count) {
                throw std::runtime_error(
                    "CHANGED/DELETED ID exceeds base_count");
            }
        }
    }

    PreparedDelta result;
    for (auto& d : delta.diffs)
        result.diffs.push_back(d->clone());
    result.indices.reserve(delta.indices.size());
    for (int id : delta.indices) {
        auto it = id_remap.find(id);
        result.indices.push_back(it != id_remap.end() ? it->second : id);
    }
    result.order.reserve(delta.order.size());
    for (int id : delta.order) {
        auto it = id_remap.find(id);
        result.order.push_back(it != id_remap.end() ? it->second : id);
    }
    return result;
}

// ── _rebuild_array_order ──

static vector<int> rebuild_array_order(
    const JsonArrayState& base,
    const vector<int>& delta_order)
{
    unordered_set<int> delta_set(delta_order.begin(), delta_order.end());
    unordered_set<int> base_id_set(base.indices.begin(), base.indices.end());

    // orphans: base 中有但 delta_order 中没有的元素
    vector<int> orphans;
    for (int id : base.indices) {
        if (delta_set.count(id) == 0) orphans.push_back(id);
    }
    unordered_set<int> orphan_set(orphans.begin(), orphans.end());

    // 构建 after map：orphan 跟在其最近的锚点之后
    unordered_map<int, vector<int>> after;
    int last_anchor = 0;
    for (int eid : base.order) {
        if (orphan_set.count(eid)) {
            after[last_anchor].push_back(eid);
        } else {
            last_anchor = eid;
        }
    }

    vector<int> new_order;
    for (int eid : delta_order) {
        new_order.push_back(eid);
        auto it = after.find(eid);
        if (it != after.end()) {
            for (int ob : it->second)
                new_order.push_back(ob);
        }
    }
    return new_order;
}

// ── 前置声明 ──

static StateNodePtr apply_delta_entry(
    const DeltaBase& diff,
    StateBase* existing,
    vector<string>* field_path,
    int version,
    bool is_override);

// ── apply_array_delta ──

void apply_array_delta(
    JsonArrayState& base,
    const DeltaArray& delta,
    vector<string>* field_path,
    int version,
    bool is_override)
{
    base.kind_ = delta.kind_;
    auto prepared = prepare_array_delta(base, delta, is_override);

    unordered_map<int, int> id_map;
    for (int i = 0; i < static_cast<int>(base.indices.size()); ++i)
        id_map[base.indices[i]] = i;

    for (size_t i = 0; i < prepared.diffs.size(); ++i) {
        int elem_id = prepared.indices[i];
        auto it = id_map.find(elem_id);
        StateBase* existing = (it != id_map.end()) ? base.diffs[it->second].get() : nullptr;

        auto applied = apply_delta_entry(*prepared.diffs[i], existing, field_path, version, is_override);
        if (!applied) continue;

        if (it == id_map.end()) {
            base.diffs.push_back(std::move(applied));
            base.indices.push_back(elem_id);
        } else {
            base.diffs[it->second] = std::move(applied);
        }
    }

    base.old_order = base.order;
    base.order = rebuild_array_order(base, prepared.order);
}

// ── apply_dict_delta ──

void apply_dict_delta(
    JsonDictState& base,
    const DeltaDict& delta,
    vector<string>* field_path,
    int version,
    bool is_override)
{
    base.kind_ = delta.kind_;

    for (auto& [key, diff] : delta.items) {
        if (field_path) field_path->push_back(key);

        StateBase* existing = base.find(key);

        // DupList 归一化
        bool existing_dup = existing && existing->is_array() &&
                            existing->as_array().is_duplist;
        bool diff_dup = diff->type() == DeltaType::Array &&
                        diff->as_array().is_duplist;
        if (existing_dup && !diff_dup) {
            // 将非 duplist diff 包装为 duplist DeltaArray
            // （简化处理：直接应用）
        }
        if (diff_dup && !existing_dup && existing) {
            // 将 existing 包装为 duplist JsonArrayState
            auto wrapper = make_unique<JsonArrayState>();
            wrapper->is_duplist = true;
            wrapper->base_count = 1;
            auto existing_node = base.entries[key]->clone();
            wrapper->diffs.push_back(std::move(existing_node));
            wrapper->indices.push_back(1);
            wrapper->order = {0, 1, -1};
            base.insert(key, std::move(wrapper));
            existing = base.find(key);
        }

        auto applied = apply_delta_entry(*diff, existing, field_path, version, is_override);
        if (applied)
            base.insert(key, std::move(applied));
        if (field_path) field_path->pop_back();
    }
}

// ── _apply_delta_entry ──

static StateNodePtr apply_delta_entry(
    const DeltaBase& diff,
    StateBase* existing,
    vector<string>* field_path,
    int version,
    bool is_override)
{
    // 类型归一化：DeltaElement(DELETED) + existing 是 dict/array → 展开
    if (diff.type() == DeltaType::Element &&
        sultan::base_kind(diff.kind()) == ChangeKind::Deleted &&
        existing != nullptr)
    {
        if (existing->is_dict()) {
            auto& dict = existing->as_dict();
            for (auto& [k, v] : dict.entries) {
                auto elem = make_unique<JsonElementState>();
                elem->kind_ = ChangeKind::Deleted;
                elem->old_value = (v->is_element()) ? v->as_element().value : ScalarValue{nullptr};
                elem->version = version;
                dict.entries[k] = std::move(elem);
            }
            dict.kind_ = ChangeKind::Deleted;
            return existing->clone();
        }
        if (existing->is_array()) {
            auto& arr = existing->as_array();
            for (auto& d : arr.diffs) {
                if (d->is_element()) {
                    d->as_element().old_value = d->as_element().value;
                    d->as_element().kind_ = ChangeKind::Deleted;
                    d->as_element().version = version;
                }
            }
            arr.kind_ = ChangeKind::Deleted;
            return existing->clone();
        }
    }

    // DeltaArray + existing 是 element → wrap existing
    if (diff.type() == DeltaType::Array && existing && existing->is_element()) {
        auto wrapper = make_unique<JsonArrayState>();
        wrapper->is_duplist = diff.as_array().is_duplist;
        wrapper->base_count = 1;
        wrapper->diffs.push_back(existing->clone());
        wrapper->indices.push_back(1);
        wrapper->order = {0, 1, -1};
        apply_array_delta(*wrapper, diff.as_array(), field_path, version, is_override);
        return wrapper;
    }

    // DeltaElement + existing 是 array → wrap diff
    if (diff.type() == DeltaType::Element && existing && existing->is_array()) {
        auto darr = make_unique<DeltaArray>();
        darr->is_duplist = existing->as_array().is_duplist;
        darr->base_count = 1;
        darr->diffs.push_back(diff.clone());
        darr->indices.push_back(1);
        darr->order = {0, 1, -1};
        auto result = existing->clone();
        apply_array_delta(result->as_array(), *darr, field_path, version, is_override);
        return result;
    }

    // 类型不匹配检查
    if (existing != nullptr) {
        bool type_match =
            (diff.type() == DeltaType::Element && existing->is_element()) ||
            (diff.type() == DeltaType::Dict && existing->is_dict()) ||
            (diff.type() == DeltaType::Array && existing->is_array());
        if (!type_match)
            return nullptr;
    }

    // DeltaElement
    if (diff.type() == DeltaType::Element) {
        auto& elem = diff.as_element();

        // ADDED 且 existing 为空：创建新 state 节点
        if (!existing && sultan::base_kind(elem.kind_) == ChangeKind::Added) {
            if (elem.value_node) {
                auto node = elem.value_node->clone();
                // 标记为 ADDED
                if (node->is_element()) {
                    node->as_element().kind_ = ChangeKind::Added;
                    node->as_element().version = version;
                } else if (node->is_dict()) {
                    node->as_dict().kind_ = ChangeKind::Added;
                } else if (node->is_array()) {
                    node->as_array().kind_ = ChangeKind::Added;
                }
                return node;
            }
            return apply_field_delta(elem,
                existing ? &existing->as_element() : nullptr,
                version, is_override);
        }

        JsonElementState* exist_elem = (existing && existing->is_element())
            ? &existing->as_element() : nullptr;
        return apply_field_delta(elem, exist_elem, version, is_override);
    }

    // DeltaDict
    if (diff.type() == DeltaType::Dict) {
        if (!existing) return nullptr;
        auto result = existing->clone();
        apply_dict_delta(result->as_dict(), diff.as_dict(), field_path, version, is_override);
        return result;
    }

    // DeltaArray
    if (diff.type() == DeltaType::Array) {
        if (!existing) return nullptr;
        auto result = existing->clone();
        apply_array_delta(result->as_array(), diff.as_array(), field_path, version, is_override);
        return result;
    }

    return nullptr;
}

// ── apply_delta_to_state ──

void apply_delta_to_state(
    JsonState& state,
    const DeltaDict& delta,
    vector<string>* field_path,
    int version,
    bool is_override)
{
    SULTAN_PERF_SCOPE("apply_delta");
    if (!state.valid())
        throw std::runtime_error("apply_delta_to_state: invalid state");
    if (!state.root().is_dict())
        throw std::runtime_error("apply_delta_to_state: root must be dict");
    apply_dict_delta(state.root().as_dict(), delta, field_path, version, is_override);
}

}  // namespace sultan
