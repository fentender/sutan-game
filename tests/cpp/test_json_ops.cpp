#include <catch2/catch_test_macros.hpp>

#include <string>
#include <unordered_map>
#include <vector>

#include "json_ops.h"
#include "json_doc.h"

using namespace sultan;

// ═══════════════════════════════════════════════════════════
// 提取测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("json_ops: extract_string_values basic") {
    auto doc = JsonDoc::parse(R"({"name":"alice","age":30,"city":"tokyo"})");
    auto names = extract_string_values(doc, "name");
    REQUIRE(names.size() == 1);
    REQUIRE(names[0] == "alice");
}

TEST_CASE("json_ops: extract_string_values nested") {
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

TEST_CASE("json_ops: extract_string_values in array") {
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

TEST_CASE("json_ops: extract_string_values no match") {
    auto doc = JsonDoc::parse(R"({"id": 42, "count": 10})");
    auto result = extract_string_values(doc, "name");
    REQUIRE(result.empty());
}

TEST_CASE("json_ops: extract_int_values basic") {
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

TEST_CASE("json_ops: extract_int_values mixed types") {
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

TEST_CASE("json_ops: extract from empty doc") {
    auto doc1 = JsonDoc::parse("{}");
    REQUIRE(extract_string_values(doc1, "x").empty());
    REQUIRE(extract_int_values(doc1, "x").empty());

    auto doc2 = JsonDoc::parse("[]");
    REQUIRE(extract_string_values(doc2, "x").empty());
    REQUIRE(extract_int_values(doc2, "x").empty());
}

TEST_CASE("json_ops: extract_root_keys basic") {
    auto doc = JsonDoc::parse(R"({"100":{"id":100},"200":{"id":200},"300":{}})");
    auto keys = extract_root_keys(doc);
    REQUIRE(keys.size() == 3);
    REQUIRE(keys[0] == "100");
    REQUIRE(keys[1] == "200");
    REQUIRE(keys[2] == "300");
}

TEST_CASE("json_ops: extract_root_keys empty") {
    auto doc = JsonDoc::parse("{}");
    REQUIRE(extract_root_keys(doc).empty());
}

TEST_CASE("json_ops: extract_root_keys non-object") {
    auto doc = JsonDoc::parse("[1,2,3]");
    REQUIRE(extract_root_keys(doc).empty());
}

// ═══════════════════════════════════════════════════════════
// 替换测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("json_ops: replace_field_ints basic") {
    auto doc = JsonDoc::parse(R"({"id": 100, "count": 100})");
    auto result = replace_field_ints(doc, "id", {{100, 999}});

    auto ids = extract_int_values(result, "id");
    REQUIRE(ids.size() == 1);
    REQUIRE(ids[0] == 999);

    auto counts = extract_int_values(result, "count");
    REQUIRE(counts.size() == 1);
    REQUIRE(counts[0] == 100);
}

TEST_CASE("json_ops: replace_field_ints only named field") {
    auto doc = JsonDoc::parse(R"({"id": 42, "ref_id": 42, "damage": 42})");
    auto result = replace_field_ints(doc, "id", {{42, 99}});

    REQUIRE(extract_int_values(result, "id")[0] == 99);
    REQUIRE(extract_int_values(result, "ref_id")[0] == 42);
    REQUIRE(extract_int_values(result, "damage")[0] == 42);
}

TEST_CASE("json_ops: replace_field_ints nested") {
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

TEST_CASE("json_ops: replace_field_ints no match") {
    auto doc = JsonDoc::parse(R"({"id": 42})");
    auto result = replace_field_ints(doc, "id", {{999, 1}});
    REQUIRE(extract_int_values(result, "id")[0] == 42);
}

TEST_CASE("json_ops: replace_field_strs basic") {
    auto doc = JsonDoc::parse(R"({"name": "old", "desc": "old"})");
    auto result = replace_field_strs(doc, "name", {{"old", "new"}});

    REQUIRE(extract_string_values(result, "name")[0] == "new");
    REQUIRE(extract_string_values(result, "desc")[0] == "old");
}

TEST_CASE("json_ops: replace_field_strs exact match") {
    auto doc = JsonDoc::parse(R"({"code": "abc_old", "code2": "abc"})");
    auto result = replace_field_strs(doc, "code", {{"abc", "xyz"}});

    REQUIRE(extract_string_values(result, "code")[0] == "abc_old");

    auto result2 = replace_field_strs(doc, "code2", {{"abc", "xyz"}});
    REQUIRE(extract_string_values(result2, "code2")[0] == "xyz");
}

TEST_CASE("json_ops: replace_root_keys basic") {
    auto doc = JsonDoc::parse(R"({"100": {"name": "a"}, "200": {"name": "b"}})");
    auto result = replace_root_keys(doc, {{"100", "999"}});
    auto text = result.to_string(true);
    REQUIRE(text.find("\"999\"") != std::string::npos);
    REQUIRE(text.find("\"100\"") == std::string::npos);
    REQUIRE(text.find("\"200\"") != std::string::npos);
}

TEST_CASE("json_ops: replace_root_keys not recursive") {
    auto doc = JsonDoc::parse(R"({"k": {"k": "v"}})");
    auto result = replace_root_keys(doc, {{"k", "replaced"}});
    auto text = result.to_string(true);

    auto check = JsonDoc::parse(text, false);
    auto inner = extract_string_values(check, "k");
    REQUIRE(inner.size() == 1);
    REQUIRE(inner[0] == "v");
}

TEST_CASE("json_ops: replace preserves original") {
    auto doc = JsonDoc::parse(R"({"id": 42})");
    auto result = replace_field_ints(doc, "id", {{42, 99}});

    REQUIRE(extract_int_values(doc, "id")[0] == 42);
    REQUIRE(extract_int_values(result, "id")[0] == 99);
}

TEST_CASE("json_ops: replace on empty doc") {
    auto doc = JsonDoc::parse("{}");
    REQUIRE(replace_field_ints(doc, "id", {{1, 2}}).valid());
    REQUIRE(replace_field_strs(doc, "name", {{"a", "b"}}).valid());
    REQUIRE(replace_root_keys(doc, {{"x", "y"}}).valid());
}

// ═══════════════════════════════════════════════════════════
// remap_all_ints 测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("json_ops: remap_all_ints basic") {
    auto doc = JsonDoc::parse(R"({"id":100,"count":200,"name":"test"})");
    auto result = remap_all_ints(doc, {{100, 999}, {200, 888}});
    auto text = result.to_string(true);
    REQUIRE(text.find("999") != std::string::npos);
    REQUIRE(text.find("888") != std::string::npos);
    REQUIRE(text.find("\"test\"") != std::string::npos);
}

TEST_CASE("json_ops: remap_all_ints nested") {
    auto doc = JsonDoc::parse(R"({"a":{"b":{"id":42}},"arr":[42,99]})");
    auto result = remap_all_ints(doc, {{42, 100}});
    auto text = result.to_string(true);
    // "id":100 in nested + arr[0]=100, arr[1]=99 unchanged
    REQUIRE(text.find("\"id\":100") != std::string::npos);
    REQUIRE(text.find("99") != std::string::npos);
}

TEST_CASE("json_ops: remap_all_ints preserves original") {
    auto doc = JsonDoc::parse(R"({"id":100})");
    auto result = remap_all_ints(doc, {{100, 200}});
    REQUIRE(doc.to_string(true).find("100") != std::string::npos);
    REQUIRE(result.to_string(true).find("200") != std::string::npos);
}

TEST_CASE("json_ops: remap_all_ints preserves duplicate keys") {
    auto doc = JsonDoc::parse(R"({"type":100,"type":200,"name":"x"})");
    auto result = remap_all_ints(doc, {{100, 111}});
    auto text = result.to_string(true);
    // 111 replaced, 200 untouched, both "type" keys preserved
    REQUIRE(text.find("111") != std::string::npos);
    REQUIRE(text.find("200") != std::string::npos);
    // count occurrences of "type"
    size_t count = 0;
    size_t pos = 0;
    while ((pos = text.find("\"type\"", pos)) != std::string::npos) {
        ++count;
        pos += 6;
    }
    REQUIRE(count == 2);
}

// ═══════════════════════════════════════════════════════════
// remap_all_str_ids 测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("json_ops: remap_all_str_ids in values") {
    auto doc = JsonDoc::parse(R"({"text":"see card 1234567 here"})");
    auto result = remap_all_str_ids(doc, {{"1234567", "9999999"}});
    auto text = result.to_string(true);
    REQUIRE(text.find("9999999") != std::string::npos);
    REQUIRE(text.find("1234567") == std::string::npos);
}

TEST_CASE("json_ops: remap_all_str_ids in keys") {
    auto doc = JsonDoc::parse(R"({"field_1234567_end":"value"})");
    auto result = remap_all_str_ids(doc, {{"1234567", "9999999"}});
    auto text = result.to_string(true);
    REQUIRE(text.find("field_9999999_end") != std::string::npos);
}

TEST_CASE("json_ops: remap_all_str_ids boundary") {
    // 8-digit number should NOT be replaced
    auto doc = JsonDoc::parse(R"({"x":"12345678"})");
    auto result = remap_all_str_ids(doc, {{"1234567", "9999999"}});
    auto text = result.to_string(true);
    REQUIRE(text.find("12345678") != std::string::npos);
    REQUIRE(text.find("9999999") == std::string::npos);
}

TEST_CASE("json_ops: remap_all_str_ids no match") {
    auto doc = JsonDoc::parse(R"({"x":"hello","y":42})");
    auto result = remap_all_str_ids(doc, {{"1234567", "9999999"}});
    auto text = result.to_string(true);
    REQUIRE(text.find("hello") != std::string::npos);
}

TEST_CASE("json_ops: remap_all_str_ids preserves duplicate keys") {
    auto doc = JsonDoc::parse(R"({"type":"1234567","type":"other"})");
    auto result = remap_all_str_ids(doc, {{"1234567", "9999999"}});
    auto text = result.to_string(true);
    REQUIRE(text.find("9999999") != std::string::npos);
    REQUIRE(text.find("\"other\"") != std::string::npos);
    size_t count = 0;
    size_t pos = 0;
    while ((pos = text.find("\"type\"", pos)) != std::string::npos) {
        ++count;
        pos += 6;
    }
    REQUIRE(count == 2);
}

// ═══════════════════════════════════════════════════════════
// extract_root_field_ints / extract_root_field_strs 测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("json_ops: extract_root_field_ints basic") {
    auto doc = JsonDoc::parse(R"({
        "code_a": {"id": 100, "name": "n1"},
        "code_b": {"id": 200, "name": "n2"}
    })");
    auto result = extract_root_field_ints(doc, "id");
    REQUIRE(result.size() == 2);
    REQUIRE(result.at("code_a") == 100);
    REQUIRE(result.at("code_b") == 200);
}

TEST_CASE("json_ops: extract_root_field_ints skips non-object entries") {
    auto doc = JsonDoc::parse(R"({
        "a": {"id": 1},
        "b": "not_an_object",
        "c": [1, 2, 3]
    })");
    auto result = extract_root_field_ints(doc, "id");
    REQUIRE(result.size() == 1);
    REQUIRE(result.at("a") == 1);
}

TEST_CASE("json_ops: extract_root_field_ints skips wrong type") {
    auto doc = JsonDoc::parse(R"({
        "a": {"id": "string_id"},
        "b": {"id": 42}
    })");
    auto result = extract_root_field_ints(doc, "id");
    REQUIRE(result.size() == 1);
    REQUIRE(result.at("b") == 42);
}

TEST_CASE("json_ops: extract_root_field_ints empty doc") {
    auto doc = JsonDoc::parse("{}");
    REQUIRE(extract_root_field_ints(doc, "id").empty());
}

TEST_CASE("json_ops: extract_root_field_ints non-object root") {
    auto doc = JsonDoc::parse("[1, 2]");
    REQUIRE(extract_root_field_ints(doc, "id").empty());
}

TEST_CASE("json_ops: extract_root_field_strs basic") {
    auto doc = JsonDoc::parse(R"({
        "code_a": {"id": 100, "name": "alice"},
        "code_b": {"id": 200, "name": "bob"}
    })");
    auto result = extract_root_field_strs(doc, "name");
    REQUIRE(result.size() == 2);
    REQUIRE(result.at("code_a") == "alice");
    REQUIRE(result.at("code_b") == "bob");
}

TEST_CASE("json_ops: extract_root_field_strs missing field") {
    auto doc = JsonDoc::parse(R"({
        "a": {"name": "x"},
        "b": {"other": "y"}
    })");
    auto result = extract_root_field_strs(doc, "name");
    REQUIRE(result.size() == 1);
    REQUIRE(result.at("a") == "x");
}

TEST_CASE("json_ops: extract_root_field_strs skips int values") {
    auto doc = JsonDoc::parse(R"({
        "a": {"val": 123},
        "b": {"val": "text"}
    })");
    auto result = extract_root_field_strs(doc, "val");
    REQUIRE(result.size() == 1);
    REQUIRE(result.at("b") == "text");
}

