#include <catch2/catch_test_macros.hpp>
#include "similarity.h"
#include "delta_rules.h"
#include "delta_node.h"
#include "compute_delta.h"
#include "array_match.h"
#include "json_doc.h"
#include "json_val.h"

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
