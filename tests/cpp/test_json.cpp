#include <catch2/catch_test_macros.hpp>

#include <filesystem>
#include <fstream>
#include <random>
#include <string>

#include "json_cleaner.h"
#include "json_doc.h"

using namespace sultan;
namespace fs = std::filesystem;

// ── 测试辅助 ──

struct TempDir {
    fs::path path;

    TempDir() {
        std::random_device rd;
        path = fs::temp_directory_path() /
               ("sultan_json_test_" + std::to_string(rd()));
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
// 清洗测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: fix_missing_commas after string value") {
    // 逗号插入在空白前，空白保留
    auto result = fix_missing_commas(R"({"a":"x" "b":"y"})");
    REQUIRE(result == R"({"a":"x", "b":"y"})");
}

TEST_CASE("json: fix_missing_commas after number") {
    auto result = fix_missing_commas(R"({"a":1 "b":2})");
    REQUIRE(result == R"({"a":1, "b":2})");
}

TEST_CASE("json: fix_missing_commas after bool") {
    auto result = fix_missing_commas(R"({"a":true "b":false})");
    REQUIRE(result == R"({"a":true, "b":false})");
}

TEST_CASE("json: fix_missing_commas after null") {
    auto result = fix_missing_commas(R"({"a":null "b":1})");
    REQUIRE(result == R"({"a":null, "b":1})");
}

TEST_CASE("json: fix_missing_commas after close brace") {
    auto result = fix_missing_commas(R"({"a":{} "b":1})");
    REQUIRE(result == R"({"a":{}, "b":1})");
}

TEST_CASE("json: fix_missing_commas after close bracket") {
    auto result = fix_missing_commas(R"({"a":[] "b":1})");
    REQUIRE(result == R"({"a":[], "b":1})");
}

TEST_CASE("json: fix_missing_commas after negative number") {
    auto result = fix_missing_commas(R"({"a":-1 "b":2})");
    REQUIRE(result == R"({"a":-1, "b":2})");
}

TEST_CASE("json: fix_missing_commas no false positive") {
    std::string input = R"({"a":1,"b":"hello","c":true})";
    auto result = fix_missing_commas(input);
    REQUIRE(result == input);
}

TEST_CASE("json: fix_missing_commas preserves strings") {
    auto result = fix_missing_commas(R"({"a":"hello world" "b":1})");
    REQUIRE(result == R"({"a":"hello world", "b":1})");
}

TEST_CASE("json: strip_duplicate_commas basic") {
    auto result = strip_duplicate_commas(R"({"a":1,,,"b":2})");
    REQUIRE(result == R"({"a":1,"b":2})");
}

TEST_CASE("json: strip_duplicate_commas with whitespace") {
    auto result = strip_duplicate_commas("{\"a\":1, , \"b\":2}");
    // 第一个逗号保留，后续空白保留，多余逗号跳过，空白保留
    REQUIRE(result == "{\"a\":1,  \"b\":2}");
}

TEST_CASE("json: strip_duplicate_commas preserves strings") {
    auto result = strip_duplicate_commas(R"({"a":",,","b":1})");
    REQUIRE(result == R"({"a":",,","b":1})");
}

TEST_CASE("json: clean_text combined") {
    // 同时有缺失逗号和连续逗号
    auto result = clean_text(R"({"a":1 "b":2,,,"c":3})");
    // fix_missing_commas: {"a":1, "b":2,,,"c":3}
    // strip_duplicate_commas: {"a":1, "b":2,"c":3}
    REQUIRE(result == R"({"a":1, "b":2,"c":3})");
}

// ═══════════════════════════════════════════════════════════
// 解析测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: parse standard json") {
    auto doc = JsonDoc::parse(R"({"name":"test","id":42})");
    REQUIRE(doc.valid());
    REQUIRE(doc.root().valid());
}

TEST_CASE("json: parse with comments") {
    auto doc = JsonDoc::parse("// comment\n{\"a\":1}");
    REQUIRE(doc.valid());
}

TEST_CASE("json: parse with trailing commas") {
    auto doc = JsonDoc::parse(R"({"a":1,"b":2,})");
    REQUIRE(doc.valid());
}

TEST_CASE("json: parse with missing commas") {
    // clean=true（默认）时通过 clean_text 修复
    auto doc = JsonDoc::parse(R"({"a":1 "b":2})");
    REQUIRE(doc.valid());
}

TEST_CASE("json: parse with BOM in text") {
    std::string bom_text = "\xEF\xBB\xBF{\"a\":1}";
    auto doc = JsonDoc::parse(bom_text);
    REQUIRE(doc.valid());
}

TEST_CASE("json: parse invalid json throws") {
    REQUIRE_THROWS_AS(JsonDoc::parse("{invalid"), std::runtime_error);
}

TEST_CASE("json: parse clean=false skips cleaning") {
    // clean=false 且 JSON 有缺失逗号 → 解析失败
    REQUIRE_THROWS_AS(
        JsonDoc::parse(R"({"a":1 "b":2})", false),
        std::runtime_error);
}

TEST_CASE("json: parse empty object") {
    auto doc = JsonDoc::parse("{}");
    REQUIRE(doc.valid());
}

TEST_CASE("json: parse empty array") {
    auto doc = JsonDoc::parse("[]");
    REQUIRE(doc.valid());
}

// ═══════════════════════════════════════════════════════════
// 重复键测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: duplicate keys preserved in serialization") {
    auto doc = JsonDoc::parse(R"({"a":1,"a":2})");
    REQUIRE(doc.valid());
    auto output = doc.to_string(true);
    // 输出应包含两个 "a"
    size_t first = output.find("\"a\"");
    REQUIRE(first != std::string::npos);
    size_t second = output.find("\"a\"", first + 1);
    REQUIRE(second != std::string::npos);
}

TEST_CASE("json: duplicate keys roundtrip") {
    std::string input = R"({"a":1,"a":2,"b":3})";
    auto doc1 = JsonDoc::parse(input);
    auto text1 = doc1.to_string(true);

    auto doc2 = JsonDoc::parse(text1);
    auto text2 = doc2.to_string(true);

    REQUIRE(text1 == text2);
}

// ═══════════════════════════════════════════════════════════
// 序列化测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: to_string pretty") {
    auto doc = JsonDoc::parse(R"({"a":1,"b":2})");
    auto output = doc.to_string(false);
    // pretty 输出应包含换行
    REQUIRE(output.find('\n') != std::string::npos);
    // 应包含缩进空格
    REQUIRE(output.find("    ") != std::string::npos);
}

TEST_CASE("json: to_string compact") {
    auto doc = JsonDoc::parse(R"({"a":1,"b":2})");
    auto output = doc.to_string(true);
    // compact 不应包含换行
    REQUIRE(output.find('\n') == std::string::npos);
}

TEST_CASE("json: to_string null doc throws") {
    auto doc = JsonDoc::parse("{}");
    auto moved = std::move(doc);
    // doc 现在为空
    REQUIRE_THROWS_AS(doc.to_string(), std::runtime_error);
}

// ═══════════════════════════════════════════════════════════
// parse_file 测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: parse_file basic") {
    TempDir tmp;
    auto f = tmp.file("test.json");
    write_raw(f, R"({"name":"hello","count":42})");

    auto doc = JsonDoc::parse_file(f);
    REQUIRE(doc.valid());
    auto text = doc.to_string(true);
    REQUIRE(text.find("hello") != std::string::npos);
}

TEST_CASE("json: parse_file nonexistent throws") {
    REQUIRE_THROWS(JsonDoc::parse_file("/nonexistent/path.json"));
}

TEST_CASE("json: parse_file with BOM") {
    TempDir tmp;
    auto f = tmp.file("bom.json");
    write_raw(f, "\xEF\xBB\xBF{\"a\":1}");

    auto doc = JsonDoc::parse_file(f);
    REQUIRE(doc.valid());
}

TEST_CASE("json: parse_file with comments and trailing commas") {
    TempDir tmp;
    auto f = tmp.file("nonstandard.json");
    write_raw(f, "// header\n{\"a\":1,\"b\":2,}");

    auto doc = JsonDoc::parse_file(f);
    REQUIRE(doc.valid());
}

TEST_CASE("json: parse_file with missing commas") {
    TempDir tmp;
    auto f = tmp.file("missing.json");
    write_raw(f, R"({"a":1 "b":2})");

    auto doc = JsonDoc::parse_file(f);
    REQUIRE(doc.valid());
}

// ═══════════════════════════════════════════════════════════
// 移动语义测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: move constructor") {
    auto doc1 = JsonDoc::parse(R"({"a":1})");
    REQUIRE(doc1.valid());

    auto doc2 = std::move(doc1);
    REQUIRE(doc2.valid());
    REQUIRE_FALSE(doc1.valid());
}

TEST_CASE("json: move assignment") {
    auto doc1 = JsonDoc::parse(R"({"a":1})");
    auto doc2 = JsonDoc::parse(R"({"b":2})");

    doc2 = std::move(doc1);
    REQUIRE(doc2.valid());
    REQUIRE_FALSE(doc1.valid());
}
