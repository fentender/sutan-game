#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <fstream>
#include <random>
#include <string>

#include "change_kind.h"
#include "state_node.h"
#include "json_state.h"
#include "json_doc.h"

using namespace sultan;
namespace fs = std::filesystem;

// ── 测试辅助 ──

struct TempDir {
    fs::path path;

    TempDir() {
        std::random_device rd;
        path = fs::temp_directory_path() /
               ("sultan_state_test_" + std::to_string(rd()));
        fs::create_directories(path);
    }

    ~TempDir() {
        std::error_code ec;
        fs::remove_all(path, ec);
    }

    std::string file(const std::string& rel) const {
        return (path / fs::u8path(rel)).u8string();
    }
};

static void write_raw(const std::string& path, const std::string& content) {
    auto fspath = fs::u8path(path);
    fs::create_directories(fspath.parent_path());
    std::ofstream ofs(fspath, std::ios::binary);
    ofs.write(content.data(), static_cast<std::streamsize>(content.size()));
}

// ═══════════════════════════════════════════════════════════
// ChangeKind 测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("state: change_kind base values") {
    REQUIRE(static_cast<uint8_t>(ChangeKind::Origin)  == 0);
    REQUIRE(static_cast<uint8_t>(ChangeKind::Added)   == 1);
    REQUIRE(static_cast<uint8_t>(ChangeKind::Deleted)  == 2);
    REQUIRE(static_cast<uint8_t>(ChangeKind::Changed)  == 3);
    REQUIRE(static_cast<uint8_t>(ChangeKind::MultiMod) == 4);
    REQUIRE(static_cast<uint8_t>(ChangeKind::Override) == 8);
}

TEST_CASE("state: change_kind bitwise or") {
    auto combined = ChangeKind::Added | ChangeKind::MultiMod;
    REQUIRE(static_cast<uint8_t>(combined) == 5);

    combined = ChangeKind::Changed | ChangeKind::Override;
    REQUIRE(static_cast<uint8_t>(combined) == 11);
}

TEST_CASE("state: change_kind base_kind extraction") {
    auto combined = ChangeKind::Changed | ChangeKind::MultiMod;
    REQUIRE(base_kind(combined) == ChangeKind::Changed);

    combined = ChangeKind::Added | ChangeKind::Override;
    REQUIRE(base_kind(combined) == ChangeKind::Added);
}

TEST_CASE("state: change_kind flags extraction") {
    auto combined = ChangeKind::Changed | ChangeKind::MultiMod;
    REQUIRE(change_flags(combined) == ChangeKind::MultiMod);

    combined = ChangeKind::Deleted | ChangeKind::Override | ChangeKind::MultiMod;
    auto flags = change_flags(combined);
    REQUIRE(is_multi_mod(flags));
    REQUIRE(is_override(flags));
}

TEST_CASE("state: change_kind is_* predicates") {
    REQUIRE(is_origin(ChangeKind::Origin));
    REQUIRE(is_added(ChangeKind::Added));
    REQUIRE(is_deleted(ChangeKind::Deleted));
    REQUIRE(is_changed(ChangeKind::Changed));
    REQUIRE_FALSE(is_origin(ChangeKind::Added));
    REQUIRE_FALSE(is_added(ChangeKind::Changed));
}

TEST_CASE("state: change_kind multi_mod flag") {
    REQUIRE(is_multi_mod(ChangeKind::Added | ChangeKind::MultiMod));
    REQUIRE_FALSE(is_multi_mod(ChangeKind::Added));
    REQUIRE(is_added(ChangeKind::Added | ChangeKind::MultiMod));
}

TEST_CASE("state: change_kind override flag") {
    REQUIRE(is_override(ChangeKind::Changed | ChangeKind::Override));
    REQUIRE_FALSE(is_override(ChangeKind::Changed));
    REQUIRE(is_changed(ChangeKind::Changed | ChangeKind::Override));
}

TEST_CASE("state: change_kind combined flags") {
    auto combined = ChangeKind::Added | ChangeKind::MultiMod | ChangeKind::Override;
    REQUIRE(is_added(combined));
    REQUIRE(is_multi_mod(combined));
    REQUIRE(is_override(combined));
    REQUIRE(base_kind(combined) == ChangeKind::Added);
}

// ═══════════════════════════════════════════════════════════
// ScalarValue 测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("state: serialize_scalar null") {
    REQUIRE(serialize_scalar(nullptr) == "null");
}

TEST_CASE("state: serialize_scalar types") {
    REQUIRE(serialize_scalar(true) == "true");
    REQUIRE(serialize_scalar(false) == "false");
    REQUIRE(serialize_scalar(int64_t{42}) == "42");
    REQUIRE(serialize_scalar(int64_t{-7}) == "-7");
    REQUIRE(serialize_scalar(std::string{"hello"}) == "\"hello\"");
    REQUIRE(serialize_scalar(std::string{"a\"b"}) == "\"a\\\"b\"");
}

TEST_CASE("state: serialize_scalar double") {
    auto s = serialize_scalar(3.14);
    REQUIRE(s.find("3.14") != std::string::npos);
    REQUIRE(serialize_scalar(0.0) == "0.0");
}

TEST_CASE("state: scalar_equal same types") {
    REQUIRE(scalar_equal(ScalarValue{int64_t{42}}, ScalarValue{int64_t{42}}));
    REQUIRE_FALSE(scalar_equal(ScalarValue{int64_t{1}}, ScalarValue{int64_t{2}}));
    REQUIRE(scalar_equal(ScalarValue{std::string{"abc"}}, ScalarValue{std::string{"abc"}}));
    REQUIRE(scalar_equal(ScalarValue{nullptr}, ScalarValue{nullptr}));
}

// ═══════════════════════════════════════════════════════════
// from_doc 构建测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("state: from_doc simple object") {
    auto state = JsonState::from_text(R"({"a": 1, "b": "hello"})");
    REQUIRE(state.valid());
    REQUIRE(state.root().is_dict());
    auto& dict = state.root().as_dict();
    REQUIRE(dict.size() == 2);
    REQUIRE(dict.find("a") != nullptr);
    REQUIRE(dict.find("a")->is_element());
    REQUIRE(std::get<int64_t>(dict.find("a")->as_element().value) == 1);
    REQUIRE(std::get<std::string>(dict.find("b")->as_element().value) == "hello");
}

TEST_CASE("state: from_doc nested object") {
    auto state = JsonState::from_text(R"({"a": {"b": 1}})");
    auto& dict = state.root().as_dict();
    REQUIRE(dict.find("a")->is_dict());
    auto& inner = dict.find("a")->as_dict();
    REQUIRE(inner.find("b")->is_element());
    REQUIRE(std::get<int64_t>(inner.find("b")->as_element().value) == 1);
}

TEST_CASE("state: from_doc array") {
    auto state = JsonState::from_text(R"({"a": [1, 2, 3]})");
    auto& dict = state.root().as_dict();
    REQUIRE(dict.find("a")->is_array());
    auto& arr = dict.find("a")->as_array();
    REQUIRE(arr.base_count == 3);
    REQUIRE(arr.indices == std::vector<int>{1, 2, 3});
    REQUIRE(arr.order == std::vector<int>{0, 1, 2, 3, -1});
    REQUIRE(arr.diffs.size() == 3);
    REQUIRE(std::get<int64_t>(arr.diffs[0]->as_element().value) == 1);
}

TEST_CASE("state: from_doc nested array") {
    auto state = JsonState::from_text(R"({"a": [[1], [2]]})");
    auto& arr = state.root().as_dict().find("a")->as_array();
    REQUIRE(arr.diffs.size() == 2);
    REQUIRE(arr.diffs[0]->is_array());
    REQUIRE(arr.diffs[0]->as_array().base_count == 1);
}

TEST_CASE("state: from_doc scalar root") {
    auto state = JsonState::from_text("42");
    REQUIRE(state.valid());
    REQUIRE(state.root().is_element());
    REQUIRE(std::get<int64_t>(state.root().as_element().value) == 42);
}

TEST_CASE("state: from_doc empty object") {
    auto state = JsonState::from_text("{}");
    REQUIRE(state.root().is_dict());
    REQUIRE(state.root().as_dict().size() == 0);
}

TEST_CASE("state: from_doc duplicate keys") {
    auto state = JsonState::from_text(R"({"a": 1, "a": 2})");
    auto& dict = state.root().as_dict();
    REQUIRE(dict.size() == 1);
    auto* a = dict.find("a");
    REQUIRE(a->is_array());
    auto& arr = a->as_array();
    REQUIRE(arr.is_duplist);
    REQUIRE(arr.base_count == 2);
    REQUIRE(std::get<int64_t>(arr.diffs[0]->as_element().value) == 1);
    REQUIRE(std::get<int64_t>(arr.diffs[1]->as_element().value) == 2);
}

TEST_CASE("state: from_doc all types") {
    auto state = JsonState::from_text(
        R"({"n": null, "b": true, "i": 42, "f": 3.14, "s": "str", "o": {}, "a": []})");
    auto& d = state.root().as_dict();
    REQUIRE(std::holds_alternative<std::nullptr_t>(d.find("n")->as_element().value));
    REQUIRE(std::get<bool>(d.find("b")->as_element().value) == true);
    REQUIRE(std::get<int64_t>(d.find("i")->as_element().value) == 42);
    REQUIRE(std::get<double>(d.find("f")->as_element().value) == 3.14);
    REQUIRE(std::get<std::string>(d.find("s")->as_element().value) == "str");
    REQUIRE(d.find("o")->is_dict());
    REQUIRE(d.find("a")->is_array());
}

TEST_CASE("state: from_doc all kinds origin") {
    auto state = JsonState::from_text(R"({"a": 1, "b": [2]})");
    auto& dict = state.root().as_dict();
    REQUIRE(is_origin(dict.find("a")->kind()));
    REQUIRE(is_origin(dict.find("b")->kind()));
    REQUIRE_FALSE(state.root().is_modified());
}

// ═══════════════════════════════════════════════════════════
// to_doc 转换测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("state: to_doc roundtrip simple") {
    std::string input = R"({"a": 1, "b": "hello"})";
    auto state = JsonState::from_text(input);
    auto doc = state.to_doc();
    auto output = doc.to_string(true);
    // 紧凑输出，键排序
    REQUIRE(output.find("\"a\"") != std::string::npos);
    REQUIRE(output.find("\"b\"") != std::string::npos);
    REQUIRE(output.find("1") != std::string::npos);
    REQUIRE(output.find("\"hello\"") != std::string::npos);
}

TEST_CASE("state: to_doc roundtrip nested") {
    auto state = JsonState::from_text(R"({"x": {"y": [1, 2]}})");
    auto doc = state.to_doc();
    auto text = doc.to_string(true);
    REQUIRE(text.find("\"x\"") != std::string::npos);
    REQUIRE(text.find("\"y\"") != std::string::npos);
    REQUIRE(text.find("[1,2]") != std::string::npos);
}

TEST_CASE("state: to_doc skips deleted") {
    auto state = JsonState::from_text(R"({"a": 1, "b": 2})");
    auto& dict = state.root().as_dict();
    auto* a = dict.find("a");
    a->as_element().kind_ = ChangeKind::Deleted;

    auto doc = state.to_doc();
    auto text = doc.to_string(true);
    REQUIRE(text.find("\"a\"") == std::string::npos);
    REQUIRE(text.find("\"b\"") != std::string::npos);
}

TEST_CASE("state: to_doc duplist expansion") {
    auto state = JsonState::from_text(R"({"a": 1, "a": 2})");
    auto doc = state.to_doc();
    auto text = doc.to_string(true);
    // yyjson 紧凑输出中重复键保留
    auto first_pos = text.find("\"a\"");
    REQUIRE(first_pos != std::string::npos);
    auto second_pos = text.find("\"a\"", first_pos + 1);
    REQUIRE(second_pos != std::string::npos);
}

TEST_CASE("state: to_doc array order") {
    auto state = JsonState::from_text(R"({"arr": [10, 20, 30]})");
    auto doc = state.to_doc();
    auto text = doc.to_string(true);
    auto p10 = text.find("10");
    auto p20 = text.find("20");
    auto p30 = text.find("30");
    REQUIRE(p10 < p20);
    REQUIRE(p20 < p30);
}

// ═══════════════════════════════════════════════════════════
// clone 测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("state: clone deep independence") {
    auto state = JsonState::from_text(R"({"a": 1, "b": 2})");
    auto cloned = state.clone();

    // 修改 clone，原树不变
    cloned.root().as_dict().find("a")->as_element().value = int64_t{99};
    REQUIRE(std::get<int64_t>(state.root().as_dict().find("a")->as_element().value) == 1);
    REQUIRE(std::get<int64_t>(cloned.root().as_dict().find("a")->as_element().value) == 99);
}

TEST_CASE("state: clone preserves structure") {
    auto state = JsonState::from_text(R"({"x": {"y": [1, 2]}})");
    auto cloned = state.clone();
    auto doc1 = state.to_doc();
    auto doc2 = cloned.to_doc();
    REQUIRE(doc1.to_string(true) == doc2.to_string(true));
}

TEST_CASE("state: clone preserves metadata") {
    auto state = JsonState::from_text(R"({"a": 1})");
    state.root().as_dict().find("a")->as_element().kind_ = ChangeKind::Changed;
    state.root().as_dict().find("a")->as_element().version = 3;
    state.root().as_dict().find("a")->as_element().old_value = int64_t{0};

    auto cloned = state.clone();
    auto& elem = cloned.root().as_dict().find("a")->as_element();
    REQUIRE(elem.kind_ == ChangeKind::Changed);
    REQUIRE(elem.version == 3);
    REQUIRE(std::get<int64_t>(elem.old_value) == 0);
}

// ═══════════════════════════════════════════════════════════
// format 格式化测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("state: format all origin") {
    auto state = JsonState::from_text(R"({"a": 1, "b": 2})");
    auto r = state.format(1);
    REQUIRE(r.left_lines.size() == r.right_lines.size());
    REQUIRE(r.left_lines == r.right_lines);
    for (auto k : r.left_kinds) {
        REQUIRE(k >= 0); // 无填充行
    }
}

TEST_CASE("state: format added field") {
    auto state = JsonState::from_text(R"({"a": 1, "b": 2})");
    auto& elem = state.root().as_dict().find("b")->as_element();
    elem.kind_ = ChangeKind::Added;
    elem.version = 1;

    auto r = state.format(1);
    REQUIRE(r.left_lines.size() == r.right_lines.size());

    // 找到 "b" 字段行：右侧有值，左侧为空
    bool found_added = false;
    for (size_t i = 0; i < r.size(); ++i) {
        if (r.right_kinds[i] >= 0 && is_added(static_cast<ChangeKind>(r.right_kinds[i]))) {
            found_added = true;
            REQUIRE(r.left_lines[i].empty());
            REQUIRE(r.left_kinds[i] == -1);
            REQUIRE_FALSE(r.right_lines[i].empty());
        }
    }
    REQUIRE(found_added);
}

TEST_CASE("state: format deleted field") {
    auto state = JsonState::from_text(R"({"a": 1, "b": 2})");
    auto& elem = state.root().as_dict().find("a")->as_element();
    elem.kind_ = ChangeKind::Deleted;
    elem.old_value = int64_t{1};
    elem.value = nullptr;
    elem.version = 1;

    auto r = state.format(1);

    bool found_deleted = false;
    for (size_t i = 0; i < r.size(); ++i) {
        if (r.left_kinds[i] >= 0 && is_deleted(static_cast<ChangeKind>(r.left_kinds[i]))) {
            found_deleted = true;
            REQUIRE_FALSE(r.left_lines[i].empty());
            REQUIRE(r.right_lines[i].empty());
            REQUIRE(r.right_kinds[i] == -1);
        }
    }
    REQUIRE(found_deleted);
}

TEST_CASE("state: format changed field") {
    auto state = JsonState::from_text(R"({"a": 1})");
    auto& elem = state.root().as_dict().find("a")->as_element();
    elem.kind_ = ChangeKind::Changed;
    elem.old_value = int64_t{1};
    elem.value = int64_t{99};
    elem.version = 1;

    auto r = state.format(1);

    bool found_changed = false;
    for (size_t i = 0; i < r.size(); ++i) {
        if (r.left_kinds[i] >= 0 && is_changed(static_cast<ChangeKind>(r.left_kinds[i]))) {
            found_changed = true;
            REQUIRE(r.left_lines[i].find("1") != std::string::npos);
            REQUIRE(r.right_lines[i].find("99") != std::string::npos);
        }
    }
    REQUIRE(found_changed);
}

TEST_CASE("state: format highlight version filter") {
    auto state = JsonState::from_text(R"({"a": 1})");
    auto& elem = state.root().as_dict().find("a")->as_element();
    elem.kind_ = ChangeKind::Changed;
    elem.old_value = int64_t{1};
    elem.value = int64_t{99};
    elem.version = 2;

    // highlight_version=1，version=2 不匹配 → 按 ORIGIN 输出
    auto r = state.format(1);
    REQUIRE(r.left_lines == r.right_lines);
    for (auto k : r.left_kinds) {
        REQUIRE((k == -1 || is_origin(static_cast<ChangeKind>(k))));
    }
}

TEST_CASE("state: format alignment") {
    auto state = JsonState::from_text(R"({"a": 1, "b": 2, "c": 3})");
    auto& elem = state.root().as_dict().find("b")->as_element();
    elem.kind_ = ChangeKind::Added;
    elem.version = 1;

    auto r = state.format(1);
    REQUIRE(r.left_lines.size() == r.right_lines.size());
    REQUIRE(r.left_kinds.size() == r.left_lines.size());
    REQUIRE(r.right_kinds.size() == r.right_lines.size());
}

TEST_CASE("state: format nested structure") {
    auto state = JsonState::from_text(R"({"a": {"b": 1}})");
    auto r = state.format(1);
    REQUIRE(r.size() > 1);
    REQUIRE(r.left_lines == r.right_lines);

    // 验证包含大括号
    bool has_open = false, has_close = false;
    for (auto& line : r.left_lines) {
        if (line.find('{') != std::string::npos) has_open = true;
        if (line.find('}') != std::string::npos) has_close = true;
    }
    REQUIRE(has_open);
    REQUIRE(has_close);
}

TEST_CASE("state: format duplist") {
    auto state = JsonState::from_text(R"({"a": 1, "a": 2})");
    auto r = state.format(1);
    REQUIRE(r.left_lines == r.right_lines);

    // 验证 "a" 出现两次（DupList 展开为重复键行）
    int a_count = 0;
    for (auto& line : r.left_lines) {
        if (line.find("\"a\"") != std::string::npos) a_count++;
    }
    REQUIRE(a_count == 2);
}

// ═══════════════════════════════════════════════════════════
// 便利工厂测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("state: from_text basic") {
    auto state = JsonState::from_text(R"({"x": 1})");
    REQUIRE(state.valid());
    REQUIRE(state.root().is_dict());
}

TEST_CASE("state: from_file basic") {
    TempDir tmp;
    auto path = tmp.file("test.json");
    write_raw(path, R"({"key": "value"})");

    auto state = JsonState::from_file(path);
    REQUIRE(state.valid());
    REQUIRE(state.root().is_dict());
    auto& d = state.root().as_dict();
    REQUIRE(std::get<std::string>(d.find("key")->as_element().value) == "value");
}

// ═══════════════════════════════════════════════════════════
// move 语义测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("state: move constructor") {
    auto state = JsonState::from_text(R"({"a": 1})");
    auto moved = std::move(state);
    REQUIRE(moved.valid());
    REQUIRE_FALSE(state.valid());  // NOLINT
}

TEST_CASE("state: move assignment") {
    auto state = JsonState::from_text(R"({"a": 1})");
    JsonState other;
    other = std::move(state);
    REQUIRE(other.valid());
    REQUIRE_FALSE(state.valid());  // NOLINT
}
