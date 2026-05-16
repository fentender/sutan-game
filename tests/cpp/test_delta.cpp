#include <catch2/catch_test_macros.hpp>
#include "similarity.h"
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

namespace {
    JsonDoc tv_doc_ = JsonDoc::parse(R"({
        "i0":0,"i1":1,"i2":2,"i5":5,"i10":10,"i20":20,"i30":30,"i40":40,"i42":42,"i99":99,
        "btrue":true,"snew":"new","sold":"old","sx":"x","null":null
    })");
    JsonVal tv(const char* key) { return tv_doc_.root().obj_get(key); }
}

// ==================== array_match ====================

TEST_CASE("delta: match by guid key") {
    auto base = JsonDoc::parse(R"([{"guid":"a","v":1},{"guid":"b","v":2}])");
    auto mod = JsonDoc::parse(R"([{"guid":"a","v":10},{"guid":"b","v":20}])");
    auto m = match_by_heuristic(base.root(), mod.root());
    REQUIRE(m.pairs.size() == 2);
    REQUIRE(m.unmatched_base.empty());
    REQUIRE(m.unmatched_mod.empty());
}

TEST_CASE("delta: match with added elements") {
    auto base = JsonDoc::parse(R"([{"id":1}])");
    auto mod = JsonDoc::parse(R"([{"id":1},{"id":2}])");
    auto m = match_by_heuristic(base.root(), mod.root());
    REQUIRE(m.pairs.size() == 1);
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
    auto& dict = d->as_dict();
    auto* entry = dict.find("x");
    REQUIRE(entry != nullptr);
    REQUIRE(entry->kind() == ChangeKind::Changed);
    REQUIRE(entry->as_element().value.get_int() == 20);
}

TEST_CASE("delta: compute field added and deleted") {
    auto base = JsonDoc::parse(R"({"a":1,"b":2})");
    auto mod = JsonDoc::parse(R"({"a":1,"c":3})");
    auto d = compute_delta(base, mod);
    REQUIRE(d != nullptr);
    auto& dict = d->as_dict();
    REQUIRE(dict.find("b")->kind() == ChangeKind::Deleted);
    REQUIRE(dict.find("c")->kind() == ChangeKind::Added);
}

TEST_CASE("delta: compute nested dict change") {
    auto base = JsonDoc::parse(R"({"outer":{"inner":1}})");
    auto mod = JsonDoc::parse(R"({"outer":{"inner":2}})");
    auto d = compute_delta(base, mod);
    REQUIRE(d != nullptr);
    auto* inner = d->as_dict().find("outer")->as_dict().find("inner");
    REQUIRE(inner->kind() == ChangeKind::Changed);
    REQUIRE(inner->as_element().value.get_int() == 2);
}

TEST_CASE("delta: compute array element added") {
    auto base = JsonDoc::parse(R"({"arr":[1,2]})");
    auto mod = JsonDoc::parse(R"({"arr":[1,2,3]})");
    auto d = compute_delta(base, mod);
    REQUIRE(d != nullptr);
    auto& arr = d->as_dict().find("arr")->as_array();
    REQUIRE(arr.base_count == 2);
    bool found_added = false;
    for (auto& diff : arr.diffs) {
        if (diff->kind() == ChangeKind::Added) { found_added = true; break; }
    }
    REQUIRE(found_added);
}

TEST_CASE("delta: compute smart mode blocks deletion") {
    auto base = JsonDoc::parse(R"({"a":1,"name":"test"})");
    auto mod = JsonDoc::parse(R"({"a":1})");
    REQUIRE(compute_delta(base, mod, MergeMode::Smart) == nullptr);
}

TEST_CASE("delta: compute smart mode allows condition deletion") {
    auto base = JsonDoc::parse(R"({"condition":{"x":1,"y":2}})");
    auto mod = JsonDoc::parse(R"({"condition":{"x":1}})");
    REQUIRE(compute_delta(base, mod, MergeMode::Smart) != nullptr);
}

TEST_CASE("delta: compute added complex value") {
    auto base = JsonDoc::parse(R"({"a":1})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":{"nested":true}})");
    auto d = compute_delta(base, mod);
    REQUIRE(d != nullptr);
    auto* entry = d->as_dict().find("b");
    REQUIRE(entry->kind() == ChangeKind::Added);
    REQUIRE(entry->as_element().value.is_obj());
}

// ==================== apply_delta ====================

TEST_CASE("delta: apply added scalar field") {
    auto base_doc = JsonDoc::parse(R"({"a":1})");
    auto state = JsonState::from_doc(base_doc);
    DeltaDict delta;
    delta.items.emplace("b", make_delta_element(ChangeKind::Added, tv("i2")));

    apply_delta_to_state(state, delta, nullptr, 1, false);

    auto doc = state.to_doc();
    REQUIRE(doc.root().obj_get("b").get_int() == 2);
}

TEST_CASE("delta: apply_field_delta basic") {
    DeltaElement diff;
    diff.kind_ = ChangeKind::Changed;
    diff.value = tv("i20");

    JsonElementState existing;
    existing.kind_ = ChangeKind::Origin;
    existing.value = tv("i10");

    auto result = apply_field_delta(diff, &existing, 1, false);
    REQUIRE(result == nullptr);
    REQUIRE(existing.value.get_int() == 20);
    REQUIRE(existing.old_value.get_int() == 10);
}

TEST_CASE("delta: apply changed scalar field") {
    auto base_doc = JsonDoc::parse(R"({"x":10})");
    auto state = JsonState::from_doc(base_doc);
    DeltaDict delta;
    delta.items.emplace("x", make_delta_element(ChangeKind::Changed, tv("i20")));

    apply_delta_to_state(state, delta, nullptr, 1, false);

    auto& elem = state.root().as_dict().find("x")->as_element();
    REQUIRE(elem.value.get_int() == 20);
    REQUIRE(elem.old_value.get_int() == 10);
    REQUIRE(base_kind(elem.kind_) == ChangeKind::Changed);
}

TEST_CASE("delta: apply deleted field") {
    auto base_doc = JsonDoc::parse(R"({"a":1,"b":2})");
    auto state = JsonState::from_doc(base_doc);
    DeltaDict delta;
    delta.items.emplace("b", make_delta_element(ChangeKind::Deleted, tv("i2")));

    apply_delta_to_state(state, delta, nullptr, 1, false);

    REQUIRE(base_kind(state.root().as_dict().find("b")->as_element().kind_) == ChangeKind::Deleted);
}

TEST_CASE("delta: apply multi_mod marking") {
    auto base_doc = JsonDoc::parse(R"({"x":1})");
    auto state = JsonState::from_doc(base_doc);

    DeltaDict delta1;
    delta1.items.emplace("x", make_delta_element(ChangeKind::Changed, tv("i10")));
    apply_delta_to_state(state, delta1, nullptr, 1, false);

    DeltaDict delta2;
    delta2.items.emplace("x", make_delta_element(ChangeKind::Changed, tv("i20")));
    apply_delta_to_state(state, delta2, nullptr, 2, false);

    auto& elem = state.root().as_dict().find("x")->as_element();
    REQUIRE(elem.value.get_int() == 20);
    REQUIRE(is_multi_mod(elem.kind_));
}

TEST_CASE("delta: apply override marking") {
    auto base_doc = JsonDoc::parse(R"({"x":1})");
    auto state = JsonState::from_doc(base_doc);
    DeltaDict delta;
    delta.items.emplace("x", make_delta_element(ChangeKind::Changed, tv("i99")));

    apply_delta_to_state(state, delta, nullptr, 1, true);

    REQUIRE(is_override(state.root().as_dict().find("x")->as_element().kind_));
}

TEST_CASE("delta: apply nested dict delta") {
    auto base_doc = JsonDoc::parse(R"({"outer":{"a":1,"b":2}})");
    auto state = JsonState::from_doc(base_doc);

    auto inner_delta = std::make_unique<DeltaDict>();
    inner_delta->as_dict().insert("b", make_delta_element(ChangeKind::Changed, tv("i20")));

    DeltaDict delta;
    delta.items.emplace("outer", std::move(inner_delta));
    apply_delta_to_state(state, delta, nullptr, 1, false);

    REQUIRE(state.root().as_dict().find("outer")->as_dict().find("b")->as_element().value.get_int() == 20);
}

TEST_CASE("delta: apply added complex value") {
    auto base = JsonDoc::parse(R"({"a":1})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":{"nested":true}})");
    auto delta = compute_delta(base, mod);
    REQUIRE(delta != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);

    auto* b = state.root().as_dict().find("b");
    REQUIRE(b != nullptr);
    REQUIRE(b->is_element());
    REQUIRE(b->as_element().value.is_obj());
}

// ==================== e2e (compute + apply) ====================

TEST_CASE("delta: e2e compute then apply scalar changes") {
    auto base_doc = JsonDoc::parse(R"({"name":"alice","age":30,"city":"NYC"})");
    auto mod_doc = JsonDoc::parse(R"({"name":"alice","age":31,"country":"US"})");

    auto delta = compute_delta(base_doc, mod_doc);
    REQUIRE(delta != nullptr);

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
    REQUIRE(result.root().obj_get("data").obj_get("x").get_int() == 10);
    REQUIRE(result.root().obj_get("data").obj_get("y").get_int() == 2);
}

// ==================== serialization roundtrip ====================

TEST_CASE("delta: serialize element roundtrip") {
    auto orig = make_delta_element(ChangeKind::Changed, tv("snew"));
    auto doc = serialize_delta(*orig);
    auto restored = deserialize_delta(doc);

    REQUIRE(restored->type() == DeltaType::Element);
    REQUIRE(restored->as_element().kind_ == ChangeKind::Changed);
    REQUIRE(std::string(restored->as_element().value.get_str()) == "new");
}

TEST_CASE("delta: serialize dict roundtrip") {
    auto dict = std::make_unique<DeltaDict>();
    dict->as_dict().insert("a", make_delta_element(ChangeKind::Added, tv("i1")));
    dict->as_dict().insert("b", make_delta_element(ChangeKind::Deleted, tv("i2")));

    auto doc = serialize_delta(*dict);
    auto restored = deserialize_delta(doc);

    REQUIRE(restored->type() == DeltaType::Dict);
    auto& d = restored->as_dict();
    REQUIRE(d.size() == 2);
    REQUIRE(d.find("a")->kind() == ChangeKind::Added);
    REQUIRE(d.find("b")->kind() == ChangeKind::Deleted);
}

TEST_CASE("delta: serialize array roundtrip") {
    auto arr = std::make_unique<DeltaArray>();
    auto& a = arr->as_array();
    a.base_count = 2;
    a.indices = {1, 3};
    a.order = {0, 1, 3, -1};
    a.is_duplist = false;
    a.diffs.push_back(make_delta_element(ChangeKind::Changed, tv("i10")));
    a.diffs.push_back(make_delta_element(ChangeKind::Added, tv("i30")));

    auto doc = serialize_delta(*arr);
    auto restored = deserialize_delta(doc);

    auto& ra = restored->as_array();
    REQUIRE(ra.base_count == 2);
    REQUIRE(ra.indices == std::vector<int>{1, 3});
    REQUIRE(ra.order == std::vector<int>{0, 1, 3, -1});
    REQUIRE(ra.diffs.size() == 2);
}

// ==================== remap_delta_to_current ====================

TEST_CASE("delta: remap deleted field not in current drops") {
    auto hist = JsonDoc::parse(R"({"a":1,"b":2})");
    auto mod = JsonDoc::parse(R"({"a":1})");
    auto current = JsonDoc::parse(R"({"a":1})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(delta != nullptr);
    REQUIRE_FALSE(remap_delta_to_current(delta->as_dict(), hist, current));
}

TEST_CASE("delta: remap deleted field in current updates old_value") {
    auto hist = JsonDoc::parse(R"({"a":1,"b":2})");
    auto mod = JsonDoc::parse(R"({"a":1})");
    auto current = JsonDoc::parse(R"({"a":1,"b":99})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(remap_delta_to_current(delta->as_dict(), hist, current));
    auto* b = delta->as_dict().find("b");
    REQUIRE(base_kind(b->kind()) == ChangeKind::Deleted);
    REQUIRE(b->as_element().value.get_int() == 99);
}

TEST_CASE("delta: remap added field same in current drops") {
    auto hist = JsonDoc::parse(R"({"a":1})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":2})");
    auto current = JsonDoc::parse(R"({"a":1,"b":2})");

    auto delta = compute_delta(hist, mod);
    REQUIRE_FALSE(remap_delta_to_current(delta->as_dict(), hist, current));
}

TEST_CASE("delta: remap added field different in current recomputes") {
    auto hist = JsonDoc::parse(R"({"a":1})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":2})");
    auto current = JsonDoc::parse(R"({"a":1,"b":5})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(remap_delta_to_current(delta->as_dict(), hist, current));
    auto* b = delta->as_dict().find("b");
    REQUIRE(base_kind(b->kind()) == ChangeKind::Changed);
    REQUIRE(b->as_element().value.get_int() == 2);
}

TEST_CASE("delta: remap changed field not in current converts to added") {
    auto hist = JsonDoc::parse(R"({"a":1,"b":2})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":10})");
    auto current = JsonDoc::parse(R"({"a":1})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(remap_delta_to_current(delta->as_dict(), hist, current));
    auto* b = delta->as_dict().find("b");
    REQUIRE(base_kind(b->kind()) == ChangeKind::Added);
    REQUIRE(b->as_element().value.get_int() == 10);
}

TEST_CASE("delta: remap changed field same in current drops") {
    auto hist = JsonDoc::parse(R"({"a":1,"b":2})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":10})");
    auto current = JsonDoc::parse(R"({"a":1,"b":10})");

    auto delta = compute_delta(hist, mod);
    REQUIRE_FALSE(remap_delta_to_current(delta->as_dict(), hist, current));
}

TEST_CASE("delta: remap nested dict recursion") {
    auto hist = JsonDoc::parse(R"({"d":{"x":1,"y":2}})");
    auto mod = JsonDoc::parse(R"({"d":{"x":10,"y":2}})");
    auto current = JsonDoc::parse(R"({"d":{"x":5,"y":2}})");

    auto delta = compute_delta(hist, mod);
    REQUIRE(remap_delta_to_current(delta->as_dict(), hist, current));
    REQUIRE(delta->as_dict().find("d")->as_dict().find("x")->as_element().value.get_int() == 10);
}

TEST_CASE("delta: remap e2e compute remap apply") {
    auto hist_base = JsonDoc::parse(R"({"a":1,"b":2,"c":3})");
    auto mod = JsonDoc::parse(R"({"a":1,"b":20,"d":4})");
    auto current_base = JsonDoc::parse(R"({"a":10,"b":2,"c":3})");

    auto delta = compute_delta(hist_base, mod);
    REQUIRE(remap_delta_to_current(delta->as_dict(), hist_base, current_base));

    auto state = JsonState::from_doc(current_base);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);
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
    REQUIRE(base_kind(delta_normal->as_dict().find("b")->kind()) == ChangeKind::Deleted);

    auto delta_skip = compute_delta(base, mod, MergeMode::Normal, true);
    REQUIRE(delta_skip->as_dict().find("b") == nullptr);
    REQUIRE(base_kind(delta_skip->as_dict().find("a")->kind()) == ChangeKind::Changed);
}

TEST_CASE("delta: skip_root_deletion still detects nested deletions") {
    auto base = JsonDoc::parse(R"({"a":{"x":1,"y":2}})");
    auto mod = JsonDoc::parse(R"({"a":{"x":10}})");

    auto delta = compute_delta(base, mod, MergeMode::Normal, true);
    REQUIRE(base_kind(delta->as_dict().find("a")->as_dict().find("y")->kind()) == ChangeKind::Deleted);
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

    auto result = match_by_heuristic(
        base_doc.root().obj_get("settlement"),
        mod_doc.root().obj_get("settlement"));

    std::set<int> ub(result.unmatched_base.begin(), result.unmatched_base.end());
    REQUIRE(ub.count(16) == 1);

    std::set<int> um(result.unmatched_mod.begin(), result.unmatched_mod.end());
    REQUIRE(um.count(14) == 1);

    std::unordered_map<int, int> pair_map;
    for (auto& [bi, mi] : result.pairs) pair_map[bi] = mi;
    for (int i = 0; i < 14; ++i) {
        REQUIRE(pair_map.count(i) == 1);
        REQUIRE(pair_map[i] == i);
    }
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
    REQUIRE(remap_delta_to_current(delta->as_dict(), hist, current));

    auto state = JsonState::from_doc(current);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);
    auto result_doc = state.to_doc();

    auto it = result_doc.root().obj_get("settlement").arr_iter();
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
    REQUIRE(remap_delta_to_current(delta->as_dict(), hist, current));

    auto& arr = delta->as_dict().find("settlement")->as_array();
    std::vector<int> inner;
    for (int x : arr.order) {
        if (x != 0 && x != -1) inner.push_back(x);
    }
    REQUIRE(inner == std::vector<int>{1, 3, 4, 5});
}

TEST_CASE("delta: remap array large preserves all origin") {
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
    REQUIRE(remap_delta_to_current(delta->as_dict(), hist, current));

    auto& arr = delta->as_dict().find("settlement")->as_array();
    std::vector<int> inner;
    for (int x : arr.order) {
        if (x != 0 && x != -1) inner.push_back(x);
    }
    REQUIRE(inner.size() == 10);
}

TEST_CASE("delta: remap nested settlement condition real bug") {
    auto hist = JsonDoc::parse(R"({
        "id": "5000002",
        "settlement": [{
            "guid": "f2dde237-b19d-40ab-94f2-8292381277aa",
            "condition": {"s1.is": 2000757, "have.2000056": 1},
            "result_title": "", "result_text": "", "result": {},
            "action": {"event_on": 5321035}
        }]
    })");
    auto mod = JsonDoc::parse(R"({
        "id": "5000002",
        "settlement": [{
            "guid": "f2dde237-b19d-40ab-94f2-8292381277aa",
            "condition": {"s1.is": 2000757, "have.2000056.追随者": 1},
            "result_title": "", "result_text": "", "result": {},
            "action": {"event_on": 5321035}
        }]
    })");
    auto current = JsonDoc::parse(R"({
        "id": "5000002",
        "settlement": [{
            "guid": "f2dde237-b19d-40ab-94f2-8292381277aa",
            "condition": {"s1.is": 2000757, "table_have.2000056": 1, "have.2000056.追随者": 1},
            "result_title": "", "result_text": "", "result": {},
            "action": {"event_on": 5321035}
        }]
    })");

    auto delta = compute_delta(hist, mod, MergeMode::Normal);
    REQUIRE(delta != nullptr);

    if (remap_delta_to_current(delta->as_dict(), hist, current)) {
        auto* settlement = delta->as_dict().find("settlement");
        if (settlement != nullptr && settlement->type() == DeltaType::Array) {
            auto& arr = settlement->as_array();
            if (!arr.diffs.empty() && arr.diffs[0] != nullptr &&
                arr.diffs[0]->type() == DeltaType::Dict) {
                auto* cond = arr.diffs[0]->as_dict().find("condition");
                if (cond != nullptr) {
                    REQUIRE(cond->as_dict().find("have.2000056") == nullptr);
                    REQUIRE(cond->as_dict().find("have.2000056.\xe8\xbf\xbd\xe9\x9a\x8f\xe8\x80\x85") == nullptr);
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

    auto& arr = delta->as_dict().find("2000523")->as_dict().find("resource")->as_array();
    REQUIRE(arr.base_count == 1);
    int added_count = 0;
    for (auto& d : arr.diffs) {
        if (d && base_kind(d->kind()) == ChangeKind::Added) ++added_count;
    }
    REQUIRE(added_count == 1);
}

// ==================== e2e (complex scenarios) ====================

TEST_CASE("delta: e2e scalar to array apply") {
    auto base = JsonDoc::parse(R"({"2000523":{"id":2000523,"name":"test","resource":"cards/2000523","rare":4}})");
    auto mod = JsonDoc::parse(R"({"2000523":{"id":2000523,"name":"test","resource":["cards/2000523","cards/2000523_1"],"rare":4}})");

    auto delta = compute_delta(base, mod, MergeMode::Normal, true);
    REQUIRE(delta != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);
    auto result_doc = state.to_doc();

    auto resource = result_doc.root().obj_get("2000523").obj_get("resource");
    REQUIRE(resource.is_arr());
    int count = 0;
    auto it = resource.arr_iter();
    JsonVal v;
    while (it.next(v)) ++count;
    REQUIRE(count == 2);
}

TEST_CASE("delta: e2e duplist single to multi") {
    auto base = JsonDoc::parse(R"({
        "id": 9999999,
        "settlement_prior": [{
            "guid": "test-guid-0001",
            "condition": {"s3.x": 1},
            "result": {"clean.s1": 1, "card": [2000123, "a+1", "b+1"]}
        }]
    })");
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

    auto sp_it = result_doc.root().obj_get("settlement_prior").arr_iter();
    JsonVal sp_elem;
    REQUIRE(sp_it.next(sp_elem));
    auto result_obj = sp_elem.obj_get("result");

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
    auto mod_a = JsonDoc::parse(R"({
        "id": 9999999,
        "tips_text": ["orig", "new_tip"],
        "settlement_prior": [{
            "guid": "test-guid-0001",
            "condition": {"s3.x": 1},
            "result": {"clean.s1": 1, "card": [2000123, "a+1", "b+1"]}
        }]
    })");
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

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta_a->as_dict(), nullptr, 1, false);
    apply_delta_to_state(state, delta_b->as_dict(), nullptr, 2, false);
    auto result_doc = state.to_doc();

    // tips_text 2 项
    int tips_count = 0;
    auto tips_it = result_doc.root().obj_get("tips_text").arr_iter();
    JsonVal tv_elem;
    while (tips_it.next(tv_elem)) ++tips_count;
    REQUIRE(tips_count == 2);

    // loot == 6000005
    auto sp_it = result_doc.root().obj_get("settlement_prior").arr_iter();
    JsonVal sp_elem;
    REQUIRE(sp_it.next(sp_elem));
    REQUIRE(sp_elem.obj_get("result").obj_get("loot").get_int() == 6000005);
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
    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);
    auto result_doc = state.to_doc();

    auto pops = result_doc.root().obj_get("cards_slot").obj_get("s4").obj_get("pops");
    auto pops_it = pops.arr_iter();
    JsonVal pop;
    pops_it.next(pop);
    pops_it.next(pop);
    auto anchors = pop.obj_get("action").obj_get("begin_guide").obj_get("anchors");
    REQUIRE(anchors.is_arr());

    int anchor_count = 0;
    auto anch_it = anchors.arr_iter();
    JsonVal anch;
    while (anch_it.next(anch)) {
        auto inner_it = anch.arr_iter();
        JsonVal iv;
        while (inner_it.next(iv)) REQUIRE(iv.get_real() == 0.5);
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
    REQUIRE(remap_delta_to_current(delta->as_dict(), hist, current));

    auto state = JsonState::from_doc(current);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);
    auto result_doc = state.to_doc();

    auto pops = result_doc.root().obj_get("cards_slot").obj_get("s4").obj_get("pops");
    auto pops_it = pops.arr_iter();
    JsonVal pop;
    pops_it.next(pop);
    pops_it.next(pop);
    auto anchors = pop.obj_get("action").obj_get("begin_guide").obj_get("anchors");

    auto anch_it = anchors.arr_iter();
    JsonVal anch;
    REQUIRE(anch_it.next(anch));
    auto a0_it = anch.arr_iter();
    JsonVal v;
    REQUIRE(a0_it.next(v)); REQUIRE(v.get_real() == 0.0);
    REQUIRE(a0_it.next(v)); REQUIRE(v.get_real() == 1.0);

    REQUIRE(anch_it.next(anch));
    auto a1_it = anch.arr_iter();
    REQUIRE(a1_it.next(v)); REQUIRE(v.get_real() == 1.0);
    REQUIRE(a1_it.next(v)); REQUIRE(v.get_real() == 0.0);
}

// ==================== array deletion in order ====================

TEST_CASE("delta: array element deletion preserved in order") {
    auto base = JsonDoc::parse(R"({"arr":[{"id":1,"v":"a"},{"id":2,"v":"b"},{"id":3,"v":"c"}]})");
    auto mod = JsonDoc::parse(R"({"arr":[{"id":1,"v":"a"},{"id":3,"v":"c"}]})");

    auto delta = compute_delta(base, mod);
    REQUIRE(delta != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);

    auto& arr = state.root().as_dict().find("arr")->as_array();

    // 被删除的元素（id=2）应该仍在 order 中
    bool found_deleted_in_order = false;
    for (int eid : arr.order) {
        if (eid == 2) { found_deleted_in_order = true; break; }
    }
    REQUIRE(found_deleted_in_order);

    // 被删除的元素应该标记为 Deleted，version=1
    std::unordered_map<int, size_t> id_to_idx;
    for (size_t i = 0; i < arr.indices.size(); ++i)
        id_to_idx[arr.indices[i]] = i;
    auto it = id_to_idx.find(2);
    REQUIRE(it != id_to_idx.end());
    REQUIRE(is_deleted(arr.diffs[it->second]->kind()));
    REQUIRE(arr.diffs[it->second]->as_dict().version == 1);
}

TEST_CASE("delta: format shows deleted array element") {
    auto base = JsonDoc::parse(R"({"arr":[{"id":1,"v":"a"},{"id":2,"v":"b"},{"id":3,"v":"c"}]})");
    auto mod = JsonDoc::parse(R"({"arr":[{"id":1,"v":"a"},{"id":3,"v":"c"}]})");

    auto delta = compute_delta(base, mod);
    REQUIRE(delta != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);

    auto fmt = state.format(1);

    // 被删除的 {"id":2,"v":"b"} 应该出现在左侧（显示为删除）
    bool found_deleted_content = false;
    bool found_deleted_kind = false;
    for (size_t i = 0; i < fmt.size(); ++i) {
        if (fmt.left_lines[i].find("\"id\"") != std::string::npos &&
            fmt.left_lines[i].find("2") != std::string::npos) {
            found_deleted_content = true;
        }
        if (fmt.left_kinds[i] >= 0 && is_deleted(static_cast<ChangeKind>(fmt.left_kinds[i])))
            found_deleted_kind = true;
    }
    REQUIRE(found_deleted_content);
    REQUIRE(found_deleted_kind);
}

TEST_CASE("delta: format shows deleted settlement element (real file)") {
    auto base_path = fixtures_path("rite_5008204_base.json");
    auto mod_path = fixtures_path("rite_5008204_mod.json");
    if (!std::filesystem::exists(base_path) || !std::filesystem::exists(mod_path)) {
        SKIP("fixture files not found");
    }

    auto base = JsonDoc::parse_file(base_path);
    auto mod = JsonDoc::parse_file(mod_path);

    auto delta = compute_delta(base, mod, MergeMode::Adaptive);
    REQUIRE(delta != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);

    auto fmt = state.format(1);
    bool found_deleted_guid = false;
    for (size_t i = 0; i < fmt.size(); ++i) {
        if (fmt.left_kinds[i] >= 0 && is_deleted(static_cast<ChangeKind>(fmt.left_kinds[i])) &&
            fmt.left_lines[i].find("6b695d11") != std::string::npos) {
            found_deleted_guid = true;
        }
    }
    REQUIRE(found_deleted_guid);
}

// ==================== format (scalar to array) ====================

TEST_CASE("delta: format scalar to array change") {
    auto base = JsonDoc::parse(R"({"2000523":{"resource":"cards/2000523"}})");
    auto mod = JsonDoc::parse(R"({"2000523":{"resource":["cards/2000523","cards/2000523_1"]}})");

    auto delta = compute_delta(base, mod, MergeMode::Normal, true);
    REQUIRE(delta != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);

    auto fmt = state.format(1);
    REQUIRE(fmt.size() > 0);

    bool found_added_text = false;
    bool found_added_kind = false;
    for (size_t i = 0; i < fmt.size(); ++i) {
        if (fmt.right_lines[i].find("cards/2000523_1") != std::string::npos)
            found_added_text = true;
        if (fmt.right_kinds[i] >= 0 && is_added(static_cast<ChangeKind>(fmt.right_kinds[i])))
            found_added_kind = true;
    }
    REQUIRE(found_added_text);
    REQUIRE(found_added_kind);
}

// ==================== deleted element skip ====================

TEST_CASE("delta: deleted array element not resurrected by later mod") {
    auto base = JsonDoc::parse(R"({"arr":[{"id":1,"v":"a"},{"id":2,"v":"b"},{"id":3,"v":"c"}]})");
    auto mod_a = JsonDoc::parse(R"({"arr":[{"id":1,"v":"a"},{"id":3,"v":"c"}]})");
    auto mod_b = JsonDoc::parse(R"({"arr":[{"id":1,"v":"a"},{"id":2,"v":"changed"},{"id":3,"v":"c"}]})");

    auto delta_a = compute_delta(base, mod_a);
    auto delta_b = compute_delta(base, mod_b);
    REQUIRE(delta_a != nullptr);
    REQUIRE(delta_b != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta_a->as_dict(), nullptr, 1, false);
    apply_delta_to_state(state, delta_b->as_dict(), nullptr, 2, false);

    // to_doc() 不应含 id=2（已被 ModA 删除，ModB 不能复活它）
    auto result = state.to_doc();
    auto arr_it = result.root().obj_get("arr").arr_iter();
    JsonVal elem;
    while (arr_it.next(elem)) {
        auto id_val = elem.obj_get("id");
        REQUIRE(id_val.get_int() != 2);
    }
}

TEST_CASE("delta: deleted dict field not resurrected by later mod") {
    auto base = JsonDoc::parse(R"({"a":{"x":1,"y":2},"b":3})");
    auto mod_a = JsonDoc::parse(R"({"b":3})");
    auto mod_b = JsonDoc::parse(R"({"a":{"x":99,"y":2},"b":3})");

    auto delta_a = compute_delta(base, mod_a);
    auto delta_b = compute_delta(base, mod_b);
    REQUIRE(delta_a != nullptr);
    REQUIRE(delta_b != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta_a->as_dict(), nullptr, 1, false);
    apply_delta_to_state(state, delta_b->as_dict(), nullptr, 2, false);

    // to_doc() 不应含 "a"（已被 ModA 删除）
    auto result = state.to_doc();
    REQUIRE_FALSE(result.root().obj_get("a").valid());
}

TEST_CASE("delta: deleted scalar field not resurrected by later mod") {
    auto base = JsonDoc::parse(R"({"x":10,"y":20})");
    auto mod_a = JsonDoc::parse(R"({"y":20})");
    auto mod_b = JsonDoc::parse(R"({"x":99,"y":20})");

    auto delta_a = compute_delta(base, mod_a);
    auto delta_b = compute_delta(base, mod_b);
    REQUIRE(delta_a != nullptr);
    REQUIRE(delta_b != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta_a->as_dict(), nullptr, 1, false);
    apply_delta_to_state(state, delta_b->as_dict(), nullptr, 2, false);

    auto result = state.to_doc();
    REQUIRE_FALSE(result.root().obj_get("x").valid());
    REQUIRE(result.root().obj_get("y").get_int() == 20);
}

TEST_CASE("delta: deleted element value is null in state") {
    auto base = JsonDoc::parse(R"({"x":10,"y":20})");
    auto mod = JsonDoc::parse(R"({"y":20})");

    auto delta = compute_delta(base, mod);
    REQUIRE(delta != nullptr);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta->as_dict(), nullptr, 1, false);

    auto& elem = state.root().as_dict().find("x")->as_element();
    REQUIRE(is_deleted(base_kind(elem.kind_)));
    // value 应为无效（null），不应保留原值
    REQUIRE_FALSE(elem.value.valid());
}

TEST_CASE("delta: format deleted then later mod no ghost highlight") {
    auto base = JsonDoc::parse(R"({"arr":[{"id":1,"v":"a"},{"id":2,"v":"b"}]})");
    auto mod_a = JsonDoc::parse(R"({"arr":[{"id":1,"v":"a"}]})");
    auto mod_b = JsonDoc::parse(R"({"arr":[{"id":1,"v":"a"},{"id":2,"v":"changed"}]})");

    auto delta_a = compute_delta(base, mod_a);
    auto delta_b = compute_delta(base, mod_b);

    auto state = JsonState::from_doc(base);
    apply_delta_to_state(state, delta_a->as_dict(), nullptr, 1, false);
    apply_delta_to_state(state, delta_b->as_dict(), nullptr, 2, false);

    // format(2) 不应出现 id=2 相关的高亮（因为被跳过了）
    auto fmt = state.format(2);
    for (size_t i = 0; i < fmt.size(); ++i) {
        if (fmt.right_kinds[i] >= 0) {
            auto ck = static_cast<ChangeKind>(fmt.right_kinds[i]);
            if (is_added(ck) || is_changed(ck)) {
                REQUIRE(fmt.right_lines[i].find("changed") == std::string::npos);
            }
        }
    }
}
