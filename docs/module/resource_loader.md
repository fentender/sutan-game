# 资源加载模块

## 概述

C++ 层文件 I/O 基础设施，供所有 C++ 模块使用（Json 模块、State/Delta 模块等）。提供文件读写、复制、删除、目录操作，以及基于 mtime 的自动缓存。

**定位**：C++ 内部模块，不暴露 nanobind 绑定。Python 层通过 Json 模块间接使用读取缓存能力；文件复制/目录操作等无缓存需求的操作，Python 层继续使用 pathlib/shutil。

**依赖**：无（C++ 层最底层模块）。

## 类型体系

| 类型 | 说明 |
|------|------|
| `TextPtr` | `std::shared_ptr<const std::string>`，缓存返回的共享常量引用 |
| `ResourceLoader` | 资源加载器：读写 / 复制 / 删除 / 目录 / 缓存管理 |
| `resource_loader()` | 全局单例（Meyer's singleton，线程安全） |

## 缓存机制

### read_text 缓存

- 缓存粒度：文件路径 → `TextPtr` + `file_time_type`
- **命中条件**：路径在缓存中 且 当前 mtime 与缓存 mtime 一致
- **自动失效**：每次 read_text 检查 mtime，外部修改自动刷新
- **写入更新**：write_text 成功后主动将新内容放入缓存，避免 Windows mtime 精度竞态

### 返回共享引用

```cpp
auto ptr1 = loader.read_text("cards.json");
auto ptr2 = loader.read_text("cards.json");
// ptr1.get() == ptr2.get()  — 同一个 string 对象
// *ptr1 是 const std::string& — 不可修改
```

同文件多次读取返回同一个 `shared_ptr`，零拷贝。`const` 保证调用方不能修改缓存内容。缓存 invalidate 后已持有的指针仍有效（引用计数）。

### 乐观读策略

```
1. 加锁查缓存 → 复制出 cached_content + cached_mtime → 释放锁
2. 无锁 stat 获取 current_mtime
3. mtime 相同 → 返回 cached_content（零拷贝）
4. mtime 变化 → 无锁读磁盘 + strip_bom → 加锁更新缓存
```

允许多线程并发读取不同文件时不互相阻塞。

## 文件结构

```
csrc/
├── resource_loader.h       # ResourceLoader / TextPtr 声明
└── resource_loader.cpp     # 实现
tests/
└── cpp/test_resource_loader.cpp  # C++ 单元测试（18 case）
```

## C++ API

```cpp
#include "resource_loader.h"
using namespace sultan;

auto& loader = resource_loader();

// ── 读取 ──

TextPtr text = loader.read_text("config/cards.json");
// *text 是 const std::string&，UTF-8，BOM 已剥离，带缓存

std::string raw = loader.read_bytes("config/cards.json");
// 原始字节，不缓存，不剥离 BOM

std::vector<TextPtr> batch = loader.batch_read_text(
    {"a.json", "b.json", "c.json"});
// 多线程并行读取，num_threads=0 自动检测核心数

// ── 写入 ──

loader.write_text("output/merged.json", json_text);
// 自动创建父目录，写入后更新缓存

// ── 复制 / 删除 ──

loader.copy_file("src.json", "dst.json");     // 单文件复制
loader.copy_tree("dir_src", "dir_dst");        // 递归复制
loader.remove_file("old.json");                // 删除文件（不存在则抛异常）
loader.remove_tree("output/");                 // 递归删除目录
loader.remove_empty_dir("empty_dir/");         // 空目录删除，非空返回 false

// ── 目录操作 ──

loader.mkdir("a/b/c/");                        // 递归创建，exist_ok
bool e = loader.exists("path");
bool f = loader.is_file("path");
bool d = loader.is_dir("path");

auto entries = loader.list_dir("config/");     // 直属子项
auto jsons = loader.rglob("config/", "*.json");  // 递归查找所有 .json 文件

// ── 缓存管理 ──

loader.invalidate("config/cards.json");        // 清除单个缓存
loader.clear_cache();                          // 清空全部缓存
size_t n = loader.cache_size();                // 当前缓存条目数

// ── 元信息 ──

double mtime = loader.get_mtime("config/cards.json");  // Unix epoch 秒
```

## 内部实现细节

### 路径处理

- `to_path()`：`std::filesystem::u8path(s)` — MSVC 下正确处理 UTF-8 中文路径
- `from_path()`：`p.u8string()` — 反向转换
- `normalize()`：`lexically_normal()` + `\` 替换为 `/` — 统一缓存 key

### BOM 处理

`read_text` 静默剥离 UTF-8 BOM（`EF BB BF`），不发诊断消息（保持模块零依赖）。Json 模块如需 BOM 诊断，可通过 `read_bytes` 检查原始前缀。

### rglob 模式匹配

基于 `std::filesystem::recursive_directory_iterator`，仅返回文件（`is_regular_file`）。支持的模式：

| 模式 | 匹配 |
|------|------|
| `*` | 所有文件 |
| `*.json` | 所有 `.json` 文件（大小写不敏感） |
| `filename` | 精确匹配文件名 |

### file_time_type → double 转换

`get_mtime()` 通过 `now()` 差值法将 `file_time_type`（MSVC 使用 Windows FILETIME epoch）转为 Unix epoch 秒：

```cpp
auto sctp = time_point_cast<system_clock::duration>(
    ftime - file_time_type::clock::now() + system_clock::now());
return duration<double>(sctp.time_since_epoch()).count();
```

内部缓存比较直接使用 `file_time_type`，不经过 double 转换，零精度损失。

## 线程安全

- `read_text` / `batch_read_text`：多线程安全（乐观读 + mutex 保护缓存更新）
- `write_text` / `copy_file` / `remove_*`：缓存操作线程安全；文件系统操作需调用方确保不并发写同一文件
- `exists` / `is_file` / `is_dir` / `list_dir` / `rglob`：无状态查询，天然安全
- `resource_loader()`：Meyer's singleton，C++11 保证线程安全初始化

## 错误处理

- 文件不存在 → `throw std::runtime_error("File not found: " + path)`
- 写入失败 → `throw std::runtime_error("Failed to write: " + path)`
- 不存在的文件调用 `remove_file` → 抛异常
- `remove_tree` 目标不存在 → 静默（与 `shutil.rmtree` 行为一致）

## 与后续模块的关系

### Json 模块（TODO 1.4）

```cpp
// json_module.cpp 内部
auto text = resource_loader().read_text(path);  // BOM 已剥离
// 执行额外清洗（fix_missing_commas 等）
// yyjson 解析
```

若需 BOM 诊断：

```cpp
auto raw = resource_loader().read_bytes(path);
if (raw.size() >= 3 && raw[0]=='\xEF' && raw[1]=='\xBB' && raw[2]=='\xBF') {
    diag_manager().warn("parse", filename + ": UTF-8 BOM detected");
}
auto text = resource_loader().read_text(path);  // BOM 已剥离
```

### Python 层

Python 不直接使用 ResourceLoader。文件读取通过 Json 模块间接走缓存；文件复制/目录操作继续使用 Python pathlib/shutil（这些操作无缓存需求，C++ 包装无性能收益）。

## 现有测试用例

### test_resource_loader.cpp（18 case）

| 测试 | 验证内容 |
|------|---------|
| `resource: read_text basic` | 读取 UTF-8 文件内容正确 |
| `resource: read_text strips BOM` | BOM 自动剥离 |
| `resource: read_bytes preserves BOM` | read_bytes 保留原始数据 |
| `resource: read_text nonexistent throws` | 不存在文件抛异常 |
| `resource: cache hit` | 读两次同文件返回同一 shared_ptr，cache_size()==1 |
| `resource: cache invalidates on mtime change` | 重写文件后 read_text 返回新内容 |
| `resource: write_text creates parent dirs` | 写入深层路径自动创建目录 |
| `resource: write_text updates cache` | 写入后 read_text 命中缓存返回写入内容 |
| `resource: copy_file` | 复制后目标内容一致 |
| `resource: copy_tree` | 递归复制目录结构 |
| `resource: remove_file` | 删除后 exists==false |
| `resource: remove_file nonexistent throws` | 不存在抛异常 |
| `resource: remove_tree` | 删除整个目录 |
| `resource: remove_empty_dir` | 空→true, 非空→false |
| `resource: mkdir recursive` | 深层目录创建 |
| `resource: list_dir` | 返回直属子项 |
| `resource: rglob pattern` | `*.json` 只返回 json 文件 |
| `resource: concurrent read_text` | 8 线程并发读同一文件无崩溃，全部返回正确内容 |
