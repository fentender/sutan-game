#include <catch2/catch_test_macros.hpp>
#include "similarity.h"
#include "delta_rules.h"
#include "delta_node.h"
#include "compute_delta.h"
#include "array_match.h"
#include "apply_delta.h"
#include "json_doc.h"
#include "json_val.h"
#include "json_state.h"

#include <filesystem>
#include <set>

using namespace sultan;

// ==================== similarity ====================

TEST_CASE("delta: levenshtein empty strings") {
    REQUIRE(levenshtein_distance("", "") == 0);
}

TEST_CASE("delta: levenshtein one empty") {
    REQUIRE(levenshtein_distance("", "abc") == 3);
    REQUIRE(levenshtein_distance("abc", "") == 3);
}

TEST_CASE("delta: levenshtein identical") {
    REQUIRE(levenshtein_distance("abc", "abc") == 0);
}

TEST_CASE("delta: levenshtein classic") {
    REQUIRE(levenshtein_distance("kitten", "sitting") == 3);
}

TEST_CASE("delta: levenshtein single char") {
    REQUIRE(levenshtein_distance("a", "b") == 1);
    REQUIRE(levenshtein_distance("a", "a") == 0);
}

TEST_CASE("delta: string_ratio identical") {
    REQUIRE(string_ratio("abc", "abc") == 1.0);
}

TEST_CASE("delta: string_ratio both empty") {
    REQUIRE(string_ratio("", "") == 1.0);
}

TEST_CASE("delta: string_ratio completely different") {
    double r = string_ratio("abc", "xyz");
    REQUIRE(r < 0.1);
}

TEST_CASE("delta: string_ratio partial") {
    double r = string_ratio("abcdef", "abcxyz");
    REQUIRE(r > 0.4);
    REQUIRE(r < 0.6);
}

// ==================== delta_rules ====================

TEST_CASE("delta: rules array element always denied") {
    REQUIRE_FALSE(smart_allow_deletion({"condition", "x"}, true));
    REQUIRE_FALSE(smart_allow_deletion({"a"}, true));
}

TEST_CASE("delta: rules condition context allowed") {
    REQUIRE(smart_allow_deletion({"condition", "x"}, false));
}

TEST_CASE("delta: rules action context allowed") {
    REQUIRE(smart_allow_deletion({"action", "x"}, false));
}

TEST_CASE("delta: rules result context allowed") {
    REQUIRE(smart_allow_deletion({"result", "x"}, false));
}

TEST_CASE("delta: rules result_text field allowed") {
    REQUIRE(smart_allow_deletion({"a", "result_text"}, false));
}

TEST_CASE("delta: rules result_title field allowed") {
    REQUIRE(smart_allow_deletion({"a", "result_title"}, false));
}

TEST_CASE("delta: rules default denied") {
    REQUIRE_FALSE(smart_allow_deletion({"a", "b"}, false));
    REQUIRE_FALSE(smart_allow_deletion({"name"}, false));
}

TEST_CASE("delta: rules empty path denied") {
    REQUIRE_FALSE(smart_allow_deletion({}, false));
}

// ==================== delta_node ====================

TEST_CASE("delta: make_delta_element basic") {
    auto node = make_delta_element(ChangeKind::Added, ScalarValue{42LL});
    REQUIRE(node->type() == DeltaType::Element);
    REQUIRE(node->kind() == ChangeKind::Added);

    auto& elem = node->as_element();
    REQUIRE(std::get<int64_t>(elem.value) == 42);
    REQUIRE(std::holds_alternative<std::nullptr_t>(elem.old_value));
    REQUIRE(elem.version == 0);
}

TEST_CASE("delta: make_delta_element with old_value") {
    auto node = make_delta_element(
        ChangeKind::Changed, ScalarValue{std::string("new")},
        ScalarValue{std::string("old")}, 3);
    auto& elem = node->as_element();
    REQUIRE(std::get<std::string>(elem.value) == "new");
    REQUIRE(std::get<std::string>(elem.old_value) == "old");
    REQUIRE(elem.version == 3);
}

TEST_CASE("delta: make_delta_dict insert and find") {
    auto dict = make_delta_dict();
    REQUIRE(dict->type() == DeltaType::Dict);

    auto& d = dict->as_dict();
    REQUIRE(d.empty());
    REQUIRE(d.size() == 0);

    d.insert("a", make_delta_element(ChangeKind::Added, ScalarValue{1LL}));
    d.insert("b", make_delta_element(ChangeKind::Deleted, nullptr));
    REQUIRE(d.size() == 2);
    REQUIRE_FALSE(d.empty());

    auto* found = d.find("a");
    REQUIRE(found != nullptr);
    REQUIRE(found->type() == DeltaType::Element);
    REQUIRE(found->kind() == ChangeKind::Added);

    REQUIRE(d.find("nonexistent") == nullptr);
}

TEST_CASE("delta: make_delta_array basic") {
    auto arr = make_delta_array();
    REQUIRE(arr->type() == DeltaType::Array);

    auto& a = arr->as_array();
    REQUIRE(a.base_count == 0);
    REQUIRE(a.diffs.empty());
    REQUIRE(a.indices.empty());
    REQUIRE(a.is_duplist == false);
}

TEST_CASE("delta: delta_array wrap with value") {
    auto value = make_delta_element(ChangeKind::Added, ScalarValue{std::string("x")});
    auto wrapped = DeltaArray::wrap(std::move(value), false);
    auto& arr = wrapped->as_array();

    REQUIRE(arr.base_count == 1);
    REQUIRE(arr.diffs.size() == 1);
    REQUIRE(arr.indices.size() == 1);
    REQUIRE(arr.indices[0] == 1);
    REQUIRE(arr.order == std::vector<int>{0, 1, -1});
    REQUIRE_FALSE(arr.is_duplist);
}

TEST_CASE("delta: delta_array wrap nullptr") {
    auto wrapped = DeltaArray::wrap(nullptr, true);
    auto& arr = wrapped->as_array();

    REQUIRE(arr.base_count == 0);
    REQUIRE(arr.diffs.empty());
    REQUIRE(arr.order == std::vector<int>{0, -1});
    REQUIRE(arr.is_duplist);
}

TEST_CASE("delta: clone element") {
    auto orig = make_delta_element(ChangeKind::Changed, ScalarValue{10LL},
                                    ScalarValue{5LL}, 2);
    auto copy = orig->clone();
    auto& e = copy->as_element();

    REQUIRE(e.kind_ == ChangeKind::Changed);
    REQUIRE(std::get<int64_t>(e.value) == 10);
    REQUIRE(std::get<int64_t>(e.old_value) == 5);
    REQUIRE(e.version == 2);
}

TEST_CASE("delta: clone dict deep independence") {
    auto dict = make_delta_dict();
    dict->as_dict().insert("x", make_delta_element(ChangeKind::Added, ScalarValue{1LL}));

    auto copy = dict->clone();
    copy->as_dict().insert("y", make_delta_element(ChangeKind::Added, ScalarValue{2LL}));

    REQUIRE(dict->as_dict().size() == 1);
    REQUIRE(copy->as_dict().size() == 2);
}

TEST_CASE("delta: clone array") {
    auto arr = make_delta_array();
    auto& a = arr->as_array();
    a.base_count = 3;
    a.indices = {1, 2, 4};
    a.order = {0, 1, 2, 4, -1};
    a.diffs.push_back(make_delta_element(ChangeKind::Origin, ScalarValue{1LL}));
    a.diffs.push_back(make_delta_element(ChangeKind::Changed, ScalarValue{20LL}));
    a.diffs.push_back(make_delta_element(ChangeKind::Added, ScalarValue{40LL}));

    auto copy = arr->clone();
    auto& c = copy->as_array();
    REQUIRE(c.base_count == 3);
    REQUIRE(c.indices == std::vector<int>{1, 2, 4});
    REQUIRE(c.order == std::vector<int>{0, 1, 2, 4, -1});
    REQUIRE(c.diffs.size() == 3);
}

TEST_CASE("delta: as_element throws on dict") {
    auto dict = make_delta_dict();
    REQUIRE_THROWS(dict->as_element());
}

TEST_CASE("delta: as_dict throws on element") {
    auto elem = make_delta_element(ChangeKind::Origin, nullptr);
    REQUIRE_THROWS(elem->as_dict());
}

TEST_CASE("delta: as_array throws on element") {
    auto elem = make_delta_element(ChangeKind::Origin, nullptr);
    REQUIRE_THROWS(elem->as_array());
}

// ==================== array_match ====================

TEST_CASE("delta: match empty arrays") {
    auto doc = JsonDoc::parse(R"({"a":[],"b":[]})");
    JsonVal root = doc.root();
    JsonVal a = root.obj_get("a");
    JsonVal b = root.obj_get("b");
    auto m = match_by_heuristic(a, b);
    REQUIRE(m.pairs.empty());
    REQUIRE(m.unmatched_base.empty());
    REQUIRE(m.unmatched_mod.empty());
}

TEST_CASE("delta: match identical scalar arrays") {
    auto base = JsonDoc::parse(R"([1, 2, 3])");
    auto mod = JsonDoc::parse(R"([1, 2, 3])");
    auto m = match_by_heuristic(base.root(), mod.root());
    REQUIRE(m.pairs.size() == 3);
    REQUIRE(m.unmatched_base.empty());
    REQUIRE(m.unmatched_mod.empty());
}

TEST_CASE("delta: match by guid key") {
    auto base = JsonDoc::parse(R"([{"guid":"a","v":1},{"guid":"b","v":2}])");
    auto mod = JsonDoc::parse(R"([{"guid":"a","v":10},{"guid":"b","v":20}])");
    auto m = match_by_heuristic(base.root(), mod.root());
    REQUIRE(m.pairs.size() == 2);
    REQUIRE(m.unmatched_base.empty());
    REQUIRE(m.unmatched_mod.empty());
}

TEST_CASE("delta: match by guid key reorder") {
    auto base = JsonDoc::parse(R"([{"guid":"a","v":1},{"guid":"b","v":2}])");
    auto mod = JsonDoc::parse(R"([{"guid":"b","v":20},{"guid":"a","v":10}])");
    auto m = match_by_heuristic(base.root(), mod.root());
    REQUIRE(m.pairs.size() >= 1);
    REQUIRE(m.unmatched_base.size() + m.pairs.size() == 2);
}

TEST_CASE("delta: match with added elements") {
    auto base = JsonDoc::parse(R"([{"id":1}])");
    auto mod = JsonDoc::parse(R"([{"id":1},{"id":2}])");
    auto m = match_by_heuristic(base.root(), mod.root());
    REQUIRE(m.pairs.size() == 1);
    REQUIRE(m.pairs[0].first == 0);
    REQUIRE(m.pairs[0].second == 0);
    REQUIRE(m.unmatched_mod.size() == 1);
    REQUIRE(m.unmatched_mod[0] == 1);
}

TEST_CASE("delta: match with deleted elements") {
    auto base = JsonDoc::parse(R"([{"id":1},{"id":2}])");
    auto mod = JsonDoc::parse(R"([{"id":1}])");
    auto m = match_by_heuristic(base.root(), mod.root());
    REQUIRE(m.pairs.size() == 1);
    REQUIRE(m.unmatched_base.size() == 1);
}

// ==================== compute_delta ====================

TEST_CASE("delta: compute identical docs returns nullptr") {
    auto base = JsonDoc::parse(R"({"a":1,"b":"hello"})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":"hello"})");
    auto d = compute_delta(base, mod);
    REQUIRE(d == nullptr);
}

TEST_CASE("delta: compute scalar field changed") {
    auto base = JsonDoc::parse(R"({"x":10})");
    auto mod = JsonDoc::parse(R"({"x":20})");
    auto d = compute_delta(base, mod);
    REQUIRE(d != nullptr);
    REQUIRE(d->type() == DeltaType::Dict);
    auto& dict = d->as_dict();
    REQUIRE(dict.size() == 1);
    auto* entry = dict.find("x");
    REQUIRE(entry != nullptr);
    REQUIRE(entry->type() == DeltaType::Element);
    REQUIRE(entry->kind() == ChangeKind::Changed);
    REQUIRE(std::get<int64_t>(entry->as_element().value) == 20);
}

TEST_CASE("delta: compute field added") {
    auto base = JsonDoc::parse(R"({"a":1})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":2})");
    auto d = compute_delta(base, mod);
    REQUIRE(d != nullptr);
    auto& dict = d->as_dict();
    REQUIRE(dict.size() == 1);
    auto* entry = dict.find("b");
    REQUIRE(entry != nullptr);
    REQUIRE(entry->kind() == ChangeKind::Added);
}

TEST_CASE("delta: compute field deleted") {
    auto base = JsonDoc::parse(R"({"a":1,"b":2})");
    auto mod = JsonDoc::parse(R"({"a":1})");
    auto d = compute_delta(base, mod);
    REQUIRE(d != nullptr);
    auto& dict = d->as_dict();
    auto* entry = dict.find("b");
    REQUIRE(entry != nullptr);
    REQUIRE(entry->kind() == ChangeKind::Deleted);
}

TEST_CASE("delta: compute nested dict change") {
    auto base = JsonDoc::parse(R"({"outer":{"inner":1}})");
    auto mod = JsonDoc::parse(R"({"outer":{"inner":2}})");
    auto d = compute_delta(base, mod);
    REQUIRE(d != nullptr);
    auto& dict = d->as_dict();
    auto* outer = dict.find("outer");
    REQUIRE(outer != nullptr);
    REQUIRE(outer->type() == DeltaType::Dict);
    auto* inner = outer->as_dict().find("inner");
    REQUIRE(inner != nullptr);
    REQUIRE(inner->kind() == ChangeKind::Changed);
    REQUIRE(std::get<int64_t>(inner->as_element().value) == 2);
}

TEST_CASE("delta: compute array element added") {
    auto base = JsonDoc::parse(R"({"arr":[1,2]})");
    auto mod = JsonDoc::parse(R"({"arr":[1,2,3]})");
    auto d = compute_delta(base, mod);
    REQUIRE(d != nullptr);
    auto* arr_entry = d->as_dict().find("arr");
    REQUIRE(arr_entry != nullptr);
    REQUIRE(arr_entry->type() == DeltaType::Array);
    auto& arr = arr_entry->as_array();
    REQUIRE(arr.base_count == 2);
    bool found_added = false;
    for (size_t i = 0; i < arr.diffs.size(); ++i) {
        if (arr.diffs[i]->kind() == ChangeKind::Added) {
            found_added = true;
            break;
        }
    }
    REQUIRE(found_added);
}

TEST_CASE("delta: compute smart mode blocks deletion") {
    auto base = JsonDoc::parse(R"({"a":1,"name":"test"})");
    auto mod = JsonDoc::parse(R"({"a":1})");
    auto d = compute_delta(base, mod, MergeMode::Smart);
    REQUIRE(d == nullptr);
}

TEST_CASE("delta: compute smart mode allows condition deletion") {
    auto base = JsonDoc::parse(R"({"condition":{"x":1,"y":2}})");
    auto mod = JsonDoc::parse(R"({"condition":{"x":1}})");
    auto d = compute_delta(base, mod, MergeMode::Smart);
    REQUIRE(d != nullptr);
}

TEST_CASE("delta: compute added complex value has value_node") {
    auto base = JsonDoc::parse(R"({"a":1})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":{"nested":true}})");
    auto d = compute_delta(base, mod);
    REQUIRE(d != nullptr);
    auto* entry = d->as_dict().find("b");
    REQUIRE(entry != nullptr);
    REQUIRE(entry->type() == DeltaType::Element);
    REQUIRE(entry->kind() == ChangeKind::Added);
    REQUIRE(entry->as_element().value_node != nullptr);
    REQUIRE(entry->as_element().value_node->is_dict());
}

// ==================== apply_delta ====================

#include "apply_delta.h"
#include "json_state.h"
#include <memory>
using std::make_unique;

TEST_CASE("delta: apply added scalar field") {
    auto state = JsonState::from_text(R"({"a":1})");
    DeltaDict delta;
    delta.items.emplace("b", make_delta_element(ChangeKind::Added, ScalarValue{2LL}));

    apply_delta_to_state(state, delta, nullptr, 1, false);

    auto doc = state.to_doc();
    auto root = doc.root();
    REQUIRE(root.obj_get("b").get_int() == 2);
}

TEST_CASE("delta: direct state insert replaces entry") {
    auto state = JsonState::from_text(R"({"x":10})");
    auto& dict = state.root().as_dict();
    REQUIRE(dict.find("x")->as_element().value == ScalarValue{int64_t(10)});
    dict.insert("x", make_element(ScalarValue{20LL}));
    REQUIRE(dict.find("x")->as_element().value == ScalarValue{int64_t(20)});
}

TEST_CASE("delta: apply_field_delta basic") {
    DeltaElement diff;
    diff.kind_ = ChangeKind::Changed;
    diff.value = ScalarValue{20LL};

    JsonElementState existing;
    existing.kind_ = ChangeKind::Origin;
    existing.value = ScalarValue{10LL};

    auto result = apply_field_delta(diff, &existing, 1, false);
    REQUIRE(result != nullptr);
    auto& r = result->as_element();
    REQUIRE(std::get<int64_t>(r.value) == 20);
    REQUIRE(std::get<int64_t>(r.old_value) == 10);
}

TEST_CASE("delta: apply changed scalar field") {
    auto state = JsonState::from_text(R"({"x":10})");
    DeltaDict delta;
    delta.items.emplace("x", make_delta_element(ChangeKind::Changed, ScalarValue{20LL}));

    apply_delta_to_state(state, delta, nullptr, 1, false);

    auto& elem = state.root().as_dict().find("x")->as_element();
    REQUIRE(std::get<int64_t>(elem.value) == 20);
    REQUIRE(std::get<int64_t>(elem.old_value) == 10);
    REQUIRE(sultan::base_kind(elem.kind_) == ChangeKind::Changed);
    REQUIRE(elem.version == 1);
}

TEST_CASE("delta: apply deleted field") {
    auto state = JsonState::from_text(R"({"a":1,"b":2})");
    DeltaDict delta;
    delta.items.emplace("b", make_delta_element(ChangeKind::Deleted, nullptr, ScalarValue{2LL}));

    apply_delta_to_state(state, delta, nullptr, 1, false);

    auto& elem = state.root().as_dict().find("b")->as_element();
    REQUIRE(sultan::base_kind(elem.kind_) == ChangeKind::Deleted);
}

TEST_CASE("delta: apply multi_mod marking") {
    auto state = JsonState::from_text(R"({"x":1})");

    DeltaDict delta1;
    delta1.items.emplace("x", make_delta_element(ChangeKind::Changed, ScalarValue{10LL}));
    apply_delta_to_state(state, delta1, nullptr, 1, false);

    DeltaDict delta2;
    delta2.items.emplace("x", make_delta_element(ChangeKind::Changed, ScalarValue{20LL}));
    apply_delta_to_state(state, delta2, nullptr, 2, false);

    auto& elem = state.root().as_dict().find("x")->as_element();
    REQUIRE(std::get<int64_t>(elem.value) == 20);
    REQUIRE(sultan::base_kind(elem.kind_) == ChangeKind::Changed);
    REQUIRE(is_multi_mod(elem.kind_));
}

TEST_CASE("delta: apply override marking") {
    auto state = JsonState::from_text(R"({"x":1})");
    DeltaDict delta;
    delta.items.emplace("x", make_delta_element(ChangeKind::Changed, ScalarValue{99LL}));

    apply_delta_to_state(state, delta, nullptr, 1, true);

    auto& elem = state.root().as_dict().find("x")->as_element();
    REQUIRE(is_override(elem.kind_));
}

TEST_CASE("delta: apply nested dict delta") {
    auto state = JsonState::from_text(R"({"outer":{"a":1,"b":2}})");

    auto inner_delta = make_delta_dict();
    inner_delta->as_dict().insert("b", make_delta_element(ChangeKind::Changed, ScalarValue{20LL}));

    DeltaDict delta;
    delta.items.emplace("outer", std::move(inner_delta));

    apply_delta_to_state(state, delta, nullptr, 1, false);

    auto* outer = state.root().as_dict().find("outer");
    REQUIRE(outer != nullptr);
    REQUIRE(outer->is_dict());
    auto* b = outer->as_dict().find("b");
    REQUIRE(b != nullptr);
    REQUIRE(std::get<int64_t>(b->as_element().value) == 20);
}

TEST_CASE("delta: apply added complex value with value_node") {
    auto state = JsonState::from_text(R"({"a":1})");

    auto complex_node = make_unique<JsonDictState>();
    auto inner_elem = make_unique<JsonElementState>();
    inner_elem->value = ScalarValue{true};
    complex_node->insert("nested", std::move(inner_elem));

    auto elem = make_unique<DeltaElement>();
    elem->kind_ = ChangeKind::Added;
    elem->value_node = std::move(complex_node);

    DeltaDict delta;
    delta.items.emplace("b", std::move(elem));

    apply_delta_to_state(state, delta, nullptr, 1, false);

    auto* b = state.root().as_dict().find("b");
    REQUIRE(b != nullptr);
    REQUIRE(b->is_dict());
    auto* nested = b->as_dict().find("nested");
    REQUIRE(nested != nullptr);
    REQUIRE(nested->is_element());
}

// ==================== end-to-end ====================

TEST_CASE("delta: e2e compute then apply scalar changes") {
    auto base_doc = JsonDoc::parse(R"({"name":"alice","age":30,"city":"NYC"})");
    auto mod_doc = JsonDoc::parse(R"({"name":"alice","age":31,"country":"US"})");

    auto delta = compute_delta(base_doc, mod_doc);
    REQUIRE(delta != nullptr);
    REQUIRE(delta->type() == DeltaType::Dict);

    auto state = JsonState::from_doc(base_doc);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);

    auto result = state.to_doc();
    auto root = result.root();
    REQUIRE(std::string(root.obj_get("name").get_str()) == "alice");
    REQUIRE(root.obj_get("age").get_int() == 31);
    REQUIRE(std::string(root.obj_get("country").get_str()) == "US");
}

TEST_CASE("delta: e2e compute then apply nested changes") {
    auto base_doc = JsonDoc::parse(R"({"data":{"x":1,"y":2}})");
    auto mod_doc = JsonDoc::parse(R"({"data":{"x":10,"y":2}})");

    auto delta = compute_delta(base_doc, mod_doc);
    REQUIRE(delta != nullptr);

    auto state = JsonState::from_doc(base_doc);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);

    auto result = state.to_doc();
    auto data = result.root().obj_get("data");
    REQUIRE(data.obj_get("x").get_int() == 10);
    REQUIRE(data.obj_get("y").get_int() == 2);
}

// ==================== serialization roundtrip ====================

TEST_CASE("delta: serialize element roundtrip") {
    auto orig = make_delta_element(ChangeKind::Changed, ScalarValue{std::string("new")},
                                    ScalarValue{std::string("old")}, 2);
    auto doc = serialize_delta(*orig);
    auto restored = deserialize_delta(doc);

    REQUIRE(restored->type() == DeltaType::Element);
    auto& e = restored->as_element();
    REQUIRE(e.kind_ == ChangeKind::Changed);
    REQUIRE(std::get<std::string>(e.value) == "new");
    REQUIRE(std::get<std::string>(e.old_value) == "old");
    REQUIRE(e.version == 2);
}

TEST_CASE("delta: serialize dict roundtrip") {
    auto dict = make_delta_dict();
    dict->as_dict().insert("a", make_delta_element(ChangeKind::Added, ScalarValue{1LL}));
    dict->as_dict().insert("b", make_delta_element(ChangeKind::Deleted, nullptr, ScalarValue{2LL}));

    auto doc = serialize_delta(*dict);
    auto restored = deserialize_delta(doc);

    REQUIRE(restored->type() == DeltaType::Dict);
    auto& d = restored->as_dict();
    REQUIRE(d.size() == 2);
    REQUIRE(d.find("a")->kind() == ChangeKind::Added);
    REQUIRE(d.find("b")->kind() == ChangeKind::Deleted);
}

TEST_CASE("delta: serialize array roundtrip") {
    auto arr = make_delta_array();
    auto& a = arr->as_array();
    a.base_count = 2;
    a.indices = {1, 3};
    a.order = {0, 1, 3, -1};
    a.is_duplist = false;
    a.diffs.push_back(make_delta_element(ChangeKind::Changed, ScalarValue{10LL}));
    a.diffs.push_back(make_delta_element(ChangeKind::Added, ScalarValue{30LL}));

    auto doc = serialize_delta(*arr);
    auto restored = deserialize_delta(doc);

    REQUIRE(restored->type() == DeltaType::Array);
    auto& ra = restored->as_array();
    REQUIRE(ra.base_count == 2);
    REQUIRE(ra.indices == std::vector<int>{1, 3});
    REQUIRE(ra.order == std::vector<int>{0, 1, 3, -1});
    REQUIRE(ra.diffs.size() == 2);
}

TEST_CASE("delta: serialize nested dict roundtrip") {
    auto outer = make_delta_dict();
    auto inner = make_delta_dict();
    inner->as_dict().insert("x", make_delta_element(ChangeKind::Changed, ScalarValue{99LL}));
    outer->as_dict().insert("nested", std::move(inner));

    auto doc = serialize_delta(*outer);
    auto restored = deserialize_delta(doc);

    REQUIRE(restored->type() == DeltaType::Dict);
    auto* nested = restored->as_dict().find("nested");
    REQUIRE(nested != nullptr);
    REQUIRE(nested->type() == DeltaType::Dict);
    auto* x = nested->as_dict().find("x");
    REQUIRE(x != nullptr);
    REQUIRE(std::get<int64_t>(x->as_element().value) == 99);
}

// ==================== remap_delta_to_current ====================

TEST_CASE("delta: remap deleted field not in current drops") {
    auto hist = JsonDoc::parse(R"({"a":1,"b":2})");
    auto mod = JsonDoc::parse(R"({"a":1})");
    auto current = JsonDoc::parse(R"({"a":1})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped == nullptr);
}

TEST_CASE("delta: remap deleted field in current updates old_value") {
    auto hist = JsonDoc::parse(R"({"a":1,"b":2})");
    auto mod = JsonDoc::parse(R"({"a":1})");
    auto current = JsonDoc::parse(R"({"a":1,"b":99})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped != nullptr);
    auto* b = remapped->as_dict().find("b");
    REQUIRE(b != nullptr);
    REQUIRE(base_kind(b->kind()) == ChangeKind::Deleted);
    REQUIRE(std::get<int64_t>(b->as_element().old_value) == 99);
}

TEST_CASE("delta: remap added field not in current keeps") {
    auto hist = JsonDoc::parse(R"({"a":1})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":2})");
    auto current = JsonDoc::parse(R"({"a":1})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped != nullptr);
    auto* b = remapped->as_dict().find("b");
    REQUIRE(b != nullptr);
    REQUIRE(base_kind(b->kind()) == ChangeKind::Added);
}

TEST_CASE("delta: remap added field same in current drops") {
    auto hist = JsonDoc::parse(R"({"a":1})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":2})");
    auto current = JsonDoc::parse(R"({"a":1,"b":2})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped == nullptr);
}

TEST_CASE("delta: remap added field different in current recomputes") {
    auto hist = JsonDoc::parse(R"({"a":1})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":2})");
    auto current = JsonDoc::parse(R"({"a":1,"b":5})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped != nullptr);
    auto* b = remapped->as_dict().find("b");
    REQUIRE(b != nullptr);
    REQUIRE(base_kind(b->kind()) == ChangeKind::Changed);
    REQUIRE(std::get<int64_t>(b->as_element().value) == 2);
    REQUIRE(std::get<int64_t>(b->as_element().old_value) == 5);
}

TEST_CASE("delta: remap changed field not in current converts to added") {
    auto hist = JsonDoc::parse(R"({"a":1,"b":2})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":10})");
    auto current = JsonDoc::parse(R"({"a":1})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped != nullptr);
    auto* b = remapped->as_dict().find("b");
    REQUIRE(b != nullptr);
    REQUIRE(base_kind(b->kind()) == ChangeKind::Added);
    REQUIRE(std::get<int64_t>(b->as_element().value) == 10);
}

TEST_CASE("delta: remap changed field same in current drops") {
    auto hist = JsonDoc::parse(R"({"a":1,"b":2})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":10})");
    auto current = JsonDoc::parse(R"({"a":1,"b":10})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped == nullptr);
}

TEST_CASE("delta: remap changed field different in current recomputes") {
    auto hist = JsonDoc::parse(R"({"a":1,"b":2})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":10})");
    auto current = JsonDoc::parse(R"({"a":1,"b":5})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped != nullptr);
    auto* b = remapped->as_dict().find("b");
    REQUIRE(b != nullptr);
    REQUIRE(base_kind(b->kind()) == ChangeKind::Changed);
    REQUIRE(std::get<int64_t>(b->as_element().value) == 10);
    REQUIRE(std::get<int64_t>(b->as_element().old_value) == 5);
}

TEST_CASE("delta: remap nested dict recursion") {
    auto hist = JsonDoc::parse(R"({"d":{"x":1,"y":2}})");
    auto mod = JsonDoc::parse(R"({"d":{"x":10,"y":2}})");
    auto current = JsonDoc::parse(R"({"d":{"x":5,"y":2}})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped != nullptr);
    auto* d = remapped->as_dict().find("d");
    REQUIRE(d != nullptr);
    REQUIRE(d->type() == DeltaType::Dict);
    auto* x = d->as_dict().find("x");
    REQUIRE(x != nullptr);
    REQUIRE(std::get<int64_t>(x->as_element().value) == 10);
    REQUIRE(std::get<int64_t>(x->as_element().old_value) == 5);
}

TEST_CASE("delta: remap array reindex") {
    auto hist = JsonDoc::parse(
        R"({"items":[{"guid":"a","v":1},{"guid":"b","v":2}]})");
    auto mod = JsonDoc::parse(
        R"({"items":[{"guid":"a","v":10},{"guid":"b","v":2}]})");
    auto current = JsonDoc::parse(
        R"({"items":[{"guid":"b","v":2},{"guid":"a","v":5}]})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped != nullptr);

    auto state = JsonState::from_doc(current);
    apply_delta_to_state(state, remapped->as_dict(), nullptr, 1, false);
    auto result = state.to_doc();
    auto items = result.root().obj_get("items");
    REQUIRE(items.valid());

    auto it = items.arr_iter();
    JsonVal elem;
    bool found_a = false;
    while (it.next(elem)) {
        auto guid = elem.obj_get("guid");
        if (guid.valid() && std::string(guid.get_str()) == "a") {
            REQUIRE(elem.obj_get("v").get_int() == 10);
            found_a = true;
        }
    }
    REQUIRE(found_a);
}

TEST_CASE("delta: remap added complex value") {
    auto hist = JsonDoc::parse(R"({"a":1})");
    auto mod = JsonDoc::parse(R"({"a":1,"obj":{"x":1,"y":2}})");
    auto current = JsonDoc::parse(R"({"a":1})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped != nullptr);
    auto* obj = remapped->as_dict().find("obj");
    REQUIRE(obj != nullptr);
    REQUIRE(base_kind(obj->kind()) == ChangeKind::Added);
}

TEST_CASE("delta: remap e2e compute remap apply") {
    auto hist_base = JsonDoc::parse(R"({"a":1,"b":2,"c":3})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":20,"d":4})");
    auto current_base = JsonDoc::parse(R"({"a":10,"b":2,"c":3})");

    auto delta = compute_delta(hist_base, mod);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist_base, current_base);
    REQUIRE(remapped != nullptr);

    auto state = JsonState::from_doc(current_base);
    apply_delta_to_state(state, remapped->as_dict(), nullptr, 1, false);
    auto result = state.to_doc();

    REQUIRE(result.root().obj_get("a").get_int() == 10);
    REQUIRE(result.root().obj_get("b").get_int() == 20);
    REQUIRE_FALSE(result.root().obj_get("c").valid());
    REQUIRE(result.root().obj_get("d").get_int() == 4);
}

// ==================== skip_root_deletion ====================

TEST_CASE("delta: skip_root_deletion suppresses root deletions") {
    auto base = JsonDoc::parse(R"({"a":1,"b":2,"c":3})");
    auto mod = JsonDoc::parse(R"({"a":10,"c":3})");

    auto delta_normal = compute_delta(base, mod, MergeMode::Normal, false);
    REQUIRE(delta_normal != nullptr);
    auto* del_b = delta_normal->as_dict().find("b");
    REQUIRE(del_b != nullptr);
    REQUIRE(base_kind(del_b->kind()) == ChangeKind::Deleted);

    auto delta_skip = compute_delta(base, mod, MergeMode::Normal, true);
    REQUIRE(delta_skip != nullptr);
    REQUIRE(delta_skip->as_dict().find("b") == nullptr);
    auto* changed_a = delta_skip->as_dict().find("a");
    REQUIRE(changed_a != nullptr);
    REQUIRE(base_kind(changed_a->kind()) == ChangeKind::Changed);
}

TEST_CASE("delta: skip_root_deletion still detects nested deletions") {
    auto base = JsonDoc::parse(R"({"a":{"x":1,"y":2}})");
    auto mod = JsonDoc::parse(R"({"a":{"x":10}})");

    auto delta = compute_delta(base, mod, MergeMode::Normal, true);
    REQUIRE(delta != nullptr);
    auto* a_node = delta->as_dict().find("a");
    REQUIRE(a_node != nullptr);
    auto* y_node = a_node->as_dict().find("y");
    REQUIRE(y_node != nullptr);
    REQUIRE(base_kind(y_node->kind()) == ChangeKind::Deleted);
}

TEST_CASE("delta: skip_root_deletion with no changes returns nullptr") {
    auto base = JsonDoc::parse(R"({"a":1,"b":2})");
    auto mod = JsonDoc::parse(R"({"a":1})");

    auto delta = compute_delta(base, mod, MergeMode::Normal, true);
    REQUIRE(delta == nullptr);
}

TEST_CASE("delta: skip_root_deletion added keys still detected") {
    auto base = JsonDoc::parse(R"({"a":1})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":2})");

    auto delta = compute_delta(base, mod, MergeMode::Normal, true);
    REQUIRE(delta != nullptr);
    auto* b_node = delta->as_dict().find("b");
    REQUIRE(b_node != nullptr);
    REQUIRE(base_kind(b_node->kind()) == ChangeKind::Added);
}

// ==================== array_match (fixture) ====================

static std::string fixtures_path(const std::string& name) {
    return std::string(FIXTURES_DIR) + "/" + name;
}

TEST_CASE("delta: match insert and delete no cascade") {
    auto path_base = fixtures_path("rite_5000003_base.json");
    auto path_mod = fixtures_path("rite_5000003_mod_abude.json");
    if (!std::filesystem::exists(path_base) || !std::filesystem::exists(path_mod)) {
        SKIP("fixture files not found");
    }

    auto base_doc = JsonDoc::parse_file(path_base);
    auto mod_doc = JsonDoc::parse_file(path_mod);

    auto base_settlement = base_doc.root().obj_get("settlement");
    auto mod_settlement = mod_doc.root().obj_get("settlement");
    REQUIRE(base_settlement.valid());
    REQUIRE(mod_settlement.valid());

    auto result = match_by_heuristic(base_settlement, mod_settlement);

    // base[16] (guid=95077769...) 应被删除 → 在 unmatched_base 中
    std::set<int> ub(result.unmatched_base.begin(), result.unmatched_base.end());
    REQUIRE(ub.count(16) == 1);

    // mod[14] 为新插入 → 在 unmatched_mod 中
    std::set<int> um(result.unmatched_mod.begin(), result.unmatched_mod.end());
    REQUIRE(um.count(14) == 1);

    // 其余元素通过 guid 精确配对
    // base[0..13] → mod[0..13]
    std::unordered_map<int, int> pair_map;
    for (auto& [bi, mi] : result.pairs) {
        pair_map[bi] = mi;
    }
    for (int i = 0; i < 14; ++i) {
        REQUIRE(pair_map.count(i) == 1);
        REQUIRE(pair_map[i] == i);
    }
    // base[14] → mod[15], base[15] → mod[16]
    REQUIRE(pair_map[14] == 15);
    REQUIRE(pair_map[15] == 16);
}

// ==================== remap (array) ====================

TEST_CASE("delta: remap array insert reindex") {
    auto hist = JsonDoc::parse(R"({
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "b", "v": 2},
            {"guid": "c", "v": 3}
        ]
    })");
    auto mod = JsonDoc::parse(R"({
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "b", "v": 99},
            {"guid": "c", "v": 3}
        ]
    })");
    auto current = JsonDoc::parse(R"({
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "x", "v": 50},
            {"guid": "b", "v": 2},
            {"guid": "c", "v": 3}
        ]
    })");

    auto delta = compute_delta(hist, mod, MergeMode::Smart);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped != nullptr);

    // 应用到 current state，验证 b.v == 99
    auto state = JsonState::from_doc(current);
    apply_delta_to_state(state, remapped->as_dict(), nullptr, 1, false);
    auto result_doc = state.to_doc();

    auto items = result_doc.root().obj_get("settlement");
    REQUIRE(items.valid());
    auto it = items.arr_iter();
    JsonVal elem;
    bool found_b = false;
    while (it.next(elem)) {
        auto guid = elem.obj_get("guid");
        if (guid.valid() && std::string(guid.get_str()) == "b") {
            REQUIRE(elem.obj_get("v").get_int() == 99);
            found_b = true;
        }
    }
    REQUIRE(found_b);
}

TEST_CASE("delta: remap array changed element disappeared") {
    auto hist = JsonDoc::parse(R"({
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "b", "v": 2}
        ]
    })");
    auto mod = JsonDoc::parse(R"({
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "b", "v": 99}
        ]
    })");
    auto current = JsonDoc::parse(R"({
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1}
        ]
    })");

    auto delta = compute_delta(hist, mod, MergeMode::Smart);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    // b 已被删除，对 b 的 CHANGED 应被丢弃
    // remapped 可能为 nullptr（全部丢弃）或 settlement delta 无 diffs
    if (remapped != nullptr) {
        auto* settlement = remapped->as_dict().find("settlement");
        if (settlement != nullptr && settlement->type() == DeltaType::Array) {
            REQUIRE(settlement->as_array().diffs.empty());
        }
    }
}

TEST_CASE("delta: remap array order preserves origin elements") {
    auto hist = JsonDoc::parse(R"({
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "b", "v": 2},
            {"guid": "c", "v": 3},
            {"guid": "d", "v": 4}
        ]
    })");
    auto mod = JsonDoc::parse(R"({
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "b", "v": 99},
            {"guid": "c", "v": 3},
            {"guid": "d", "v": 4}
        ]
    })");
    auto current = JsonDoc::parse(R"({
        "id": "x",
        "settlement": [
            {"guid": "a", "v": 1},
            {"guid": "x", "v": 50},
            {"guid": "b", "v": 2},
            {"guid": "c", "v": 3},
            {"guid": "d", "v": 4}
        ]
    })");

    auto delta = compute_delta(hist, mod, MergeMode::Smart);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped != nullptr);

    auto* settlement = remapped->as_dict().find("settlement");
    REQUIRE(settlement != nullptr);
    REQUIRE(settlement->type() == DeltaType::Array);
    auto& arr = settlement->as_array();

    // order 应包含 A(1), B(3), C(4), D(5)，不含 X(2)；边界 0/-1
    std::vector<int> inner;
    for (int x : arr.order) {
        if (x != 0 && x != -1) inner.push_back(x);
    }
    REQUIRE(inner == std::vector<int>{1, 3, 4, 5});
}

TEST_CASE("delta: remap array large preserves all origin") {
    // 10 元素数组只改第 6 个，current 在位置 3 插入新元素
    std::string hist_json = R"({"id":"x","settlement":[)";
    std::string mod_json = R"({"id":"x","settlement":[)";
    std::string current_json = R"({"id":"x","settlement":[)";
    for (int i = 0; i < 10; ++i) {
        std::string comma = (i > 0) ? "," : "";
        hist_json += comma + R"({"guid":"g)" + std::to_string(i) + R"(","v":)" + std::to_string(i) + "}";
        int v = (i == 5) ? 999 : i;
        mod_json += comma + R"({"guid":"g)" + std::to_string(i) + R"(","v":)" + std::to_string(v) + "}";
        if (i == 3) {
            current_json += comma + R"({"guid":"new","v":-1})";
            current_json += R"(,{"guid":"g)" + std::to_string(i) + R"(","v":)" + std::to_string(i) + "}";
        } else {
            current_json += comma + R"({"guid":"g)" + std::to_string(i) + R"(","v":)" + std::to_string(i) + "}";
        }
    }
    hist_json += "]}";
    mod_json += "]}";
    current_json += "]}";

    auto hist = JsonDoc::parse(hist_json);
    auto mod = JsonDoc::parse(mod_json);
    auto current = JsonDoc::parse(current_json);

    auto delta = compute_delta(hist, mod, MergeMode::Smart);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped != nullptr);

    auto* settlement = remapped->as_dict().find("settlement");
    REQUIRE(settlement != nullptr);
    REQUIRE(settlement->type() == DeltaType::Array);
    auto& arr = settlement->as_array();

    std::vector<int> inner;
    for (int x : arr.order) {
        if (x != 0 && x != -1) inner.push_back(x);
    }
    // 10 个历史元素应全部出现（current 有 11 个元素，历史 10 个全匹配）
    REQUIRE(inner.size() == 10);
}

TEST_CASE("delta: remap nested settlement condition real bug") {
    // 复现真实 bug：rite/5000002.json guid=f2dde237 的 condition 字段
    auto hist = JsonDoc::parse(R"({
        "id": "5000002",
        "settlement": [{
            "guid": "f2dde237-b19d-40ab-94f2-8292381277aa",
            "condition": {"s1.is": 2000757, "have.2000056": 1},
            "result_title": "",
            "result_text": "",
            "result": {},
            "action": {"event_on": 5321035}
        }]
    })");
    auto mod = JsonDoc::parse(R"({
        "id": "5000002",
        "settlement": [{
            "guid": "f2dde237-b19d-40ab-94f2-8292381277aa",
            "condition": {"s1.is": 2000757, "have.2000056.追随者": 1},
            "result_title": "",
            "result_text": "",
            "result": {},
            "action": {"event_on": 5321035}
        }]
    })");
    auto current = JsonDoc::parse(R"({
        "id": "5000002",
        "settlement": [{
            "guid": "f2dde237-b19d-40ab-94f2-8292381277aa",
            "condition": {"s1.is": 2000757, "table_have.2000056": 1, "have.2000056.追随者": 1},
            "result_title": "",
            "result_text": "",
            "result": {},
            "action": {"event_on": 5321035}
        }]
    })");

    auto delta = compute_delta(hist, mod, MergeMode::Normal);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    // have.2000056: DELETED 应被丢弃（current 中不存在）
    // have.2000056.追随者: ADDED 应被丢弃（current 中已存在且值相同）
    // 因此整个 delta 应为空或 condition 子 delta 为空
    if (remapped != nullptr) {
        auto* settlement = remapped->as_dict().find("settlement");
        if (settlement != nullptr && settlement->type() == DeltaType::Array) {
            auto& arr = settlement->as_array();
            if (!arr.diffs.empty() && arr.diffs[0] != nullptr) {
                auto* elem_delta = arr.diffs[0].get();
                if (elem_delta->type() == DeltaType::Dict) {
                    auto* cond = elem_delta->as_dict().find("condition");
                    if (cond != nullptr) {
                        // condition 中不应有 have.2000056 和 have.2000056.追随者
                        REQUIRE(cond->as_dict().find("have.2000056") == nullptr);
                        auto key = std::string("have.2000056.\xe8\xbf\xbd\xe9\x9a\x8f\xe8\x80\x85");
                        REQUIRE(cond->as_dict().find(key) == nullptr);
                    }
                }
            }
        }
    }
}

// ==================== compute_delta (scalar to array) ====================

TEST_CASE("delta: compute scalar to array type change") {
    auto base = JsonDoc::parse(R"({"2000523":{"id":2000523,"name":"test","resource":"cards/2000523","rare":4}})");
    auto mod = JsonDoc::parse(R"({"2000523":{"id":2000523,"name":"test","resource":["cards/2000523","cards/2000523_1"],"rare":4}})");

    auto delta = compute_delta(base, mod, MergeMode::Normal, true);
    REQUIRE(delta != nullptr);

    auto* entry = delta->as_dict().find("2000523");
    REQUIRE(entry != nullptr);
    REQUIRE(entry->type() == DeltaType::Dict);

    // resource 字段应为 DeltaArray（标量归一化为单元素数组后做数组差异）
    auto* resource = entry->as_dict().find("resource");
    REQUIRE(resource != nullptr);
    REQUIRE(resource->type() == DeltaType::Array);

    auto& arr = resource->as_array();
    // base_count=1（原标量归一化为单元素数组）
    REQUIRE(arr.base_count == 1);
    // 应有 1 个 ADDED 元素
    int added_count = 0;
    for (auto& d : arr.diffs) {
        if (d && base_kind(d->kind()) == ChangeKind::Added) ++added_count;
    }
    REQUIRE(added_count == 1);
}

TEST_CASE("delta: remap scalar to array not dropped") {
    auto hist_base = JsonDoc::parse(R"({"2000523":{"id":2000523,"resource":"cards/2000523","rare":4}})");
    auto current_base = JsonDoc::parse(R"({"2000523":{"id":2000523,"resource":"cards/2000523","rare":4}})");
    auto mod = JsonDoc::parse(R"({"2000523":{"id":2000523,"resource":["cards/2000523","cards/2000523_1"],"rare":4}})");

    auto delta = compute_delta(hist_base, mod, MergeMode::Smart, true);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist_base, current_base);
    REQUIRE(remapped != nullptr);

    auto* entry = remapped->as_dict().find("2000523");
    REQUIRE(entry != nullptr);
    auto* resource = entry->as_dict().find("resource");
    REQUIRE(resource != nullptr);
    REQUIRE(resource->type() == DeltaType::Array);
}

// ==================== e2e (compute + apply) ====================

TEST_CASE("delta: e2e scalar to array apply") {
    auto base = JsonDoc::parse(R"({"2000523":{"id":2000523,"name":"test","resource":"cards/2000523","rare":4}})");
    auto mod = JsonDoc::parse(R"({"2000523":{"id":2000523,"name":"test","resource":["cards/2000523","cards/2000523_1"],"rare":4}})");

    auto delta = compute_delta(base, mod, MergeMode::Normal, true);
    REQUIRE(delta != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);
    auto result_doc = state.to_doc();

    auto entry = result_doc.root().obj_get("2000523");
    REQUIRE(entry.valid());
    auto resource = entry.obj_get("resource");
    REQUIRE(resource.valid());
    REQUIRE(resource.is_arr());

    int count = 0;
    auto it = resource.arr_iter();
    JsonVal v;
    while (it.next(v)) ++count;
    REQUIRE(count == 2);
}

TEST_CASE("delta: e2e duplist single to multi") {
    // base: result 中有一个 card 键
    auto base = JsonDoc::parse(R"({
        "id": 9999999,
        "settlement_prior": [{
            "guid": "test-guid-0001",
            "condition": {"s3.x": 1},
            "result": {"clean.s1": 1, "card": [2000123, "a+1", "b+1"]}
        }]
    })");
    // mod: result 中有两个 card 键（duplicate key）
    auto mod = JsonDoc::parse(R"({
        "id": 9999999,
        "settlement_prior": [{
            "guid": "test-guid-0001",
            "condition": {"s3.x": 1},
            "result": {"clean.s1": 1, "card": [2000123, "a+1"], "card": [2000808, "b+1"]}
        }]
    })");

    auto delta = compute_delta(base, mod);
    REQUIRE(delta != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);
    auto result_doc = state.to_doc();

    // 验证输出包含 duplist（两个 card 键）
    auto sp = result_doc.root().obj_get("settlement_prior");
    REQUIRE(sp.valid());
    auto sp_it = sp.arr_iter();
    JsonVal sp_elem;
    REQUIRE(sp_it.next(sp_elem));
    auto result_obj = sp_elem.obj_get("result");
    REQUIRE(result_obj.valid());

    // 遍历 result 对象，统计 card 键出现次数
    int card_count = 0;
    auto obj_it = result_obj.obj_iter();
    JsonVal::ObjEntry oe;
    while (obj_it.next(oe)) {
        if (std::string(oe.key, oe.key_len) == "card") ++card_count;
    }
    REQUIRE(card_count == 2);
}

TEST_CASE("delta: e2e duplist multi mod merge") {
    auto base = JsonDoc::parse(R"({
        "id": 9999999,
        "tips_text": ["orig"],
        "settlement_prior": [{
            "guid": "test-guid-0001",
            "condition": {"s3.x": 1},
            "result": {"clean.s1": 1, "card": [2000123, "a+1", "b+1"]}
        }]
    })");
    // Mod A: 只改 tips_text
    auto mod_a = JsonDoc::parse(R"({
        "id": 9999999,
        "tips_text": ["orig", "new_tip"],
        "settlement_prior": [{
            "guid": "test-guid-0001",
            "condition": {"s3.x": 1},
            "result": {"clean.s1": 1, "card": [2000123, "a+1", "b+1"]}
        }]
    })");
    // Mod B: 改 card 为 duplist + 新增 loot
    auto mod_b = JsonDoc::parse(R"({
        "id": 9999999,
        "tips_text": ["orig"],
        "settlement_prior": [{
            "guid": "test-guid-0001",
            "condition": {"s3.x": 1},
            "result": {"clean.s1": 1, "card": [2000123, "a+1"], "card": [2000808, "b+1"], "loot": 6000005}
        }]
    })");

    auto delta_a = compute_delta(base, mod_a);
    auto delta_b = compute_delta(base, mod_b);
    REQUIRE(delta_a != nullptr);
    REQUIRE(delta_b != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta_a->as_dict(), nullptr, 1, false);
    apply_delta_to_state(state, delta_b->as_dict(), nullptr, 2, false);
    auto result_doc = state.to_doc();

    // 验证 tips_text 有 2 项
    auto tips = result_doc.root().obj_get("tips_text");
    REQUIRE(tips.valid());
    int tips_count = 0;
    auto tips_it = tips.arr_iter();
    JsonVal tv;
    while (tips_it.next(tv)) ++tips_count;
    REQUIRE(tips_count == 2);

    // 验证 loot == 6000005
    auto sp = result_doc.root().obj_get("settlement_prior");
    auto sp_it = sp.arr_iter();
    JsonVal sp_elem;
    REQUIRE(sp_it.next(sp_elem));
    auto result_obj = sp_elem.obj_get("result");
    REQUIRE(result_obj.valid());
    auto loot = result_obj.obj_get("loot");
    REQUIRE(loot.valid());
    REQUIRE(loot.get_int() == 6000005);
}

TEST_CASE("delta: e2e nested array anchors unchanged") {
    auto base = JsonDoc::parse(R"({
        "id": "5002004",
        "cards_slot": {"s4": {"pops": [
            {"guid": "pop-0", "condition": {"s1.is": 100}, "action": {"event_on": 9000}},
            {"guid": "pop-1", "condition": {"s1.is": 200}, "action": {
                "event_on": 9001,
                "begin_guide": {
                    "type": "FILL_COIN",
                    "anim_type": "MouseRightClick",
                    "bind": "UI/Submit",
                    "pos": [-1024, -404],
                    "anchors": [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]],
                    "is_on_in_mobile": true
                }
            }}
        ]}}
    })");
    // mod 改了 begin_guide 的其他字段，anchors 不变
    auto mod = JsonDoc::parse(R"({
        "id": "5002004",
        "cards_slot": {"s4": {"pops": [
            {"guid": "pop-0", "condition": {"s1.is": 100}, "action": {"event_on": 9000}},
            {"guid": "pop-1", "condition": {"s1.is": 200}, "action": {
                "event_on": 9001,
                "begin_guide": {
                    "type": "FILL_COIN",
                    "anim_type": "MouseLeftClick",
                    "bind": "UI/Submit",
                    "pos": [-800, -300],
                    "anchors": [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]],
                    "is_on_in_mobile": false
                }
            }}
        ]}}
    })");

    auto delta = compute_delta(base, mod);
    REQUIRE(delta != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);
    auto result_doc = state.to_doc();

    // 验证 anchors 保持 [[0.5,0.5],[0.5,0.5],[0.5,0.5]]
    auto pops = result_doc.root().obj_get("cards_slot").obj_get("s4").obj_get("pops");
    auto pops_it = pops.arr_iter();
    JsonVal pop;
    pops_it.next(pop); // pop-0
    pops_it.next(pop); // pop-1
    auto guide = pop.obj_get("action").obj_get("begin_guide");
    auto anchors = guide.obj_get("anchors");
    REQUIRE(anchors.valid());
    REQUIRE(anchors.is_arr());

    int anchor_count = 0;
    auto anch_it = anchors.arr_iter();
    JsonVal anch;
    while (anch_it.next(anch)) {
        REQUIRE(anch.is_arr());
        int inner_count = 0;
        auto inner_it = anch.arr_iter();
        JsonVal iv;
        while (inner_it.next(iv)) {
            REQUIRE(iv.get_real() == 0.5);
            ++inner_count;
        }
        REQUIRE(inner_count == 2);
        ++anchor_count;
    }
    REQUIRE(anchor_count == 3);
}

TEST_CASE("delta: e2e nested array anchors adaptive remap") {
    auto hist = JsonDoc::parse(R"({
        "id": "5002004",
        "cards_slot": {"s4": {"pops": [
            {"guid": "pop-0", "condition": {"s1.is": 100}, "action": {"event_on": 9000}},
            {"guid": "pop-1", "condition": {"s1.is": 200}, "action": {
                "event_on": 9001,
                "begin_guide": {
                    "type": "FILL_COIN",
                    "anim_type": "MouseRightClick",
                    "bind": "UI/Submit",
                    "pos": [-1024, -404],
                    "anchors": [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]],
                    "is_on_in_mobile": true
                }
            }}
        ]}}
    })");
    auto mod = JsonDoc::parse(R"({
        "id": "5002004",
        "cards_slot": {"s4": {"pops": [
            {"guid": "pop-0", "condition": {"s1.is": 100}, "action": {"event_on": 9000}},
            {"guid": "pop-1", "condition": {"s1.is": 200}, "action": {
                "event_on": 9001,
                "begin_guide": {
                    "type": "FILL_COIN",
                    "anim_type": "MouseLeftClick",
                    "bind": "UI/Submit",
                    "pos": [-800, -300],
                    "anchors": [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]],
                    "is_on_in_mobile": false
                }
            }}
        ]}}
    })");
    // current == hist（相同）
    auto current = JsonDoc::parse(R"({
        "id": "5002004",
        "cards_slot": {"s4": {"pops": [
            {"guid": "pop-0", "condition": {"s1.is": 100}, "action": {"event_on": 9000}},
            {"guid": "pop-1", "condition": {"s1.is": 200}, "action": {
                "event_on": 9001,
                "begin_guide": {
                    "type": "FILL_COIN",
                    "anim_type": "MouseRightClick",
                    "bind": "UI/Submit",
                    "pos": [-1024, -404],
                    "anchors": [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]],
                    "is_on_in_mobile": true
                }
            }}
        ]}}
    })");

    auto delta = compute_delta(hist, mod, MergeMode::Smart);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped != nullptr);

    auto state = JsonState::from_doc(current);
    apply_delta_to_state(state, remapped->as_dict(), nullptr, 1, false);
    auto result_doc = state.to_doc();

    auto pops = result_doc.root().obj_get("cards_slot").obj_get("s4").obj_get("pops");
    auto pops_it = pops.arr_iter();
    JsonVal pop;
    pops_it.next(pop);
    pops_it.next(pop);
    auto guide = pop.obj_get("action").obj_get("begin_guide");
    auto anchors = guide.obj_get("anchors");
    REQUIRE(anchors.valid());

    int anchor_count = 0;
    auto anch_it = anchors.arr_iter();
    JsonVal anch;
    while (anch_it.next(anch)) {
        int inner_count = 0;
        auto inner_it = anch.arr_iter();
        JsonVal iv;
        while (inner_it.next(iv)) ++inner_count;
        REQUIRE(inner_count == 2);
        ++anchor_count;
    }
    REQUIRE(anchor_count == 3);
}

TEST_CASE("delta: e2e nested array anchors mod changes") {
    auto hist = JsonDoc::parse(R"({
        "id": "5002004",
        "cards_slot": {"s4": {"pops": [
            {"guid": "pop-0", "condition": {"s1.is": 100}, "action": {"event_on": 9000}},
            {"guid": "pop-1", "condition": {"s1.is": 200}, "action": {
                "event_on": 9001,
                "begin_guide": {
                    "type": "FILL_COIN",
                    "anim_type": "MouseRightClick",
                    "anchors": [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]]
                }
            }}
        ]}}
    })");
    // mod 修改了 anchors 值
    auto mod = JsonDoc::parse(R"({
        "id": "5002004",
        "cards_slot": {"s4": {"pops": [
            {"guid": "pop-0", "condition": {"s1.is": 100}, "action": {"event_on": 9000}},
            {"guid": "pop-1", "condition": {"s1.is": 200}, "action": {
                "event_on": 9001,
                "begin_guide": {
                    "type": "FILL_COIN",
                    "anim_type": "MouseLeftClick",
                    "anchors": [[0.0, 1.0], [1.0, 0.0], [0.5, 0.5]]
                }
            }}
        ]}}
    })");
    auto current = JsonDoc::parse(R"({
        "id": "5002004",
        "cards_slot": {"s4": {"pops": [
            {"guid": "pop-0", "condition": {"s1.is": 100}, "action": {"event_on": 9000}},
            {"guid": "pop-1", "condition": {"s1.is": 200}, "action": {
                "event_on": 9001,
                "begin_guide": {
                    "type": "FILL_COIN",
                    "anim_type": "MouseRightClick",
                    "anchors": [[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]]
                }
            }}
        ]}}
    })");

    auto delta = compute_delta(hist, mod, MergeMode::Normal);
    REQUIRE(delta != nullptr);

    auto remapped = remap_delta_to_current(delta->as_dict(), hist, current);
    REQUIRE(remapped != nullptr);

    auto state = JsonState::from_doc(current);
    apply_delta_to_state(state, remapped->as_dict(), nullptr, 1, false);
    auto result_doc = state.to_doc();

    auto pops = result_doc.root().obj_get("cards_slot").obj_get("s4").obj_get("pops");
    auto pops_it = pops.arr_iter();
    JsonVal pop;
    pops_it.next(pop);
    pops_it.next(pop);
    auto guide = pop.obj_get("action").obj_get("begin_guide");
    auto anchors = guide.obj_get("anchors");
    REQUIRE(anchors.valid());

    auto anch_it = anchors.arr_iter();
    JsonVal anch;
    // anchors[0] == [0.0, 1.0]
    REQUIRE(anch_it.next(anch));
    auto a0_it = anch.arr_iter();
    JsonVal a0v;
    REQUIRE(a0_it.next(a0v));
    REQUIRE(a0v.get_real() == 0.0);
    REQUIRE(a0_it.next(a0v));
    REQUIRE(a0v.get_real() == 1.0);

    // anchors[1] == [1.0, 0.0]
    REQUIRE(anch_it.next(anch));
    auto a1_it = anch.arr_iter();
    JsonVal a1v;
    REQUIRE(a1_it.next(a1v));
    REQUIRE(a1v.get_real() == 1.0);
    REQUIRE(a1_it.next(a1v));
    REQUIRE(a1v.get_real() == 0.0);

    // anchors[2] == [0.5, 0.5]
    REQUIRE(anch_it.next(anch));
    auto a2_it = anch.arr_iter();
    JsonVal a2v;
    REQUIRE(a2_it.next(a2v));
    REQUIRE(a2v.get_real() == 0.5);
    REQUIRE(a2_it.next(a2v));
    REQUIRE(a2v.get_real() == 0.5);
}

// ==================== format (scalar to array) ====================

TEST_CASE("delta: format scalar to array change") {
    auto base = JsonDoc::parse(
        R"({"2000523":{"resource":"cards/2000523"}})");
    auto mod = JsonDoc::parse(
        R"({"2000523":{"resource":["cards/2000523","cards/2000523_1"]}})");

    auto delta = compute_delta(base, mod, MergeMode::Normal, true);
    REQUIRE(delta != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);

    auto fmt = state.format(1);
    REQUIRE(fmt.size() > 0);

    // 右侧应包含 "cards/2000523_1" 文本
    bool found_added_text = false;
    bool found_added_kind = false;
    for (size_t i = 0; i < fmt.size(); ++i) {
        if (fmt.right_lines[i].find("cards/2000523_1") != std::string::npos) {
            found_added_text = true;
        }
        if (fmt.right_kinds[i] >= 0 && is_added(static_cast<ChangeKind>(fmt.right_kinds[i]))) {
            found_added_kind = true;
        }
    }
    REQUIRE(found_added_text);
    REQUIRE(found_added_kind);
}
