#include <catch2/catch_test_macros.hpp>

#include <string>
#include <unordered_map>
#include <vector>

#include "field_ops.h"
#include "json_doc.h"

using namespace sultan;

// ═══════════════════════════════════════════════════════════
// 提取测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("field_ops: extract_string_values basic") {
    auto doc = JsonDoc::parse(R"({"name":"alice","age":30,"city":"tokyo"})");
    auto names = extract_string_values(doc, "name");
    REQUIRE(names.size() == 1);
    REQUIRE(names[0] == "alice");
}

TEST_CASE("field_ops: extract_string_values nested") {
    auto doc = JsonDoc::parse(R"({
        "a": {"name": "x", "b": {"name": "y"}},
        "name": "z"
    })");
    auto names = extract_string_values(doc, "name");
    REQUIRE(names.size() == 3);
    REQUIRE(names[0] == "x");
    REQUIRE(names[1] == "y");
    REQUIRE(names[2] == "z");
}

TEST_CASE("field_ops: extract_string_values in array") {
    auto doc = JsonDoc::parse(R"({
        "items": [
            {"name": "a"},
            {"name": "b"},
            {"other": 1}
        ]
    })");
    auto names = extract_string_values(doc, "name");
    REQUIRE(names.size() == 2);
    REQUIRE(names[0] == "a");
    REQUIRE(names[1] == "b");
}

TEST_CASE("field_ops: extract_string_values no match") {
    auto doc = JsonDoc::parse(R"({"id": 42, "count": 10})");
    auto result = extract_string_values(doc, "name");
    REQUIRE(result.empty());
}

TEST_CASE("field_ops: extract_int_values basic") {
    auto doc = JsonDoc::parse(R"({
        "cards": {
            "100": {"id": 100, "name": "card_a"},
            "200": {"id": 200, "name": "card_b"}
        }
    })");
    auto ids = extract_int_values(doc, "id");
    REQUIRE(ids.size() == 2);
    REQUIRE(ids[0] == 100);
    REQUIRE(ids[1] == 200);
}

TEST_CASE("field_ops: extract_int_values mixed types") {
    auto doc = JsonDoc::parse(R"({
        "id": 42,
        "sub": {"id": "not_an_int"},
        "list": [{"id": 99}]
    })");
    auto ids = extract_int_values(doc, "id");
    REQUIRE(ids.size() == 2);
    REQUIRE(ids[0] == 42);
    REQUIRE(ids[1] == 99);
}

TEST_CASE("field_ops: extract from empty doc") {
    auto doc1 = JsonDoc::parse("{}");
    REQUIRE(extract_string_values(doc1, "x").empty());
    REQUIRE(extract_int_values(doc1, "x").empty());

    auto doc2 = JsonDoc::parse("[]");
    REQUIRE(extract_string_values(doc2, "x").empty());
    REQUIRE(extract_int_values(doc2, "x").empty());
}

// ═══════════════════════════════════════════════════════════
// 替换测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("field_ops: replace_field_ints basic") {
    auto doc = JsonDoc::parse(R"({"id": 100, "count": 100})");
    auto result = replace_field_ints(doc, "id", {{100, 999}});

    auto ids = extract_int_values(result, "id");
    REQUIRE(ids.size() == 1);
    REQUIRE(ids[0] == 999);

    auto counts = extract_int_values(result, "count");
    REQUIRE(counts.size() == 1);
    REQUIRE(counts[0] == 100);
}

TEST_CASE("field_ops: replace_field_ints only named field") {
    auto doc = JsonDoc::parse(R"({"id": 42, "ref_id": 42, "damage": 42})");
    auto result = replace_field_ints(doc, "id", {{42, 99}});

    REQUIRE(extract_int_values(result, "id")[0] == 99);
    REQUIRE(extract_int_values(result, "ref_id")[0] == 42);
    REQUIRE(extract_int_values(result, "damage")[0] == 42);
}

TEST_CASE("field_ops: replace_field_ints nested") {
    auto doc = JsonDoc::parse(R"({
        "100": {"id": 100, "name": "a"},
        "200": {"id": 200, "sub": {"id": 100}}
    })");
    auto result = replace_field_ints(doc, "id", {{100, 999}});
    auto ids = extract_int_values(result, "id");
    REQUIRE(ids.size() == 3);
    REQUIRE(ids[0] == 999);
    REQUIRE(ids[1] == 200);
    REQUIRE(ids[2] == 999);
}

TEST_CASE("field_ops: replace_field_ints no match") {
    auto doc = JsonDoc::parse(R"({"id": 42})");
    auto result = replace_field_ints(doc, "id", {{999, 1}});
    REQUIRE(extract_int_values(result, "id")[0] == 42);
}

TEST_CASE("field_ops: replace_field_strs basic") {
    auto doc = JsonDoc::parse(R"({"name": "old", "desc": "old"})");
    auto result = replace_field_strs(doc, "name", {{"old", "new"}});

    REQUIRE(extract_string_values(result, "name")[0] == "new");
    REQUIRE(extract_string_values(result, "desc")[0] == "old");
}

TEST_CASE("field_ops: replace_field_strs exact match") {
    auto doc = JsonDoc::parse(R"({"code": "abc_old", "code2": "abc"})");
    auto result = replace_field_strs(doc, "code", {{"abc", "xyz"}});

    REQUIRE(extract_string_values(result, "code")[0] == "abc_old");

    auto result2 = replace_field_strs(doc, "code2", {{"abc", "xyz"}});
    REQUIRE(extract_string_values(result2, "code2")[0] == "xyz");
}

TEST_CASE("field_ops: replace_root_keys basic") {
    auto doc = JsonDoc::parse(R"({"100": {"name": "a"}, "200": {"name": "b"}})");
    auto result = replace_root_keys(doc, {{"100", "999"}});
    auto text = result.to_string(true);
    REQUIRE(text.find("\"999\"") != std::string::npos);
    REQUIRE(text.find("\"100\"") == std::string::npos);
    REQUIRE(text.find("\"200\"") != std::string::npos);
}

TEST_CASE("field_ops: replace_root_keys not recursive") {
    auto doc = JsonDoc::parse(R"({"k": {"k": "v"}})");
    auto result = replace_root_keys(doc, {{"k", "replaced"}});
    auto text = result.to_string(true);

    auto check = JsonDoc::parse(text, false);
    auto inner = extract_string_values(check, "k");
    REQUIRE(inner.size() == 1);
    REQUIRE(inner[0] == "v");
}

TEST_CASE("field_ops: replace preserves original") {
    auto doc = JsonDoc::parse(R"({"id": 42})");
    auto result = replace_field_ints(doc, "id", {{42, 99}});

    REQUIRE(extract_int_values(doc, "id")[0] == 42);
    REQUIRE(extract_int_values(result, "id")[0] == 99);
}

TEST_CASE("field_ops: replace on empty doc") {
    auto doc = JsonDoc::parse("{}");
    REQUIRE(replace_field_ints(doc, "id", {{1, 2}}).valid());
    REQUIRE(replace_field_strs(doc, "name", {{"a", "b"}}).valid());
    REQUIRE(replace_root_keys(doc, {{"x", "y"}}).valid());
}
