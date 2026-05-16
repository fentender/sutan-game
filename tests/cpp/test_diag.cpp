#include <catch2/catch_test_macros.hpp>

#include <algorithm>
#include <atomic>
#include <string>
#include <thread>
#include <vector>

#include "diag.h"

using namespace sultan;

static DiagManager make_manager() { return DiagManager{}; }

TEST_CASE("diag: emit all levels to buffer") {
    auto mgr = make_manager();

    mgr.info("parse", "info msg");
    mgr.warn("parse", "warn msg");
    mgr.error("merge", "error msg");

    auto msgs = mgr.snapshot();
    REQUIRE(msgs.size() == 3);

    std::sort(msgs.begin(), msgs.end(),
              [](auto& a, auto& b) { return a.level < b.level; });

    REQUIRE(msgs[0].level == DiagLevel::Info);
    REQUIRE(msgs[0].category == "parse");
    REQUIRE(msgs[0].message == "info msg");

    REQUIRE(msgs[1].level == DiagLevel::Warn);
    REQUIRE(msgs[1].category == "parse");

    REQUIRE(msgs[2].level == DiagLevel::Error);
    REQUIRE(msgs[2].category == "merge");
}

TEST_CASE("diag: snapshot filters by category") {
    auto mgr = make_manager();

    mgr.warn("parse", "p1");
    mgr.warn("parse", "p2");
    mgr.error("merge", "m1");

    auto parse_msgs = mgr.snapshot({"parse"});
    REQUIRE(parse_msgs.size() == 2);
    REQUIRE(parse_msgs[0].message == "p1");
    REQUIRE(parse_msgs[1].message == "p2");

    auto remaining = mgr.snapshot();
    REQUIRE(remaining.size() == 1);
    REQUIRE(remaining[0].category == "merge");
}

TEST_CASE("diag: snapshot drains buffer") {
    auto mgr = make_manager();

    mgr.warn("test", "msg");
    auto first = mgr.snapshot();
    REQUIRE(first.size() == 1);

    auto second = mgr.snapshot();
    REQUIRE(second.empty());
}

TEST_CASE("diag: notify with callback bypasses buffer") {
    auto mgr = make_manager();

    DiagLevel cb_level{};
    std::string cb_cat, cb_msg;
    mgr.set_callback([&](DiagLevel l, const std::string& c,
                         const std::string& m) {
        cb_level = l;
        cb_cat = c;
        cb_msg = m;
    });

    mgr.error("merge", "critical", true);

    REQUIRE(cb_level == DiagLevel::Error);
    REQUIRE(cb_cat == "merge");
    REQUIRE(cb_msg == "critical");

    auto msgs = mgr.snapshot();
    REQUIRE(msgs.empty());
}

TEST_CASE("diag: notify without callback falls back to buffer") {
    auto mgr = make_manager();

    mgr.error("merge", "critical", true);

    auto msgs = mgr.snapshot();
    REQUIRE(msgs.size() == 1);
    REQUIRE(msgs[0].message == "critical");
}

TEST_CASE("diag: notify=false with callback goes to buffer") {
    auto mgr = make_manager();

    bool called = false;
    mgr.set_callback([&](DiagLevel, const std::string&, const std::string&) {
        called = true;
    });

    mgr.error("merge", "normal", false);

    REQUIRE_FALSE(called);
    auto msgs = mgr.snapshot();
    REQUIRE(msgs.size() == 1);
}

TEST_CASE("diag: clear callback then notify falls back") {
    auto mgr = make_manager();

    bool called = false;
    mgr.set_callback([&](DiagLevel, const std::string&, const std::string&) {
        called = true;
    });
    mgr.clear_callback();

    mgr.error("merge", "after clear", true);

    REQUIRE_FALSE(called);
    auto msgs = mgr.snapshot();
    REQUIRE(msgs.size() == 1);
}

TEST_CASE("diag: thread safety") {
    auto mgr = make_manager();

    const int n_threads = 8;
    const int msgs_per_thread = 100;

    std::vector<std::thread> threads;
    std::atomic<int> ready{0};

    for (int t = 0; t < n_threads; ++t) {
        threads.emplace_back([&mgr, &ready, t, n_threads, msgs_per_thread]() {
            ready.fetch_add(1);
            while (ready.load() < n_threads) {}
            for (int i = 0; i < msgs_per_thread; ++i) {
                mgr.warn("thread",
                          "t" + std::to_string(t) + "-" + std::to_string(i));
            }
        });
    }

    for (auto& th : threads) th.join();

    auto msgs = mgr.snapshot();
    REQUIRE(msgs.size() == n_threads * msgs_per_thread);
}
