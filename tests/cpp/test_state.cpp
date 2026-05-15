#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <fstream>
#include <random>
#include <string>

#include "change_kind.h"
#include "state_node.h"
#include "json_state.h"
#include "json_doc.h"
#include "json_val.h"

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
// from_doc 构建测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("state: from_doc comprehensive") {
    auto doc = JsonDoc::parse(
        R"({"n": null, "b": true, "i": 42, "f": 3.14, "s": "str", "o": {"inner": 1}, "a": [1, 2, 3]})");
    auto state = JsonState::from_doc(doc);

    REQUIRE(state.valid());
    auto& d = state.root().as_dict();

    // 标量类型
    REQUIRE(d.find("n")->as_element().value.is_null());
    REQUIRE(d.find("b")->as_element().value.get_bool() == true);
    REQUIRE(d.find("i")->as_element().value.get_int() == 42);
    REQUIRE(d.find("f")->as_element().value.get_real() == 3.14);
    REQUIRE(std::string(d.find("s")->as_element().value.get_str()) == "str");

    // 嵌套对象
    REQUIRE(d.find("o")->is_dict());
    REQUIRE(d.find("o")->as_dict().find("inner")->as_element().value.get_int() == 1);

    // 数组
    REQUIRE(d.find("a")->is_array());
    auto& arr = d.find("a")->as_array();
    REQUIRE(arr.base_count == 3);
    REQUIRE(arr.indices == std::vector<int>{1, 2, 3});
    REQUIRE(arr.order == std::vector<int>{0, 1, 2, 3, -1});
    REQUIRE(arr.diffs[0]->as_element().value.get_int() == 1);

    // 全部 origin
    REQUIRE_FALSE(state.root().is_modified());
}

TEST_CASE("state: from_doc duplist") {
    auto doc = JsonDoc::parse(R"({"a": 1, "a": 2})");
    auto state = JsonState::from_doc(doc);

    auto& dict = state.root().as_dict();
    REQUIRE(dict.size() == 1);
    auto* a = dict.find("a");
    REQUIRE(a->is_array());
    auto& arr = a->as_array();
    REQUIRE(arr.is_duplist);
    REQUIRE(arr.base_count == 2);
    REQUIRE(arr.diffs[0]->as_element().value.get_int() == 1);
    REQUIRE(arr.diffs[1]->as_element().value.get_int() == 2);
}

TEST_CASE("state: from_doc nested array") {
    auto doc = JsonDoc::parse(R"({"a": [[1], [2]]})");
    auto state = JsonState::from_doc(doc);

    auto& arr = state.root().as_dict().find("a")->as_array();
    REQUIRE(arr.diffs.size() == 2);
    REQUIRE(arr.diffs[0]->is_array());
    REQUIRE(arr.diffs[0]->as_array().base_count == 1);
}

TEST_CASE("state: from_file roundtrip") {
    TempDir tmp;
    auto path = tmp.file("test.json");
    write_raw(path, R"({"key": "value"})");

    auto doc = JsonDoc::parse_file(path);
    auto state = JsonState::from_doc(doc);
    REQUIRE(state.valid());
    REQUIRE(std::string(state.root().as_dict().find("key")->as_element().value.get_str()) == "value");
}

// ═══════════════════════════════════════════════════════════
// to_doc 转换测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("state: to_doc roundtrip") {
    auto doc = JsonDoc::parse(R"({"x": {"y": [10, 20, 30]}})");
    auto state = JsonState::from_doc(doc);
    auto out = state.to_doc();
    auto text = out.to_string(true);

    REQUIRE(text.find("\"x\"") != std::string::npos);
    REQUIRE(text.find("\"y\"") != std::string::npos);

    // 数组顺序保持
    auto p10 = text.find("10");
    auto p20 = text.find("20");
    auto p30 = text.find("30");
    REQUIRE(p10 < p20);
    REQUIRE(p20 < p30);
}

TEST_CASE("state: to_doc skips deleted") {
    auto doc = JsonDoc::parse(R"({"a": 1, "b": 2})");
    auto state = JsonState::from_doc(doc);
    state.root().as_dict().find("a")->as_element().kind_ = ChangeKind::Deleted;

    auto out = state.to_doc();
    auto text = out.to_string(true);
    REQUIRE(text.find("\"a\"") == std::string::npos);
    REQUIRE(text.find("\"b\"") != std::string::npos);
}

TEST_CASE("state: to_doc duplist expansion") {
    auto doc = JsonDoc::parse(R"({"a": 1, "a": 2})");
    auto state = JsonState::from_doc(doc);
    auto out = state.to_doc();
    auto text = out.to_string(true);

    auto first_pos = text.find("\"a\"");
    REQUIRE(first_pos != std::string::npos);
    auto second_pos = text.find("\"a\"", first_pos + 1);
    REQUIRE(second_pos != std::string::npos);
}

// ═══════════════════════════════════════════════════════════
// clone 测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("state: clone independence and metadata") {
    auto doc = JsonDoc::parse(R"({"a": 1, "b": {"c": 2}})");
    auto state = JsonState::from_doc(doc);

    // 设置 metadata
    auto zero_doc = JsonDoc::parse("0");
    auto& elem = state.root().as_dict().find("a")->as_element();
    elem.kind_ = ChangeKind::Changed;
    elem.version = 3;
    elem.old_value = zero_doc.root();

    auto cloned = state.clone();

    // clone 保持 metadata
    auto& cloned_elem = cloned.root().as_dict().find("a")->as_element();
    REQUIRE(cloned_elem.kind_ == ChangeKind::Changed);
    REQUIRE(cloned_elem.version == 3);
    REQUIRE(cloned_elem.old_value.get_int() == 0);

    // 修改 clone 不影响原始
    auto new_doc = JsonDoc::parse("99");
    cloned_elem.value = new_doc.root();
    REQUIRE(state.root().as_dict().find("a")->as_element().value.get_int() == 1);
    REQUIRE(cloned_elem.value.get_int() == 99);

    // clone 结构完整，可序列化
    auto out1 = state.to_doc();
    // clone 的值已改，不再比较输出相等
}

// ═══════════════════════════════════════════════════════════
// format 格式化测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("state: format all origin") {
    auto doc = JsonDoc::parse(R"({"a": 1, "b": 2})");
    auto state = JsonState::from_doc(doc);
    auto r = state.format(1);

    REQUIRE(r.left_lines.size() == r.right_lines.size());
    REQUIRE(r.left_lines == r.right_lines);
    for (auto k : r.left_kinds) {
        REQUIRE(k >= 0);
    }
}

TEST_CASE("state: format added field") {
    auto doc = JsonDoc::parse(R"({"a": 1, "b": 2})");
    auto state = JsonState::from_doc(doc);
    auto& elem = state.root().as_dict().find("b")->as_element();
    elem.kind_ = ChangeKind::Added;
    elem.version = 1;

    auto r = state.format(1);

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
    auto doc = JsonDoc::parse(R"({"a": 1, "b": 2})");
    auto state = JsonState::from_doc(doc);
    auto& elem = state.root().as_dict().find("a")->as_element();
    elem.kind_ = ChangeKind::Deleted;
    elem.old_value = elem.value;
    elem.value = JsonVal{};
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
    auto doc = JsonDoc::parse(R"({"a": 1})");
    auto new_doc = JsonDoc::parse("99");
    auto state = JsonState::from_doc(doc);
    auto& elem = state.root().as_dict().find("a")->as_element();
    elem.kind_ = ChangeKind::Changed;
    elem.old_value = elem.value;
    elem.value = new_doc.root();
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
    auto doc = JsonDoc::parse(R"({"a": 1})");
    auto new_doc = JsonDoc::parse("99");
    auto state = JsonState::from_doc(doc);
    auto& elem = state.root().as_dict().find("a")->as_element();
    elem.kind_ = ChangeKind::Changed;
    elem.old_value = elem.value;
    elem.value = new_doc.root();
    elem.version = 2;

    // highlight_version=1，version=2 不匹配 → 按 ORIGIN 输出
    auto r = state.format(1);
    REQUIRE(r.left_lines == r.right_lines);
}

TEST_CASE("state: format duplist") {
    auto doc = JsonDoc::parse(R"({"a": 1, "a": 2})");
    auto state = JsonState::from_doc(doc);
    auto r = state.format(1);
    REQUIRE(r.left_lines == r.right_lines);

    int a_count = 0;
    for (auto& line : r.left_lines) {
        if (line.find("\"a\"") != std::string::npos) a_count++;
    }
    REQUIRE(a_count == 2);
}
