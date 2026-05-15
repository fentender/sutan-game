# C++ 单元测试

## 概述

基于 **Catch2 v3** 的 C++ 单元测试体系。测试目标 `sultan_tests` 链接 `sultan_core_lib` 静态库，直接测试 C++ 业务逻辑，不经过 nanobind 绑定层。

## 依赖

| 组件 | 版本 | 引入方式 |
|------|------|---------|
| Catch2 | v3.8.0 | git submodule（`extern/Catch2`） |
| sultan_core_lib | — | 项目内静态库（`csrc/`） |

## 文件结构

```
tests/cpp/
├── CMakeLists.txt              # 测试构建配置
├── test_diag.cpp               # 诊断模块测试（8 case）
├── test_resource_loader.cpp    # 资源加载模块测试（18 case）
├── test_json.cpp               # Json 模块测试（34 case）
├── test_json_ops.cpp          # 字段操作模块测试（17 case）
├── test_state.cpp              # State 模块测试（41 case）
├── test_mut_val.cpp            # MutVal/MutDoc 测试（36 case）
└── test_delta.cpp              # Delta 模块测试（71 case）
```

后续模块添加测试时，在此目录新建 `test_xxx.cpp` 并注册到 `CMakeLists.txt`。

## 构建与运行

```bash
# 1. 获取 nanobind cmake 路径（顶层 CMake 需要）
NB_DIR=$(python -c "import nanobind; print(nanobind.cmake_dir())")

# 2. 配置（BUILD_TESTS=ON 开启测试编译）
cmake -B build_test -DBUILD_TESTS=ON -Dnanobind_DIR="$NB_DIR"

# 3. 构建测试目标
cmake --build build_test --target sultan_tests --config Release

# 4a. 通过 CTest 运行（自动发现所有 TEST_CASE）
ctest --test-dir build_test -C Release --output-on-failure

# 4b. 或直接运行可执行文件
./build_test/tests/cpp/Release/sultan_tests.exe
```

`BUILD_TESTS` 默认 OFF，正常 `pip install` 不编译测试，不影响构建速度。

## CMake 配置说明

### 顶层 `CMakeLists.txt`

```cmake
option(BUILD_TESTS "Build C++ unit tests" OFF)
if(BUILD_TESTS)
    enable_testing()
    add_subdirectory(extern/Catch2)
    add_subdirectory(tests/cpp)
endif()
```

### `tests/cpp/CMakeLists.txt`

```cmake
include(CTest)
include(Catch)

add_executable(sultan_tests
    test_diag.cpp
    # 后续添加：test_resource.cpp, test_json.cpp, ...
)
target_link_libraries(sultan_tests PRIVATE
    sultan_core_lib
    Catch2::Catch2WithMain
)
set_target_properties(sultan_tests PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED ON
)

catch_discover_tests(sultan_tests)
```

关键点：
- **Catch2::Catch2WithMain**：自动提供 `main()` 函数，测试文件无需手写入口
- **catch_discover_tests()**：构建后自动运行可执行文件发现所有 `TEST_CASE`，注册到 CTest
- **sultan_core_lib**：链接 C++ 业务逻辑静态库，测试直接调用 C++ API

## 编写测试

### 基本模式

```cpp
#include <catch2/catch_test_macros.hpp>
#include "diag.h"  // 被测模块头文件

using namespace sultan;

TEST_CASE("module: test description") {
    // 构造被测对象（使用独立实例，避免全局状态干扰）
    auto mgr = DiagManager{};

    // 操作
    mgr.warn("parse", "test message");

    // 断言
    auto msgs = mgr.snapshot();
    REQUIRE(msgs.size() == 1);
    REQUIRE(msgs[0].level == DiagLevel::Warn);
    REQUIRE(msgs[0].message == "test message");
}
```

### 命名规范

`TEST_CASE` 名称格式：`"模块名: 行为描述"`，使用英文（避免 CTest 编码问题）。

```cpp
TEST_CASE("diag: emit all levels to buffer") { ... }
TEST_CASE("diag: snapshot filters by category") { ... }
TEST_CASE("json: parse with trailing commas") { ... }  // 后续模块示例
```

### 常用断言

| 宏 | 说明 |
|----|------|
| `REQUIRE(expr)` | 断言为真，失败则终止当前 test case |
| `CHECK(expr)` | 断言为真，失败继续执行（收集所有失败） |
| `REQUIRE_FALSE(expr)` | 断言为假 |
| `REQUIRE_THROWS(expr)` | 断言抛出异常 |
| `REQUIRE_NOTHROW(expr)` | 断言不抛异常 |

### 并发测试

使用 `std::thread` + `std::atomic` barrier 确保并发启动：

```cpp
TEST_CASE("module: thread safety") {
    auto obj = SomeClass{};
    const int n_threads = 8;
    const int ops_per_thread = 100;

    std::vector<std::thread> threads;
    std::atomic<int> ready{0};

    for (int t = 0; t < n_threads; ++t) {
        threads.emplace_back([&obj, &ready, t, n_threads, ops_per_thread]() {
            ready.fetch_add(1);
            while (ready.load() < n_threads) {}  // barrier
            for (int i = 0; i < ops_per_thread; ++i) {
                obj.some_operation(...);
            }
        });
    }
    for (auto& th : threads) th.join();

    // 验证结果一致性
    REQUIRE(obj.count() == n_threads * ops_per_thread);
}
```

注意：MSVC lambda 中不能隐式捕获 `constexpr` 变量，使用 `const` 并显式捕获。

## 现有测试用例

### test_diag.cpp（8 case，26 assertions）

| 测试 | 验证内容 |
|------|---------|
| `diag: emit all levels to buffer` | Info/Warn/Error 三级别消息正确进入 buffer |
| `diag: snapshot filters by category` | 按类别拉取，其他类别保留 |
| `diag: snapshot drains buffer` | snapshot 后 buffer 清空 |
| `diag: notify with callback bypasses buffer` | notify=true + 有回调 → 回调触发，不进 buffer |
| `diag: notify without callback falls back to buffer` | notify=true + 无回调 → 回退进 buffer |
| `diag: notify=false with callback goes to buffer` | notify=false + 有回调 → 进 buffer，回调不触发 |
| `diag: clear callback then notify falls back` | 清除回调后 notify=true 回退进 buffer |
| `diag: thread safety` | 8 线程各 100 条并发 emit → 总数 800 |

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
| `resource: concurrent read_text` | 8 线程并发读同一文件无崩溃 |

测试使用 `TempDir` RAII 结构管理临时目录，构造时创建随机名目录，析构时自动清理。

### test_json.cpp（34 case）

**清洗（13 case）**

| 测试 | 验证内容 |
|------|---------|
| `json: fix_missing_commas after string value` | 字符串值后插入逗号 |
| `json: fix_missing_commas after number` | 数字后插入逗号 |
| `json: fix_missing_commas after bool` | true/false 后插入逗号 |
| `json: fix_missing_commas after null` | null 后插入逗号 |
| `json: fix_missing_commas after close brace` | `}` 后插入逗号 |
| `json: fix_missing_commas after close bracket` | `]` 后插入逗号 |
| `json: fix_missing_commas after negative number` | 负数后插入逗号 |
| `json: fix_missing_commas no false positive` | 正确 JSON 不变 |
| `json: fix_missing_commas preserves strings` | 字符串内内容不修改 |
| `json: strip_duplicate_commas basic` | 连续逗号压缩 |
| `json: strip_duplicate_commas with whitespace` | 含空白的连续逗号 |
| `json: strip_duplicate_commas preserves strings` | 字符串内逗号不动 |
| `json: clean_text combined` | 缺失逗号+连续逗号同时修复 |

**解析（9 case）**

| 测试 | 验证内容 |
|------|---------|
| `json: parse standard json` | 标准 JSON 解析成功 |
| `json: parse with comments` | `//` 注释正常解析 |
| `json: parse with trailing commas` | 尾随逗号正常解析 |
| `json: parse with missing commas` | clean=true 修复后解析 |
| `json: parse with BOM in text` | BOM 文本容忍 |
| `json: parse invalid json throws` | 无效 JSON 抛异常 |
| `json: parse clean=false skips cleaning` | clean=false 不清洗 |
| `json: parse empty object` | `{}` 解析 |
| `json: parse empty array` | `[]` 解析 |

**重复键（2 case）**

| 测试 | 验证内容 |
|------|---------|
| `json: duplicate keys preserved in serialization` | 序列化保留重复键 |
| `json: duplicate keys roundtrip` | parse → to_string → parse → to_string 一致 |

**序列化（3 case）**

| 测试 | 验证内容 |
|------|---------|
| `json: to_string pretty` | 包含换行和缩进 |
| `json: to_string compact` | 不含换行 |
| `json: to_string null doc throws` | 空文档抛异常 |

**parse_file（5 case）**

| 测试 | 验证内容 |
|------|---------|
| `json: parse_file basic` | 读取临时文件解析 |
| `json: parse_file nonexistent throws` | 不存在文件抛异常 |
| `json: parse_file with BOM` | 含 BOM 文件正确解析 |
| `json: parse_file with comments and trailing commas` | 非标准语法文件 |
| `json: parse_file with missing commas` | 缺失逗号文件 |

**移动语义（2 case）**

| 测试 | 验证内容 |
|------|---------|
| `json: move constructor` | 移动后源无效，目标有效 |
| `json: move assignment` | 移动赋值后源无效 |

### test_json_ops.cpp（17 case）

**提取（7 case）**

| 测试 | 验证内容 |
|------|---------|
| `json_ops: extract_string_values basic` | 从对象提取字符串字段值 |
| `json_ops: extract_string_values nested` | 深层嵌套对象中提取 |
| `json_ops: extract_string_values in array` | 数组内对象中提取 |
| `json_ops: extract_string_values no match` | 字段不存在返回空 |
| `json_ops: extract_int_values basic` | 提取整数字段值 |
| `json_ops: extract_int_values mixed types` | 同名字段不同类型，仅收集整数 |
| `json_ops: extract from empty doc` | 空对象/空数组 |

**替换（10 case）**

| 测试 | 验证内容 |
|------|---------|
| `json_ops: replace_field_ints basic` | 按字段名替换整数值 |
| `json_ops: replace_field_ints only named field` | 同值不同字段名不受影响 |
| `json_ops: replace_field_ints nested` | 深层嵌套中按名替换 |
| `json_ops: replace_field_ints no match` | 不匹配不变 |
| `json_ops: replace_field_strs basic` | 按字段名替换字符串值 |
| `json_ops: replace_field_strs exact match` | 仅精确匹配 |
| `json_ops: replace_root_keys basic` | 根级键替换 |
| `json_ops: replace_root_keys not recursive` | 嵌套层级同名键不受影响 |
| `json_ops: replace preserves original` | 替换返回新文档，原文档不变 |
| `json_ops: replace on empty doc` | 空文档替换不报错 |

### test_state.cpp（41 case）

**ChangeKind（8 case）**

| 测试 | 验证内容 |
|------|---------|
| `state: change_kind base values` | 各枚举值正确 |
| `state: change_kind bitwise or` | 位或运算 |
| `state: change_kind base_kind extraction` | 提取基础类型 |
| `state: change_kind flags extraction` | 提取修饰标志 |
| `state: change_kind is_* predicates` | 各判断函数 |
| `state: change_kind multi_mod flag` | MultiMod 标志 |
| `state: change_kind override flag` | Override 标志 |
| `state: change_kind combined flags` | 组合标志 |

**ScalarValue（4 case）**

| 测试 | 验证内容 |
|------|---------|
| `state: serialize_scalar null` | null 序列化 |
| `state: serialize_scalar types` | bool/int/string 序列化 |
| `state: serialize_scalar double` | 浮点数序列化 |
| `state: scalar_equal same types` | 相等比较 |

**from_doc 构建（9 case）**

| 测试 | 验证内容 |
|------|---------|
| `state: from_doc simple object` | 简单对象构建 |
| `state: from_doc nested object` | 嵌套对象 |
| `state: from_doc array` | 数组构建（indices/order） |
| `state: from_doc nested array` | 嵌套数组 |
| `state: from_doc scalar root` | 标量根节点 |
| `state: from_doc empty object` | 空对象 |
| `state: from_doc duplicate keys` | 重复键 → DupList |
| `state: from_doc all types` | null/bool/int/float/string/object/array |
| `state: from_doc all kinds origin` | 所有 kind 初始为 Origin |

**to_doc 转换（5 case）**

| 测试 | 验证内容 |
|------|---------|
| `state: to_doc roundtrip simple` | 简单 roundtrip |
| `state: to_doc roundtrip nested` | 嵌套 roundtrip |
| `state: to_doc skips deleted` | 跳过 DELETED 字段 |
| `state: to_doc duplist expansion` | DupList 展开为重复键 |
| `state: to_doc array order` | 按 order 顺序输出 |

**clone（3 case）**

| 测试 | 验证内容 |
|------|---------|
| `state: clone deep independence` | 修改 clone 不影响原树 |
| `state: clone preserves structure` | to_string 一致 |
| `state: clone preserves metadata` | kind/version/old_value 保留 |

**format 格式化（8 case）**

| 测试 | 验证内容 |
|------|---------|
| `state: format all origin` | 全 ORIGIN → left==right |
| `state: format added field` | ADDED → 右侧显示/左侧填充 |
| `state: format deleted field` | DELETED → 左侧显示/右侧填充 |
| `state: format changed field` | CHANGED → 旧值左/新值右 |
| `state: format highlight version filter` | version 不匹配按 ORIGIN 输出 |
| `state: format alignment` | 行数预对齐 |
| `state: format nested structure` | 嵌套递归格式化 |
| `state: format duplist` | DupList 展开为重复键行 |

**便利工厂 + move 语义（4 case）**

| 测试 | 验证内容 |
|------|---------|
| `state: from_text basic` | 文本构建 |
| `state: from_file basic` | 文件构建 |
| `state: move constructor` | 移动后源无效 |
| `state: move assignment` | 移动赋值后源无效 |

### test_mut_val.cpp（36 case）

**MutDoc 生命周期（5 case）**

| 测试 | 验证内容 |
|------|---------|
| `mutval: MutDoc default construct` | 空文档，root 无效但 doc_ 有效 |
| `mutval: MutDoc from immutable doc` | 从不可变文档创建可变副本 |
| `mutval: MutDoc move constructor` | 移动后目标有效 |
| `mutval: MutDoc freeze` | 转为不可变 JsonDoc |
| `mutval: MutDoc freeze null throws` | 空 MutDoc freeze 抛异常 |

**类型检查（3 case）**

| 测试 | 验证内容 |
|------|---------|
| `mutval: type checks` | 7 种类型全覆盖 |
| `mutval: type() returns JsonType` | type() 返回正确枚举 |
| `mutval: invalid val type is Null` | 无效 MutVal 类型为 Null |

**读取（5 case）**

| 测试 | 验证内容 |
|------|---------|
| `mutval: get_bool` | 布尔值读取 |
| `mutval: get_int` | 整数读取 |
| `mutval: get_real` | 浮点数读取 |
| `mutval: get_str` | 字符串读取 |
| `mutval: get_len for string` | 字符串长度 |

**修改标量（4 case）**

| 测试 | 验证内容 |
|------|---------|
| `mutval: set_int` | 修改整数值 |
| `mutval: set_bool` | 修改布尔值 |
| `mutval: set_real` | 修改浮点数 |
| `mutval: set_str` | 修改字符串值 |

**对象操作（5 case）**

| 测试 | 验证内容 |
|------|---------|
| `mutval: obj_get existing key` | 按键查找 |
| `mutval: obj_get missing key` | 键不存在返回无效 |
| `mutval: obj_add` | 添加键值对 |
| `mutval: obj_put replaces existing` | 已存在键替换值 |
| `mutval: obj_remove` | 删除键值对 |
| `mutval: obj_size` | 对象大小 |

**数组操作（5 case）**

| 测试 | 验证内容 |
|------|---------|
| `mutval: arr_get` | 按索引访问 |
| `mutval: arr_append` | 尾部追加 |
| `mutval: arr_prepend` | 头部插入 |
| `mutval: arr_remove` | 按索引删除 |
| `mutval: arr_size` | 数组大小 |

**值创建（1 case）**

| 测试 | 验证内容 |
|------|---------|
| `mutval: new_* via root context` | 通过 root() 上下文创建全部值类型 |

**迭代器（5 case）**

| 测试 | 验证内容 |
|------|---------|
| `mutval: obj_iter` | 对象迭代基本功能 |
| `mutval: obj_iter modify values` | 迭代中修改值 |
| `mutval: obj_iter modify keys` | 迭代中修改键 |
| `mutval: arr_iter` | 数组迭代求和 |
| `mutval: arr_iter modify` | 迭代中修改元素 |

**Roundtrip（2 case）**

| 测试 | 验证内容 |
|------|---------|
| `mutval: roundtrip from -> modify -> freeze` | 完整修改流程 |
| `mutval: roundtrip build from scratch` | 从头构建 JSON 文档 |

## 添加新模块测试

1. 在 `tests/cpp/` 创建 `test_xxx.cpp`
2. 在 `tests/cpp/CMakeLists.txt` 的 `add_executable` 中添加源文件：
   ```cmake
   add_executable(sultan_tests
       test_diag.cpp
       test_xxx.cpp    # 新增
   )
   ```
3. 如需链接额外库（如 yyjson），在 `target_link_libraries` 中添加
4. 重新 cmake 配置 + 构建即可自动发现新测试

### test_delta.cpp（71 case）

**相似度（9 case）**

| 测试 | 验证内容 |
|------|---------|
| `delta: levenshtein empty strings` | 空字符串距离 0 |
| `delta: levenshtein one empty` | 单空距离等于另一方长度 |
| `delta: levenshtein identical` | 相同字符串距离 0 |
| `delta: levenshtein classic` | kitten→sitting = 3 |
| `delta: levenshtein single char` | 单字符比较 |
| `delta: string_ratio identical` | 相同 → 1.0 |
| `delta: string_ratio both empty` | 双空 → 1.0 |
| `delta: string_ratio completely different` | 完全不同 → 接近 0 |
| `delta: string_ratio partial` | 部分相同 → 0.4~0.6 |

**删除规则（8 case）**

| 测试 | 验证内容 |
|------|---------|
| `delta: rules array element always denied` | 数组元素禁删 |
| `delta: rules condition context allowed` | condition 上下文允许 |
| `delta: rules action context allowed` | action 上下文允许 |
| `delta: rules result context allowed` | result 上下文允许 |
| `delta: rules result_text field allowed` | result_text 字段允许 |
| `delta: rules result_title field allowed` | result_title 字段允许 |
| `delta: rules default denied` | 默认禁止 |
| `delta: rules empty path denied` | 空路径禁止 |

**Delta 节点（12 case）**

| 测试 | 验证内容 |
|------|---------|
| `delta: make_delta_element basic` | 工厂函数基础创建 |
| `delta: make_delta_element with old_value` | 带旧值和 version |
| `delta: delta_array wrap with value` | wrap 有值 |
| `delta: delta_array wrap nullptr` | wrap 空值 |
| `delta: clone element` | 元素深拷贝 |
| `delta: clone dict deep independence` | 字典深拷贝独立性 |
| `delta: clone array` | 数组深拷贝 |
| `delta: as_element throws on dict` | 类型转换异常 |
| `delta: as_dict throws on element` | 类型转换异常 |
| `delta: as_array throws on element` | 类型转换异常 |

**数组匹配（6 case）**

| 测试 | 验证内容 |
|------|---------|
| `delta: match empty arrays` | 空数组 |
| `delta: match identical scalar arrays` | 相同标量数组全配对 |
| `delta: match by guid key` | GUID 精确匹配 |
| `delta: match by guid key reorder` | 乱序 GUID |
| `delta: match with added elements` | 新增元素检测 |
| `delta: match with deleted elements` | 删除元素检测 |

**compute_delta（9 case）**

| 测试 | 验证内容 |
|------|---------|
| `delta: compute identical docs returns nullptr` | 无差异 → nullptr |
| `delta: compute scalar field changed` | 标量字段修改 |
| `delta: compute field added` | 字段新增 |
| `delta: compute field deleted` | 字段删除 |
| `delta: compute nested dict change` | 嵌套 dict 修改 |
| `delta: compute array element added` | 数组元素新增 |
| `delta: compute smart mode blocks deletion` | SMART 模式禁删 |
| `delta: compute smart mode allows condition deletion` | SMART 模式 condition 允删 |
| `delta: compute added complex value has value_node` | 复杂值 value_node |

**apply_delta（11 case）**

| 测试 | 验证内容 |
|------|---------|
| `delta: direct state insert replaces entry` | insert_or_assign 验证 |
| `delta: apply_field_delta basic` | 字段级应用基础 |
| `delta: apply added scalar field` | ADDED 应用 |
| `delta: apply changed scalar field` | CHANGED 应用（old_value 保留） |
| `delta: apply deleted field` | DELETED 应用 |
| `delta: apply multi_mod marking` | MULTI_MOD 标志 |
| `delta: apply override marking` | OVERRIDE 标志 |
| `delta: apply nested dict delta` | 嵌套 dict 应用 |
| `delta: apply added complex value with value_node` | 复杂值应用 |
| `delta: e2e compute then apply scalar changes` | 标量端到端 |
| `delta: e2e compute then apply nested changes` | 嵌套端到端 |

**序列化（4 case）**

| 测试 | 验证内容 |
|------|---------|
| `delta: serialize element roundtrip` | DeltaElement 往返 |
| `delta: serialize dict roundtrip` | DeltaDict 往返 |
| `delta: serialize array roundtrip` | DeltaArray 往返 |
| `delta: serialize nested dict roundtrip` | 嵌套 DeltaDict 往返 |

**remap（12 case）**

| 测试 | 验证内容 |
|------|---------|
| `delta: remap deleted field not in current drops` | DELETED 字段不在 current → 丢弃 |
| `delta: remap deleted field in current updates old_value` | DELETED 字段在 current → 更新 old_value |
| `delta: remap added field not in current keeps` | ADDED 字段不在 current → 保留 |
| `delta: remap added field same in current drops` | ADDED 字段已存在且值相同 → 丢弃 |
| `delta: remap added field different in current recomputes` | ADDED 字段值不同 → 变 CHANGED |
| `delta: remap changed field not in current converts to added` | CHANGED 字段不在 current → 变 ADDED |
| `delta: remap changed field same in current drops` | CHANGED 目标值与 current 相同 → 丢弃 |
| `delta: remap changed field different in current recomputes` | CHANGED 值不同 → 重算 old_value |
| `delta: remap nested dict recursion` | 嵌套 dict 递归重映射 |
| `delta: remap array reindex` | 数组元素位置变化后索引重映射 |
| `delta: remap added complex value` | ADDED 复杂值（obj/arr）保留 |
| `delta: remap e2e compute remap apply` | 端到端：计算→重映射→应用→验证 |

## CI 集成

`.github/workflows/build.yml` 中 C++ 测试步骤：

```yaml
- name: C++ unit tests
  run: |
    $nb_dir = python -c "import nanobind; print(nanobind.cmake_dir())"
    cmake -B build_test -DBUILD_TESTS=ON -Dnanobind_DIR="$nb_dir"
    cmake --build build_test --target sultan_tests --config Release
    ctest --test-dir build_test -C Release --output-on-failure
```
