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
// 清洗测试 — 缺失逗号（对象）
// 期望值对照 jsonrepair CLI 输出
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean missing comma after string value") {
    auto result = clean_text(R"({"a":"x" "b":"y"})");
    REQUIRE(result == R"({"a":"x", "b":"y"})");
}

TEST_CASE("json: clean missing comma after number") {
    auto result = clean_text(R"({"a":1 "b":2})");
    REQUIRE(result == R"({"a":1, "b":2})");
}

TEST_CASE("json: clean missing comma after bool") {
    auto result = clean_text(R"({"a":true "b":false})");
    REQUIRE(result == R"({"a":true, "b":false})");
}

TEST_CASE("json: clean missing comma after null") {
    auto result = clean_text(R"({"a":null "b":1})");
    REQUIRE(result == R"({"a":null, "b":1})");
}

TEST_CASE("json: clean missing comma after close brace") {
    auto result = clean_text(R"({"a":{} "b":1})");
    REQUIRE(result == R"({"a":{}, "b":1})");
}

TEST_CASE("json: clean missing comma after close bracket") {
    auto result = clean_text(R"({"a":[] "b":1})");
    REQUIRE(result == R"({"a":[], "b":1})");
}

TEST_CASE("json: clean missing comma after negative number") {
    auto result = clean_text(R"({"a":-1 "b":2})");
    REQUIRE(result == R"({"a":-1, "b":2})");
}

TEST_CASE("json: clean no false positive") {
    std::string input = R"({"a":1,"b":"hello","c":true})";
    auto result = clean_text(input);
    REQUIRE(result == input);
}

TEST_CASE("json: clean preserves strings") {
    auto result = clean_text(R"({"a":"hello world" "b":1})");
    REQUIRE(result == R"({"a":"hello world", "b":1})");
}

// ═══════════════════════════════════════════════════════════
// 清洗测试 — 重复逗号
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean duplicate commas basic") {
    // jsonrepair: {"a":1,,,"b":2} → {"a":1,"b":2}
    auto result = clean_text(R"({"a":1,,,"b":2})");
    REQUIRE(result == R"({"a":1,"b":2})");
}

TEST_CASE("json: clean duplicate commas with whitespace") {
    // jsonrepair: {"a":1, , "b":2} → {"a":1 , "b":2}
    auto result = clean_text("{\"a\":1, , \"b\":2}");
    REQUIRE(result == "{\"a\":1 , \"b\":2}");
}

TEST_CASE("json: clean duplicate commas preserves strings") {
    auto result = clean_text(R"({"a":",,","b":1})");
    REQUIRE(result == R"({"a":",,","b":1})");
}

TEST_CASE("json: clean combined missing and duplicate commas") {
    // jsonrepair: {"a":1 "b":2,,,"c":3} → {"a":1, "b":2,"c":3}
    auto result = clean_text(R"({"a":1 "b":2,,,"c":3})");
    REQUIRE(result == R"({"a":1, "b":2,"c":3})");
}

// ═══════════════════════════════════════════════════════════
// 清洗测试 — 数组缺逗号
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean array missing comma strings") {
    auto result = clean_text(R"(["a" "b" "c"])");
    REQUIRE(result == R"(["a", "b", "c"])");
}

TEST_CASE("json: clean array missing comma numbers") {
    auto result = clean_text("[1 2 3]");
    REQUIRE(result == "[1, 2, 3]");
}

TEST_CASE("json: clean array missing comma booleans") {
    auto result = clean_text("[true false null]");
    REQUIRE(result == "[true, false, null]");
}

TEST_CASE("json: clean array missing comma objects") {
    auto result = clean_text(R"([{"a":1} {"b":2}])");
    REQUIRE(result == R"([{"a":1}, {"b":2}])");
}

TEST_CASE("json: clean array missing comma arrays") {
    auto result = clean_text("[[1] [2]]");
    REQUIRE(result == "[[1], [2]]");
}

TEST_CASE("json: clean array missing comma mixed") {
    auto result = clean_text(R"([1 "a" true null])");
    REQUIRE(result == R"([1, "a", true, null])");
}

TEST_CASE("json: clean array missing comma negative") {
    auto result = clean_text("[1 -2 3]");
    REQUIRE(result == "[1, -2, 3]");
}

// ═══════════════════════════════════════════════════════════
// 中文引号
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean chinese double quotes pair") {
    auto result = clean_text("{" "\xE2\x80\x9C" "a" "\xE2\x80\x9D" ":1}");
    REQUIRE(result == R"({"a":1})");
}

TEST_CASE("json: clean chinese double quotes left-ascii") {
    auto result = clean_text("{" "\xE2\x80\x9C" "a\"" ":1}");
    REQUIRE(result == R"({"a":1})");
}

TEST_CASE("json: clean chinese double quotes in value") {
    auto result = clean_text("{" "\xE2\x80\x9C" "a" "\xE2\x80\x9D" ":" "\xE2\x80\x9C" "hello" "\xE2\x80\x9D" "}");
    REQUIRE(result == R"({"a":"hello"})");
}

TEST_CASE("json: clean chinese quotes content preserved in ascii string") {
    // ASCII " 开头 → 只有 ASCII " 能关闭 → 中文引号是内容
    auto result = clean_text("{\"a\":\"" "\xE2\x80\x9C" "hi" "\xE2\x80\x9D" "\"}");
    // jsonrepair: {"a":"<U+201C>hi<U+201D>"}
    auto expected = "{\"a\":\"" "\xE2\x80\x9C" "hi" "\xE2\x80\x9D" "\"}";
    REQUIRE(result == expected);
}

// ═══════════════════════════════════════════════════════════
// 单引号
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean single quotes") {
    auto result = clean_text("{'a':1,'b':'hello'}");
    REQUIRE(result == R"({"a":1,"b":"hello"})");
}

TEST_CASE("json: clean backtick quotes") {
    auto result = clean_text("{`a`:1}");
    REQUIRE(result == R"({"a":1})");
}

TEST_CASE("json: clean fancy single quotes") {
    auto result = clean_text("{" "\xE2\x80\x98" "a" "\xE2\x80\x99" ":1}");
    REQUIRE(result == R"({"a":1})");
}

// ═══════════════════════════════════════════════════════════
// 键名无引号
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean unquoted keys") {
    auto result = clean_text("{name:\"test\",count:42}");
    REQUIRE(result == R"({"name":"test","count":42})");
}

TEST_CASE("json: clean unquoted key with underscore") {
    auto result = clean_text("{my_key:1}");
    REQUIRE(result == R"({"my_key":1})");
}

TEST_CASE("json: clean unquoted key with digits") {
    auto result = clean_text("{key123:1}");
    REQUIRE(result == R"({"key123":1})");
}

// ═══════════════════════════════════════════════════════════
// 缺少冒号
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean missing colon") {
    // jsonrepair: {"a" 1} → {"a": 1}
    auto result = clean_text(R"({"a" 1})");
    REQUIRE(result == R"({"a": 1})");
}

// ═══════════════════════════════════════════════════════════
// 注释
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean line comment stripped") {
    // jsonrepair: // comment\n{"a":1} → \n{"a":1}
    auto result = clean_text("// comment\n{\"a\":1}");
    REQUIRE(result == "\n{\"a\":1}");
}

TEST_CASE("json: clean block comment stripped") {
    auto result = clean_text("/* comment */{\"a\":1}");
    REQUIRE(result == "{\"a\":1}");
}

TEST_CASE("json: clean inline comment stripped") {
    // jsonrepair: {"a":1 /* inline */ ,"b":2} → {"a":1  ,"b":2}
    auto result = clean_text("{\"a\":1 /* inline */ ,\"b\":2}");
    REQUIRE(result == "{\"a\":1  ,\"b\":2}");
}

TEST_CASE("json: clean comment between values") {
    // jsonrepair: {"a":1,// comment\n"b":2} → {"a":1,\n"b":2}
    auto result = clean_text("{\"a\":1,// comment\n\"b\":2}");
    REQUIRE(result == "{\"a\":1,\n\"b\":2}");
}

// ═══════════════════════════════════════════════════════════
// 尾随逗号 / 开头多余逗号
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean trailing comma object") {
    auto result = clean_text(R"({"a":1,"b":2,})");
    REQUIRE(result == R"({"a":1,"b":2})");
}

TEST_CASE("json: clean trailing comma array") {
    auto result = clean_text("[1,2,3,]");
    REQUIRE(result == "[1,2,3]");
}

TEST_CASE("json: clean leading comma object") {
    auto result = clean_text(R"({,"a":1})");
    REQUIRE(result == R"({"a":1})");
}

TEST_CASE("json: clean leading comma array") {
    auto result = clean_text("[,1,2]");
    REQUIRE(result == "[1,2]");
}

// ═══════════════════════════════════════════════════════════
// 缺失 / 多余括号
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean missing closing brace") {
    auto result = clean_text(R"({"a":1)");
    REQUIRE(result == R"({"a":1})");
}

TEST_CASE("json: clean missing closing bracket") {
    auto result = clean_text("[1,2,3");
    REQUIRE(result == "[1,2,3]");
}

TEST_CASE("json: clean extra closing brace") {
    auto result = clean_text(R"({"a":1}})");
    REQUIRE(result == R"({"a":1})");
}

TEST_CASE("json: clean extra closing bracket") {
    auto result = clean_text("[1,2]]");
    REQUIRE(result == "[1,2]");
}

// ═══════════════════════════════════════════════════════════
// 缺少 Object 值
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean missing object value comma") {
    auto result = clean_text(R"({"a":,"b":2})");
    REQUIRE(result == R"({"a":null,"b":2})");
}

TEST_CASE("json: clean missing object value end") {
    auto result = clean_text(R"({"a":})");
    REQUIRE(result == R"({"a":null})");
}

// ═══════════════════════════════════════════════════════════
// 数字截断
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean truncated decimal") {
    auto result = clean_text("[2.]");
    REQUIRE(result == "[2.0]");
}

TEST_CASE("json: clean truncated exponent") {
    auto result = clean_text("[1e]");
    REQUIRE(result == "[1e0]");
}

TEST_CASE("json: clean truncated exponent with sign") {
    auto result = clean_text("[1e+]");
    REQUIRE(result == "[1e+0]");
}

// ═══════════════════════════════════════════════════════════
// Python 关键字
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean Python True") {
    auto result = clean_text("[True]");
    REQUIRE(result == "[true]");
}

TEST_CASE("json: clean Python False") {
    auto result = clean_text("[False]");
    REQUIRE(result == "[false]");
}

TEST_CASE("json: clean Python None") {
    auto result = clean_text("[None]");
    REQUIRE(result == "[null]");
}

// ═══════════════════════════════════════════════════════════
// 省略号
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean ellipsis in array") {
    auto result = clean_text("[1,2,3,...]");
    REQUIRE(result == "[1,2,3]");
}

TEST_CASE("json: clean ellipsis in object") {
    auto result = clean_text(R"({"a":1,...})");
    REQUIRE(result == R"({"a":1})");
}

// ═══════════════════════════════════════════════════════════
// 转义处理
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean valid escapes preserved") {
    auto result = clean_text(R"({"a":"hello\nworld"})");
    REQUIRE(result == R"({"a":"hello\nworld"})");
}

TEST_CASE("json: clean invalid escape stripped") {
    auto result = clean_text(R"({"a":"hello\xworld"})");
    REQUIRE(result == R"({"a":"helloxworld"})");
}

// ═══════════════════════════════════════════════════════════
// 特殊空白
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean fullwidth space") {
    std::string input = "{" "\xE3\x80\x80" "\"a\":1}";
    auto result = clean_text(input);
    REQUIRE(result == "{ \"a\":1}");
}

TEST_CASE("json: clean non-breaking space") {
    std::string input = "{" "\xC2\xA0" "\"a\":1}";
    auto result = clean_text(input);
    REQUIRE(result == "{ \"a\":1}");
}

// ═══════════════════════════════════════════════════════════
// 换行自动关闭字符串
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean unclosed string at newline") {
    // jsonrepair: {"a":"hello\n} → {"a":"hello"\n}
    auto result = clean_text("{\"a\":\"hello\n}");
    auto doc = JsonDoc::parse(result, false);
    REQUIRE(doc.valid());
}

// ═══════════════════════════════════════════════════════════
// 集成测试
// ═══════════════════════════════════════════════════════════

TEST_CASE("json: clean multiple issues combined") {
    std::string input =
        "// header comment\n"
        "{" "\xE2\x80\x9C" "a" "\xE2\x80\x9D" ":1 "
        "\xE2\x80\x9C" "b" "\xE2\x80\x9D" ":2,}";
    auto result = clean_text(input);
    auto doc = JsonDoc::parse(result, false);
    REQUIRE(doc.valid());
}

TEST_CASE("json: clean complex nested") {
    auto result = clean_text(R"({"items":[{"id":1 "name":"a"} {"id":2 "name":"b"}] "count":2})");
    REQUIRE(result == R"({"items":[{"id":1, "name":"a"}, {"id":2, "name":"b"}], "count":2})");
}

TEST_CASE("json: clean empty input") {
    auto result = clean_text("");
    REQUIRE(result == "");
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
    auto doc = JsonDoc::parse(R"({"a":1 "b":2})");
    REQUIRE(doc.valid());
}

TEST_CASE("json: parse with BOM in text") {
    std::string bom_text = "\xEF\xBB\xBF{\"a\":1}";
    auto doc = JsonDoc::parse(bom_text);
    REQUIRE(doc.valid());
}

TEST_CASE("json: parse invalid json throws") {
    // {invalid} has key "invalid" but no colon → throwColonExpected
    REQUIRE_THROWS_AS(JsonDoc::parse("{invalid}"), std::runtime_error);
}

TEST_CASE("json: parse clean=false skips cleaning") {
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
    REQUIRE(output.find('\n') != std::string::npos);
    REQUIRE(output.find("    ") != std::string::npos);
}

TEST_CASE("json: to_string compact") {
    auto doc = JsonDoc::parse(R"({"a":1,"b":2})");
    auto output = doc.to_string(true);
    REQUIRE(output.find('\n') == std::string::npos);
}

TEST_CASE("json: to_string null doc throws") {
    auto doc = JsonDoc::parse("{}");
    auto moved = std::move(doc);
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
