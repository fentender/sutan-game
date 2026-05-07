#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>

#include <string>

#include "json_doc.h"
#include "json_val.h"
#include "mut_doc.h"
#include "mut_val.h"

using namespace sultan;

// ═══════════════════════════════════════════════════════════
// MutDoc 生命周期
// ═══════════════════════════════════════════════════════════

TEST_CASE("mutval: MutDoc default construct") {
    MutDoc d;
    auto root = d.root();
    REQUIRE_FALSE(root.valid());
    REQUIRE(root.raw_doc() != nullptr);
}

TEST_CASE("mutval: MutDoc from immutable doc") {
    auto doc = JsonDoc::parse(R"({"a":1,"b":"hello"})");
    auto d = MutDoc::from(doc);
    auto root = d.root();
    REQUIRE(root.valid());
    REQUIRE(root.is_obj());
}

TEST_CASE("mutval: MutDoc move constructor") {
    MutDoc d1;
    auto d2 = std::move(d1);
    REQUIRE(d2.root().raw_doc() != nullptr);
}

TEST_CASE("mutval: MutDoc freeze") {
    auto doc = JsonDoc::parse(R"({"x":42})");
    auto d = MutDoc::from(doc);
    auto result = d.freeze();
    REQUIRE(result.valid());
    auto text = result.to_string(true);
    REQUIRE(text.find("42") != std::string::npos);
}

TEST_CASE("mutval: MutDoc freeze null throws") {
    MutDoc d;
    auto d2 = std::move(d);
    REQUIRE_THROWS_AS(d.freeze(), std::runtime_error);
}

// ═══════════════════════════════════════════════════════════
// MutVal 类型检查
// ═══════════════════════════════════════════════════════════

TEST_CASE("mutval: type checks") {
    auto doc = JsonDoc::parse(R"({"n":null,"b":true,"i":42,"r":3.14,"s":"hi","o":{},"a":[]})");
    auto d = MutDoc::from(doc);
    auto root = d.root();

    REQUIRE(root.obj_get("n").is_null());
    REQUIRE(root.obj_get("b").is_bool());
    REQUIRE(root.obj_get("i").is_int());
    REQUIRE(root.obj_get("r").is_real());
    REQUIRE(root.obj_get("s").is_str());
    REQUIRE(root.obj_get("o").is_obj());
    REQUIRE(root.obj_get("a").is_arr());
}

TEST_CASE("mutval: type() returns JsonType") {
    auto doc = JsonDoc::parse(R"({"i":42})");
    auto d = MutDoc::from(doc);
    REQUIRE(d.root().obj_get("i").type() == JsonType::Int);
}

TEST_CASE("mutval: invalid val type is Null") {
    MutVal v;
    REQUIRE(v.type() == JsonType::Null);
    REQUIRE(v.is_null());
}

// ═══════════════════════════════════════════════════════════
// MutVal 读取
// ═══════════════════════════════════════════════════════════

TEST_CASE("mutval: get_bool") {
    auto doc = JsonDoc::parse(R"({"v":true})");
    auto d = MutDoc::from(doc);
    REQUIRE(d.root().obj_get("v").get_bool() == true);
}

TEST_CASE("mutval: get_int") {
    auto doc = JsonDoc::parse(R"({"v":42})");
    auto d = MutDoc::from(doc);
    REQUIRE(d.root().obj_get("v").get_int() == 42);
}

TEST_CASE("mutval: get_real") {
    auto doc = JsonDoc::parse(R"({"v":3.14})");
    auto d = MutDoc::from(doc);
    REQUIRE(d.root().obj_get("v").get_real() == Catch::Approx(3.14));
}

TEST_CASE("mutval: get_str") {
    auto doc = JsonDoc::parse(R"({"v":"hello"})");
    auto d = MutDoc::from(doc);
    REQUIRE(std::string(d.root().obj_get("v").get_str()) == "hello");
}

TEST_CASE("mutval: get_len for string") {
    auto doc = JsonDoc::parse(R"({"v":"abc"})");
    auto d = MutDoc::from(doc);
    REQUIRE(d.root().obj_get("v").get_len() == 3);
}

// ═══════════════════════════════════════════════════════════
// MutVal 修改标量
// ═══════════════════════════════════════════════════════════

TEST_CASE("mutval: set_int") {
    auto doc = JsonDoc::parse(R"({"v":1})");
    auto d = MutDoc::from(doc);
    d.root().obj_get("v").set_int(99);
    auto result = d.freeze();
    REQUIRE(result.to_string(true).find("99") != std::string::npos);
}

TEST_CASE("mutval: set_bool") {
    auto doc = JsonDoc::parse(R"({"v":true})");
    auto d = MutDoc::from(doc);
    d.root().obj_get("v").set_bool(false);
    auto result = d.freeze();
    REQUIRE(result.to_string(true).find("false") != std::string::npos);
}

TEST_CASE("mutval: set_real") {
    auto doc = JsonDoc::parse(R"({"v":1.0})");
    auto d = MutDoc::from(doc);
    d.root().obj_get("v").set_real(2.5);
    auto result = d.freeze();
    REQUIRE(result.to_string(true).find("2.5") != std::string::npos);
}

TEST_CASE("mutval: set_str") {
    auto doc = JsonDoc::parse(R"({"v":"old"})");
    auto d = MutDoc::from(doc);
    d.root().obj_get("v").set_str("new");
    auto result = d.freeze();
    auto text = result.to_string(true);
    REQUIRE(text.find("new") != std::string::npos);
    REQUIRE(text.find("old") == std::string::npos);
}

// ═══════════════════════════════════════════════════════════
// MutVal 对象操作
// ═══════════════════════════════════════════════════════════

TEST_CASE("mutval: obj_get existing key") {
    auto doc = JsonDoc::parse(R"({"a":1,"b":2})");
    auto d = MutDoc::from(doc);
    auto v = d.root().obj_get("b");
    REQUIRE(v.valid());
    REQUIRE(v.get_int() == 2);
}

TEST_CASE("mutval: obj_get missing key") {
    auto doc = JsonDoc::parse(R"({"a":1})");
    auto d = MutDoc::from(doc);
    REQUIRE_FALSE(d.root().obj_get("missing").valid());
}

TEST_CASE("mutval: obj_add") {
    auto doc = JsonDoc::parse(R"({"a":1})");
    auto d = MutDoc::from(doc);
    auto root = d.root();
    root.obj_add("b", root.new_int(2));
    auto result = d.freeze();
    auto text = result.to_string(true);
    REQUIRE(text.find("\"b\"") != std::string::npos);
    REQUIRE(text.find("2") != std::string::npos);
}

TEST_CASE("mutval: obj_put replaces existing") {
    auto doc = JsonDoc::parse(R"({"a":1})");
    auto d = MutDoc::from(doc);
    auto root = d.root();
    root.obj_put("a", root.new_int(99));
    auto result = d.freeze();
    auto text = result.to_string(true);
    REQUIRE(text.find("99") != std::string::npos);
}

TEST_CASE("mutval: obj_remove") {
    auto doc = JsonDoc::parse(R"({"a":1,"b":2})");
    auto d = MutDoc::from(doc);
    REQUIRE(d.root().obj_remove("a"));
    auto result = d.freeze();
    auto text = result.to_string(true);
    REQUIRE(text.find("\"a\"") == std::string::npos);
    REQUIRE(text.find("\"b\"") != std::string::npos);
}

TEST_CASE("mutval: obj_size") {
    auto doc = JsonDoc::parse(R"({"a":1,"b":2,"c":3})");
    auto d = MutDoc::from(doc);
    REQUIRE(d.root().obj_size() == 3);
}

// ═══════════════════════════════════════════════════════════
// MutVal 数组操作
// ═══════════════════════════════════════════════════════════

TEST_CASE("mutval: arr_get") {
    auto doc = JsonDoc::parse(R"([10,20,30])");
    auto d = MutDoc::from(doc);
    REQUIRE(d.root().arr_get(1).get_int() == 20);
}

TEST_CASE("mutval: arr_append") {
    auto doc = JsonDoc::parse(R"([1,2])");
    auto d = MutDoc::from(doc);
    auto root = d.root();
    root.arr_append(root.new_int(3));
    auto result = d.freeze();
    REQUIRE(result.root().arr_size() == 3);
}

TEST_CASE("mutval: arr_prepend") {
    auto doc = JsonDoc::parse(R"([2,3])");
    auto d = MutDoc::from(doc);
    auto root = d.root();
    root.arr_prepend(root.new_int(1));
    auto result = d.freeze();
    REQUIRE(result.root().arr_size() == 3);
    auto arr_it = result.root().arr_iter();
    JsonVal first;
    arr_it.next(first);
    REQUIRE(first.get_int() == 1);
}

TEST_CASE("mutval: arr_remove") {
    auto doc = JsonDoc::parse(R"([1,2,3])");
    auto d = MutDoc::from(doc);
    REQUIRE(d.root().arr_remove(1));
    auto result = d.freeze();
    REQUIRE(result.root().arr_size() == 2);
}

TEST_CASE("mutval: arr_size") {
    auto doc = JsonDoc::parse(R"([1,2,3,4])");
    auto d = MutDoc::from(doc);
    REQUIRE(d.root().arr_size() == 4);
}

// ═══════════════════════════════════════════════════════════
// MutVal 值创建
// ═══════════════════════════════════════════════════════════

TEST_CASE("mutval: new_* via root context") {
    MutDoc d;
    auto ctx = d.root();
    REQUIRE_FALSE(ctx.valid());

    auto obj = ctx.new_obj();
    REQUIRE(obj.valid());
    REQUIRE(obj.is_obj());

    obj.obj_add("name", ctx.new_str("test"));
    obj.obj_add("count", ctx.new_int(42));
    obj.obj_add("flag", ctx.new_bool(true));
    obj.obj_add("rate", ctx.new_real(1.5));
    obj.obj_add("empty", ctx.new_null());

    auto arr = ctx.new_arr();
    arr.arr_append(ctx.new_int(1));
    arr.arr_append(ctx.new_int(2));
    obj.obj_add("list", arr);

    d.set_root(obj);
    auto result = d.freeze();
    REQUIRE(result.valid());

    auto root = result.root();
    REQUIRE(root.obj_size() == 6);
    REQUIRE(std::string(root.obj_get("name").get_str()) == "test");
    REQUIRE(root.obj_get("count").get_int() == 42);
    REQUIRE(root.obj_get("flag").get_bool() == true);
    double rate = root.obj_get("rate").get_real();
    REQUIRE(rate == Catch::Approx(1.5));
    REQUIRE(root.obj_get("empty").is_null());
    REQUIRE(root.obj_get("list").arr_size() == 2);
}

// ═══════════════════════════════════════════════════════════
// MutVal 对象迭代
// ═══════════════════════════════════════════════════════════

TEST_CASE("mutval: obj_iter") {
    auto doc = JsonDoc::parse(R"({"a":1,"b":2,"c":3})");
    auto d = MutDoc::from(doc);
    auto root = d.root();

    int count = 0;
    auto it = root.obj_iter();
    MutVal::ObjEntry e;
    while (it.next(e)) {
        REQUIRE(e.key_str != nullptr);
        REQUIRE(e.key_len > 0);
        REQUIRE(e.val.valid());
        ++count;
    }
    REQUIRE(count == 3);
}

TEST_CASE("mutval: obj_iter modify values") {
    auto doc = JsonDoc::parse(R"({"a":1,"b":2})");
    auto d = MutDoc::from(doc);
    auto root = d.root();

    auto it = root.obj_iter();
    MutVal::ObjEntry e;
    while (it.next(e)) {
        if (e.val.is_int()) {
            e.val.set_int(e.val.get_int() * 10);
        }
    }

    auto result = d.freeze();
    REQUIRE(result.root().obj_get("a").get_int() == 10);
    REQUIRE(result.root().obj_get("b").get_int() == 20);
}

TEST_CASE("mutval: obj_iter modify keys") {
    auto doc = JsonDoc::parse(R"({"old_key":1})");
    auto d = MutDoc::from(doc);
    auto root = d.root();

    auto it = root.obj_iter();
    MutVal::ObjEntry e;
    while (it.next(e)) {
        if (std::string(e.key_str, e.key_len) == "old_key") {
            e.key.set_str("new_key");
        }
    }

    auto result = d.freeze();
    auto text = result.to_string(true);
    REQUIRE(text.find("new_key") != std::string::npos);
    REQUIRE(text.find("old_key") == std::string::npos);
}

// ═══════════════════════════════════════════════════════════
// MutVal 数组迭代
// ═══════════════════════════════════════════════════════════

TEST_CASE("mutval: arr_iter") {
    auto doc = JsonDoc::parse(R"([10,20,30])");
    auto d = MutDoc::from(doc);

    int sum = 0;
    auto it = d.root().arr_iter();
    MutVal elem;
    while (it.next(elem)) {
        sum += static_cast<int>(elem.get_int());
    }
    REQUIRE(sum == 60);
}

TEST_CASE("mutval: arr_iter modify") {
    auto doc = JsonDoc::parse(R"([1,2,3])");
    auto d = MutDoc::from(doc);

    auto it = d.root().arr_iter();
    MutVal elem;
    while (it.next(elem)) {
        elem.set_int(elem.get_int() + 100);
    }

    auto result = d.freeze();
    auto arr = result.root();
    auto arr_it = arr.arr_iter();
    JsonVal v;
    arr_it.next(v); REQUIRE(v.get_int() == 101);
    arr_it.next(v); REQUIRE(v.get_int() == 102);
    arr_it.next(v); REQUIRE(v.get_int() == 103);
}

// ═══════════════════════════════════════════════════════════
// MutDoc roundtrip
// ═══════════════════════════════════════════════════════════

TEST_CASE("mutval: roundtrip from -> modify -> freeze") {
    auto doc = JsonDoc::parse(R"({"name":"old","id":1,"tags":["a","b"]})");
    auto d = MutDoc::from(doc);
    auto root = d.root();

    root.obj_get("name").set_str("new");
    root.obj_get("id").set_int(99);
    root.obj_add("extra", root.new_bool(true));

    auto result = d.freeze();
    auto r = result.root();
    REQUIRE(std::string(r.obj_get("name").get_str()) == "new");
    REQUIRE(r.obj_get("id").get_int() == 99);
    REQUIRE(r.obj_get("extra").get_bool() == true);
    REQUIRE(r.obj_get("tags").arr_size() == 2);
}

TEST_CASE("mutval: roundtrip build from scratch") {
    MutDoc d;
    auto ctx = d.root();

    auto obj = ctx.new_obj();
    obj.obj_add("key", ctx.new_str("value"));
    obj.obj_add("num", ctx.new_int(123));

    auto arr = ctx.new_arr();
    arr.arr_append(ctx.new_int(1));
    arr.arr_append(ctx.new_int(2));
    obj.obj_add("list", arr);

    d.set_root(obj);
    auto result = d.freeze();

    auto text = result.to_string(true);
    REQUIRE(text.find("\"key\"") != std::string::npos);
    REQUIRE(text.find("\"value\"") != std::string::npos);
    REQUIRE(text.find("123") != std::string::npos);
}
