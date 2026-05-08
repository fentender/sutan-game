#include "compute_delta.h"
#include "array_match.h"
#include "delta_rules.h"
#include "json_doc.h"
#include "json_val.h"
#include "json_state.h"
#include "mut_doc.h"
#include "mut_val.h"
#include "state_node.h"
#include <algorithm>
#include <optional>
#include <unordered_map>

namespace sultan {

using std::string;
using std::vector;
using std::unordered_map;
using std::make_unique;

// ── 创建 ADDED/DELETED DeltaElement，复杂值存入 value_node ──

static DeltaNodePtr make_added_element(JsonVal v) {
    auto p = make_unique<DeltaElement>();
    p->kind_ = ChangeKind::Added;
    if (v.is_obj() || v.is_arr()) {
        p->value_node = build_state_node(v);
    } else {
        p->value = val_to_scalar(v);
    }
    return p;
}

static DeltaNodePtr make_deleted_element(JsonVal v) {
    auto p = make_unique<DeltaElement>();
    p->kind_ = ChangeKind::Deleted;
    if (!v.is_obj() && !v.is_arr()) {
        p->old_value = val_to_scalar(v);
    }
    return p;
}

// ── 深度相等 ──

static bool deep_equal(JsonVal a, JsonVal b);

static bool obj_equal(JsonVal a, JsonVal b) {
    if (a.obj_size() != b.obj_size()) return false;
    auto it = a.obj_iter();
    JsonVal::ObjEntry e;
    while (it.next(e)) {
        JsonVal bv = b.obj_get(e.key);
        if (!bv.valid()) return false;
        if (!deep_equal(e.val, bv)) return false;
    }
    return true;
}

static bool arr_equal(JsonVal a, JsonVal b) {
    if (a.arr_size() != b.arr_size()) return false;
    auto ait = a.arr_iter();
    auto bit = b.arr_iter();
    JsonVal ae, be;
    while (ait.next(ae)) {
        if (!bit.next(be)) return false;
        if (!deep_equal(ae, be)) return false;
    }
    return true;
}

static bool deep_equal(JsonVal a, JsonVal b) {
    if (a.type() != b.type()) return false;
    switch (a.type()) {
        case JsonType::Null: return true;
        case JsonType::Bool: return a.get_bool() == b.get_bool();
        case JsonType::Int:  return a.get_int() == b.get_int();
        case JsonType::Real: return a.get_real() == b.get_real();
        case JsonType::Str:  return string(a.get_str()) == string(b.get_str());
        case JsonType::Obj:  return obj_equal(a, b);
        case JsonType::Arr:  return arr_equal(a, b);
    }
    return false;
}

// ── 重复键收集 ──

struct ObjKeys {
    struct KV { vector<JsonVal> vals; };
    vector<string> key_order;
    unordered_map<string, KV> entries;
    bool has_dup = false;

    static ObjKeys collect(JsonVal obj) {
        ObjKeys r;
        auto it = obj.obj_iter();
        JsonVal::ObjEntry e;
        while (it.next(e)) {
            string k(e.key, e.key_len);
            auto& kv = r.entries[k];
            if (kv.vals.empty()) r.key_order.push_back(k);
            else r.has_dup = true;
            kv.vals.push_back(e.val);
        }
        return r;
    }
};

static bool has_duplicate_keys(JsonVal obj) {
    unordered_map<string, int> counts;
    auto it = obj.obj_iter();
    JsonVal::ObjEntry e;
    while (it.next(e)) {
        string k(e.key, e.key_len);
        if (++counts[k] > 1) return true;
    }
    return false;
}

// ── order 列表构建 ──

static vector<int> build_order(
    const ArrayMatching& matching,
    const unordered_map<int, int>& added_id_map)
{
    unordered_map<int, int> paired_map;
    for (auto& [base_idx, mod_idx] : matching.pairs)
        paired_map[mod_idx] = base_idx + 1;

    int mod_max = -1;
    for (auto& [bi, mi] : matching.pairs)
        if (mi > mod_max) mod_max = mi;
    for (int mi : matching.unmatched_mod)
        if (mi > mod_max) mod_max = mi;
    int total_mod = mod_max + 1;

    vector<int> order = {0};
    for (int mi = 0; mi < total_mod; ++mi) {
        auto pit = paired_map.find(mi);
        if (pit != paired_map.end()) {
            order.push_back(pit->second);
        } else {
            auto ait = added_id_map.find(mi);
            if (ait != added_id_map.end())
                order.push_back(ait->second);
        }
    }
    order.push_back(-1);
    return order;
}

// ── 递归 delta 计算 ──

static DeltaNodePtr recursive_delta(
    JsonVal base, JsonVal mod,
    const vector<string>* field_path,
    MergeMode merge_mode);

static DeltaNodePtr array_delta_from_matching(
    const vector<JsonVal>& base_elems,
    const vector<JsonVal>& mod_elems,
    const ArrayMatching& matching,
    const vector<string>* field_path,
    bool is_duplist,
    MergeMode merge_mode)
{
    int base_count = static_cast<int>(base_elems.size());

    vector<DeltaNodePtr> diffs;
    vector<int> indices;
    int next_added_id = base_count + 1;

    for (auto& [base_idx, mod_idx] : matching.pairs) {
        auto elem_delta = recursive_delta(
            base_elems[base_idx], mod_elems[mod_idx], field_path, merge_mode);
        if (elem_delta) {
            diffs.push_back(std::move(elem_delta));
            indices.push_back(base_idx + 1);
        }
    }

    unordered_map<int, int> added_id_map;
    for (int mod_idx : matching.unmatched_mod) {
        diffs.push_back(make_added_element(mod_elems[mod_idx]));
        indices.push_back(next_added_id);
        added_id_map[mod_idx] = next_added_id;
        next_added_id++;
    }

    if (merge_mode != MergeMode::Smart) {
        for (int base_idx : matching.unmatched_base) {
            diffs.push_back(make_deleted_element(base_elems[base_idx]));
            indices.push_back(base_idx + 1);
        }
    }

    if (diffs.empty())
        return nullptr;

    auto order = build_order(matching, added_id_map);
    auto arr = make_unique<DeltaArray>();
    arr->diffs = std::move(diffs);
    arr->base_count = base_count;
    arr->indices = std::move(indices);
    arr->order = std::move(order);
    arr->is_duplist = is_duplist;
    return arr;
}

static DeltaNodePtr recursive_delta(
    JsonVal base, JsonVal mod,
    const vector<string>* field_path,
    MergeMode merge_mode)
{
    if (deep_equal(base, mod))
        return nullptr;

    // dict vs dict
    if (base.is_obj() && mod.is_obj()) {
        bool base_dup = has_duplicate_keys(base);
        bool mod_dup = has_duplicate_keys(mod);

        if (base_dup || mod_dup) {
            auto base_keys = ObjKeys::collect(base);
            auto mod_keys = ObjKeys::collect(mod);

            auto dict = make_unique<DeltaDict>();
            for (auto& key : mod_keys.key_order) {
                auto& mod_vals = mod_keys.entries[key].vals;
                auto base_it = base_keys.entries.find(key);

                if (base_it == base_keys.entries.end()) {
                    if (mod_vals.size() == 1) {
                        dict->insert(key, make_added_element(mod_vals[0]));
                    }
                    continue;
                }

                auto& base_vals = base_it->second.vals;
                if (base_vals.size() > 1 || mod_vals.size() > 1) {
                    // DupList：按数组匹配处理
                    auto base_arr_v = base_vals;
                    auto mod_arr_v = mod_vals;
                    // 简化处理：逐位置比较
                    int bc = static_cast<int>(base_arr_v.size());
                    int mc = static_cast<int>(mod_arr_v.size());
                    auto darr = make_unique<DeltaArray>();
                    darr->is_duplist = true;
                    darr->base_count = bc;
                    darr->order.push_back(0);
                    int next_id = bc + 1;
                    for (int i = 0; i < std::max(bc, mc); ++i) {
                        if (i < bc && i < mc) {
                            auto sub = recursive_delta(base_arr_v[i], mod_arr_v[i], field_path, merge_mode);
                            if (sub) {
                                darr->diffs.push_back(std::move(sub));
                                darr->indices.push_back(i + 1);
                            }
                            darr->order.push_back(i + 1);
                        } else if (i >= bc) {
                            darr->diffs.push_back(make_added_element(mod_arr_v[i]));
                            darr->indices.push_back(next_id);
                            darr->order.push_back(next_id);
                            next_id++;
                        } else {
                            darr->diffs.push_back(make_deleted_element(base_arr_v[i]));
                            darr->indices.push_back(i + 1);
                        }
                    }
                    darr->order.push_back(-1);
                    if (!darr->diffs.empty())
                        dict->insert(key, std::move(darr));
                } else {
                    vector<string> cp;
                    vector<string>* child_path = nullptr;
                    if (field_path) {
                        cp = *field_path;
                        cp.push_back(key);
                        child_path = &cp;
                    }
                    auto sub = recursive_delta(base_vals[0], mod_vals[0], child_path, merge_mode);
                    if (sub) dict->insert(key, std::move(sub));
                }
            }

            for (auto& key : base_keys.key_order) {
                if (mod_keys.entries.count(key)) continue;
                if (merge_mode == MergeMode::Smart) {
                    vector<string> cp;
                    if (field_path) cp = *field_path;
                    cp.push_back(key);
                    if (!smart_allow_deletion(cp, false)) continue;
                }
                auto& bvals = base_keys.entries[key].vals;
                if (bvals.size() == 1)
                    dict->insert(key, make_deleted_element(bvals[0]));
            }
            if (dict->empty()) return nullptr;
            return dict;
        }

        // 普通 dict（无重复键）
        auto dict = make_unique<DeltaDict>();

        auto mod_it = mod.obj_iter();
        JsonVal::ObjEntry e;
        while (mod_it.next(e)) {
            string key(e.key, e.key_len);
            JsonVal base_val = base.obj_get(e.key);

            if (!base_val.valid()) {
                dict->insert(key, make_added_element(e.val));
            } else {
                vector<string> cp;
                vector<string>* child_path = nullptr;
                if (field_path) {
                    cp = *field_path;
                    cp.push_back(key);
                    child_path = &cp;
                }
                auto sub = recursive_delta(base_val, e.val, child_path, merge_mode);
                if (sub) dict->insert(key, std::move(sub));
            }
        }

        auto base_it = base.obj_iter();
        while (base_it.next(e)) {
            string key(e.key, e.key_len);
            JsonVal mod_val = mod.obj_get(e.key);
            if (mod_val.valid()) continue;

            if (merge_mode == MergeMode::Smart) {
                vector<string> cp;
                if (field_path) cp = *field_path;
                cp.push_back(key);
                if (!smart_allow_deletion(cp, false)) continue;
            }
            dict->insert(key, make_deleted_element(e.val));
        }

        if (dict->empty()) return nullptr;
        return dict;
    }

    // array vs array
    if (base.is_arr() && mod.is_arr()) {
        vector<JsonVal> base_elems, mod_elems;
        {
            auto it = base.arr_iter();
            JsonVal elem;
            while (it.next(elem)) base_elems.push_back(elem);
        }
        {
            auto it = mod.arr_iter();
            JsonVal elem;
            while (it.next(elem)) mod_elems.push_back(elem);
        }
        auto matching = match_by_heuristic(base, mod);
        return array_delta_from_matching(
            base_elems, mod_elems, matching, field_path, false, merge_mode);
    }

    // 标量变化
    auto mod_val = val_to_scalar(mod);
    return make_delta_element(ChangeKind::Changed, std::move(mod_val));
}

// ── 公开 API ──

DeltaNodePtr compute_delta(
    const JsonDoc& base,
    const JsonDoc& mod,
    MergeMode merge_mode)
{
    if (!base.valid() || !mod.valid())
        return nullptr;
    vector<string> root_path;
    vector<string>* fp = (merge_mode == MergeMode::Smart) ? &root_path : nullptr;
    return recursive_delta(base.root(), mod.root(), fp, merge_mode);
}

// ── remap: StateNodePtr → JsonDoc 转换 ──

static JsonDoc state_node_to_doc(const StateBase& node) {
    auto state = JsonState::from_node(node.clone());
    return state.to_doc();
}

// ── remap: 递归设置 delta 树的 version ──

static void set_delta_version(DeltaBase& node, int version) {
    switch (node.type()) {
        case DeltaType::Element:
            node.as_element().version = version;
            break;
        case DeltaType::Dict:
            for (auto& [k, v] : node.as_dict().items)
                set_delta_version(*v, version);
            break;
        case DeltaType::Array:
            for (auto& d : node.as_array().diffs)
                set_delta_version(*d, version);
            break;
    }
}

// ── remap: 标量 JsonVal 包装为单元素数组 JsonDoc（DupList 归一化用） ──

static JsonDoc wrap_scalar_as_array(JsonVal v) {
    MutDoc md;
    auto ctx = md.root();
    auto arr = ctx.new_arr();
    switch (v.type()) {
        case JsonType::Null: arr.arr_append(ctx.new_null()); break;
        case JsonType::Bool: arr.arr_append(ctx.new_bool(v.get_bool())); break;
        case JsonType::Int:  arr.arr_append(ctx.new_int(v.get_int())); break;
        case JsonType::Real: arr.arr_append(ctx.new_real(v.get_real())); break;
        case JsonType::Str:  arr.arr_append(ctx.new_str(string(v.get_str()))); break;
        default: arr.arr_append(ctx.new_null()); break;
    }
    md.set_root(arr);
    return md.freeze();
}

// ── remap 前置声明 ──

static DeltaNodePtr remap_dict_delta(
    const DeltaDict& delta, JsonVal hist_obj, JsonVal cur_obj);
static DeltaNodePtr remap_array_diff(
    const DeltaArray& delta, JsonVal hist_arr, JsonVal cur_arr);

// ── remap: 标量/叶子节点重映射 ──

static DeltaNodePtr remap_field_diff(
    const DeltaElement& entry,
    bool cur_has, JsonVal cur_val)
{
    ChangeKind bk = sultan::base_kind(entry.kind_);

    if (bk == ChangeKind::Deleted) {
        if (!cur_has) return nullptr;
        auto p = make_unique<DeltaElement>();
        p->kind_ = ChangeKind::Deleted;
        p->version = entry.version;
        if (!cur_val.is_obj() && !cur_val.is_arr())
            p->old_value = val_to_scalar(cur_val);
        return p;
    }

    if (bk == ChangeKind::Added) {
        if (!cur_has) return entry.clone();

        if (entry.value_node) {
            auto target_doc = state_node_to_doc(*entry.value_node);
            if (deep_equal(cur_val, target_doc.root())) return nullptr;
            auto result = recursive_delta(cur_val, target_doc.root(), nullptr, MergeMode::Normal);
            if (!result) return nullptr;
            set_delta_version(*result, entry.version);
            return result;
        }
        ScalarValue cur_sv = val_to_scalar(cur_val);
        if (scalar_equal(cur_sv, entry.value)) return nullptr;
        return make_delta_element(ChangeKind::Changed, ScalarValue{entry.value},
                                  std::move(cur_sv), entry.version);
    }

    if (bk == ChangeKind::Changed) {
        if (!cur_has) {
            auto p = make_unique<DeltaElement>();
            p->kind_ = ChangeKind::Added;
            p->value = entry.value;
            p->version = entry.version;
            if (entry.value_node) p->value_node = entry.value_node->clone();
            return p;
        }
        ScalarValue cur_sv = val_to_scalar(cur_val);
        if (scalar_equal(cur_sv, entry.value)) return nullptr;
        return make_delta_element(ChangeKind::Changed, ScalarValue{entry.value},
                                  std::move(cur_sv), entry.version);
    }

    return entry.clone();
}

// ── remap: 数组 delta 重映射（索引体系 hist→current） ──

static DeltaNodePtr remap_array_diff(
    const DeltaArray& delta,
    JsonVal hist_arr, JsonVal cur_arr)
{
    int hist_base_count = delta.base_count;
    int cur_base_count = static_cast<int>(cur_arr.arr_size());

    auto matching = match_by_heuristic(hist_arr, cur_arr);

    unordered_map<int, int> hist_to_cur;
    for (auto& [hi, ci] : matching.pairs)
        hist_to_cur[hi] = ci;

    vector<JsonVal> hist_elems, cur_elems;
    {
        auto it = hist_arr.arr_iter();
        JsonVal elem;
        while (it.next(elem)) hist_elems.push_back(elem);
    }
    {
        auto it = cur_arr.arr_iter();
        JsonVal elem;
        while (it.next(elem)) cur_elems.push_back(elem);
    }

    vector<DeltaNodePtr> new_diffs;
    vector<int> new_indices;
    unordered_map<int, int> id_remap;
    int added_counter = 0;

    for (size_t i = 0; i < delta.diffs.size(); ++i) {
        int eid = delta.indices[i];
        auto& d = delta.diffs[i];

        if (eid > hist_base_count) {
            added_counter++;
            int new_id = cur_base_count + added_counter;
            new_diffs.push_back(d->clone());
            new_indices.push_back(new_id);
            id_remap[eid] = new_id;
            continue;
        }

        int hist_idx = eid - 1;
        auto hit = hist_to_cur.find(hist_idx);
        if (hit == hist_to_cur.end()) continue;

        int cur_idx = hit->second;
        int new_id = cur_idx + 1;

        switch (d->type()) {
            case DeltaType::Dict: {
                if (hist_idx < static_cast<int>(hist_elems.size()) &&
                    cur_idx < static_cast<int>(cur_elems.size()) &&
                    hist_elems[hist_idx].is_obj() && cur_elems[cur_idx].is_obj()) {
                    auto sub = remap_dict_delta(d->as_dict(),
                        hist_elems[hist_idx], cur_elems[cur_idx]);
                    if (sub && !sub->as_dict().empty()) {
                        new_diffs.push_back(std::move(sub));
                        new_indices.push_back(new_id);
                        id_remap[eid] = new_id;
                    }
                }
                break;
            }
            case DeltaType::Array: {
                if (hist_idx < static_cast<int>(hist_elems.size()) &&
                    cur_idx < static_cast<int>(cur_elems.size()) &&
                    hist_elems[hist_idx].is_arr() && cur_elems[cur_idx].is_arr()) {
                    auto sub = remap_array_diff(d->as_array(),
                        hist_elems[hist_idx], cur_elems[cur_idx]);
                    if (sub) {
                        new_diffs.push_back(std::move(sub));
                        new_indices.push_back(new_id);
                        id_remap[eid] = new_id;
                    }
                } else {
                    new_diffs.push_back(d->clone());
                    new_indices.push_back(new_id);
                    id_remap[eid] = new_id;
                }
                break;
            }
            case DeltaType::Element: {
                bool has = cur_idx < static_cast<int>(cur_elems.size());
                JsonVal cv = has ? cur_elems[cur_idx] : JsonVal{};
                auto remapped = remap_field_diff(d->as_element(), has, cv);
                if (remapped) {
                    new_diffs.push_back(std::move(remapped));
                    new_indices.push_back(new_id);
                    id_remap[eid] = new_id;
                }
                break;
            }
        }
    }

    if (new_diffs.empty()) return nullptr;

    // 重建 order：1-based ID 从 hist 映射到 current
    unordered_map<int, int> hist_id_to_cur_id;
    for (auto& [hi, ci] : matching.pairs)
        hist_id_to_cur_id[hi + 1] = ci + 1;

    vector<int> new_order;
    for (int eid : delta.order) {
        if (eid == 0 || eid == -1) {
            new_order.push_back(eid);
            continue;
        }
        if (eid > hist_base_count) {
            auto it2 = id_remap.find(eid);
            if (it2 != id_remap.end())
                new_order.push_back(it2->second);
        } else {
            auto it2 = hist_id_to_cur_id.find(eid);
            if (it2 != hist_id_to_cur_id.end())
                new_order.push_back(it2->second);
        }
    }

    auto result = make_unique<DeltaArray>();
    result->kind_ = delta.kind_;
    result->diffs = std::move(new_diffs);
    result->base_count = cur_base_count;
    result->indices = std::move(new_indices);
    result->order = std::move(new_order);
    result->is_duplist = delta.is_duplist;
    return result;
}

// ── remap: 字典 delta 重映射 ──

static DeltaNodePtr remap_dict_delta(
    const DeltaDict& delta,
    JsonVal hist_obj, JsonVal cur_obj)
{
    auto new_dict = make_unique<DeltaDict>();
    new_dict->kind_ = delta.kind_;

    for (auto& [key, diff] : delta.items) {
        JsonVal hist_val = hist_obj.obj_get(key.c_str());
        bool hist_has = hist_val.valid();
        JsonVal cur_val = cur_obj.obj_get(key.c_str());
        bool cur_has = cur_val.valid();

        switch (diff->type()) {
            case DeltaType::Element: {
                auto remapped = remap_field_diff(diff->as_element(), cur_has, cur_val);
                if (remapped)
                    new_dict->insert(key, std::move(remapped));
                break;
            }
            case DeltaType::Dict: {
                if (cur_has && cur_val.is_obj() && hist_has && hist_val.is_obj()) {
                    auto sub = remap_dict_delta(diff->as_dict(), hist_val, cur_val);
                    if (sub && !sub->as_dict().empty())
                        new_dict->insert(key, std::move(sub));
                }
                break;
            }
            case DeltaType::Array: {
                if (!cur_has || !hist_has) break;

                JsonVal h = hist_val, c = cur_val;
                std::optional<JsonDoc> hist_wrap, cur_wrap;

                if (!hist_val.is_arr() && !hist_val.is_obj()) {
                    hist_wrap.emplace(wrap_scalar_as_array(hist_val));
                    h = hist_wrap->root();
                }
                if (!cur_val.is_arr() && !cur_val.is_obj()) {
                    cur_wrap.emplace(wrap_scalar_as_array(cur_val));
                    c = cur_wrap->root();
                }

                if (h.is_arr() && c.is_arr()) {
                    auto remapped = remap_array_diff(diff->as_array(), h, c);
                    if (remapped)
                        new_dict->insert(key, std::move(remapped));
                }
                break;
            }
        }
    }

    if (new_dict->empty()) return nullptr;
    return new_dict;
}

// ── 公开 API: remap ──

DeltaNodePtr remap_delta_to_current(
    const DeltaDict& delta,
    const JsonDoc& hist_base,
    const JsonDoc& current_base)
{
    if (!hist_base.valid() || !current_base.valid())
        return nullptr;
    JsonVal hist = hist_base.root();
    JsonVal cur = current_base.root();
    if (!hist.is_obj() || !cur.is_obj())
        return delta.clone();
    return remap_dict_delta(delta, hist, cur);
}

}  // namespace sultan
