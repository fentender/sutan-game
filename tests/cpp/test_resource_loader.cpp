#include <catch2/catch_test_macros.hpp>

#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <random>
#include <string>
#include <thread>
#include <vector>

#include "resource_loader.h"

using namespace sultan;
namespace fs = std::filesystem;

struct TempDir {
    fs::path path;

    TempDir() {
        std::random_device rd;
        path = fs::temp_directory_path() /
               ("sultan_test_" + std::to_string(rd()));
        fs::create_directories(path);
    }

    ~TempDir() {
        std::error_code ec;
        fs::remove_all(path, ec);
    }

    std::string str() const { return path.u8string(); }

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

static ResourceLoader make_loader() { return ResourceLoader{}; }

// ── 读取 ──

TEST_CASE("resource: read_text basic") {
    TempDir tmp;
    auto loader = make_loader();
    auto f = tmp.file("hello.txt");
    write_raw(f, "hello world");

    auto ptr = loader.read_text(f);
    REQUIRE(ptr != nullptr);
    REQUIRE(*ptr == "hello world");
}

TEST_CASE("resource: read_text strips BOM") {
    TempDir tmp;
    auto loader = make_loader();
    auto f = tmp.file("bom.txt");
    write_raw(f, "\xEF\xBB\xBFhello");

    auto ptr = loader.read_text(f);
    REQUIRE(*ptr == "hello");
}

TEST_CASE("resource: read_bytes preserves BOM") {
    TempDir tmp;
    auto loader = make_loader();
    auto f = tmp.file("bom.bin");
    std::string raw = "\xEF\xBB\xBF" "data";
    write_raw(f, raw);

    auto bytes = loader.read_bytes(f);
    REQUIRE(bytes == raw);
}

TEST_CASE("resource: read_text nonexistent throws") {
    auto loader = make_loader();
    REQUIRE_THROWS(loader.read_text("nonexistent_file_12345.txt"));
}

// ── 缓存 ──

TEST_CASE("resource: cache hit") {
    TempDir tmp;
    auto loader = make_loader();
    auto f = tmp.file("cached.txt");
    write_raw(f, "content");

    auto ptr1 = loader.read_text(f);
    auto ptr2 = loader.read_text(f);
    REQUIRE(loader.cache_size() == 1);
    REQUIRE(ptr1.get() == ptr2.get());
}

TEST_CASE("resource: cache invalidates on mtime change") {
    TempDir tmp;
    auto loader = make_loader();
    auto f = tmp.file("mtime.txt");
    write_raw(f, "old");

    auto ptr1 = loader.read_text(f);
    REQUIRE(*ptr1 == "old");

    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    auto fspath = fs::u8path(f);
    auto old_time = fs::last_write_time(fspath);
    write_raw(f, "new");
    fs::last_write_time(fspath,
                        old_time + std::chrono::seconds(2));

    auto ptr2 = loader.read_text(f);
    REQUIRE(*ptr2 == "new");
}

// ── 写入 ──

TEST_CASE("resource: write_text creates parent dirs") {
    TempDir tmp;
    auto loader = make_loader();
    auto f = tmp.file("a/b/c/deep.txt");

    loader.write_text(f, "deep content");
    REQUIRE(fs::exists(fs::u8path(f)));

    auto ptr = loader.read_text(f);
    REQUIRE(*ptr == "deep content");
}

TEST_CASE("resource: write_text updates cache") {
    TempDir tmp;
    auto loader = make_loader();
    auto f = tmp.file("wr.txt");

    loader.write_text(f, "written");
    REQUIRE(loader.cache_size() == 1);

    auto ptr = loader.read_text(f);
    REQUIRE(*ptr == "written");
}

// ── 复制 ──

TEST_CASE("resource: copy_file") {
    TempDir tmp;
    auto loader = make_loader();
    auto src = tmp.file("src.txt");
    auto dst = tmp.file("sub/dst.txt");
    write_raw(src, "copy me");

    loader.copy_file(src, dst);
    REQUIRE(fs::exists(fs::u8path(dst)));

    auto ptr = loader.read_text(dst);
    REQUIRE(*ptr == "copy me");
}

TEST_CASE("resource: copy_tree") {
    TempDir tmp;
    auto loader = make_loader();

    auto dir_src = tmp.file("tree_src");
    write_raw(tmp.file("tree_src/a.txt"), "a");
    write_raw(tmp.file("tree_src/sub/b.txt"), "b");

    auto dir_dst = tmp.file("tree_dst");
    loader.copy_tree(dir_src, dir_dst);

    REQUIRE(fs::exists(fs::u8path(tmp.file("tree_dst/a.txt"))));
    REQUIRE(fs::exists(fs::u8path(tmp.file("tree_dst/sub/b.txt"))));
}

// ── 删除 ──

TEST_CASE("resource: remove_file") {
    TempDir tmp;
    auto loader = make_loader();
    auto f = tmp.file("del.txt");
    write_raw(f, "delete me");

    loader.remove_file(f);
    REQUIRE_FALSE(loader.exists(f));
}

TEST_CASE("resource: remove_file nonexistent throws") {
    auto loader = make_loader();
    REQUIRE_THROWS(loader.remove_file("nonexistent_del_12345.txt"));
}

TEST_CASE("resource: remove_tree") {
    TempDir tmp;
    auto loader = make_loader();

    auto dir = tmp.file("rmtree");
    write_raw(tmp.file("rmtree/a.txt"), "a");
    write_raw(tmp.file("rmtree/sub/b.txt"), "b");

    loader.remove_tree(dir);
    REQUIRE_FALSE(fs::exists(fs::u8path(dir)));
}

TEST_CASE("resource: remove_empty_dir") {
    TempDir tmp;
    auto loader = make_loader();

    auto empty_dir = tmp.file("empty");
    fs::create_directories(fs::u8path(empty_dir));
    REQUIRE(loader.remove_empty_dir(empty_dir));
    REQUIRE_FALSE(fs::exists(fs::u8path(empty_dir)));

    auto nonempty = tmp.file("nonempty");
    write_raw(tmp.file("nonempty/file.txt"), "x");
    REQUIRE_FALSE(loader.remove_empty_dir(nonempty));
    REQUIRE(fs::exists(fs::u8path(nonempty)));
}

// ── 目录 ──

TEST_CASE("resource: mkdir recursive") {
    TempDir tmp;
    auto loader = make_loader();
    auto deep = tmp.file("x/y/z");

    loader.mkdir(deep);
    REQUIRE(loader.is_dir(deep));
}

TEST_CASE("resource: list_dir") {
    TempDir tmp;
    auto loader = make_loader();

    write_raw(tmp.file("list/a.txt"), "a");
    write_raw(tmp.file("list/b.txt"), "b");
    fs::create_directories(fs::u8path(tmp.file("list/sub")));

    auto items = loader.list_dir(tmp.file("list"));
    REQUIRE(items.size() == 3);
}

TEST_CASE("resource: rglob pattern") {
    TempDir tmp;
    auto loader = make_loader();

    write_raw(tmp.file("glob/a.json"), "{}");
    write_raw(tmp.file("glob/b.txt"), "text");
    write_raw(tmp.file("glob/sub/c.json"), "[]");
    write_raw(tmp.file("glob/sub/d.txt"), "text");

    auto jsons = loader.rglob(tmp.file("glob"), "*.json");
    REQUIRE(jsons.size() == 2);

    auto all = loader.rglob(tmp.file("glob"), "*");
    REQUIRE(all.size() == 4);
}

// ── 缓存管理 ──

TEST_CASE("resource: invalidate and clear_cache") {
    TempDir tmp;
    auto loader = make_loader();
    auto f1 = tmp.file("c1.txt");
    auto f2 = tmp.file("c2.txt");
    write_raw(f1, "1");
    write_raw(f2, "2");

    loader.read_text(f1);
    loader.read_text(f2);
    REQUIRE(loader.cache_size() == 2);

    loader.invalidate(f1);
    REQUIRE(loader.cache_size() == 1);

    loader.clear_cache();
    REQUIRE(loader.cache_size() == 0);
}

// ── 并发 ──

TEST_CASE("resource: concurrent read_text") {
    TempDir tmp;
    auto loader = make_loader();
    auto f = tmp.file("concurrent.txt");
    write_raw(f, "shared content");

    const int n_threads = 8;
    const int reads_per_thread = 50;

    std::vector<std::thread> threads;
    std::atomic<int> ready{0};
    std::atomic<int> ok_count{0};

    for (int t = 0; t < n_threads; ++t) {
        threads.emplace_back(
            [&loader, &f, &ready, &ok_count, n_threads, reads_per_thread]() {
                ready.fetch_add(1);
                while (ready.load() < n_threads) {}
                for (int i = 0; i < reads_per_thread; ++i) {
                    auto ptr = loader.read_text(f);
                    if (ptr && *ptr == "shared content") {
                        ok_count.fetch_add(1);
                    }
                }
            });
    }
    for (auto& th : threads) th.join();

    REQUIRE(ok_count.load() == n_threads * reads_per_thread);
}
