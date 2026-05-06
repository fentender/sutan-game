# Json 模块

## 概述

C++ 层 JSON 解析与序列化模块，基于 **yyjson** 封装。将文本/文件解析为不可变 `JsonDoc` 对象（opaque handle），供后续 C++ 模块（State / Delta / Merger）使用。

**定位**：C++ 内部模块，当前不暴露 nanobind 绑定。Python 层继续使用 `_fast_json.c` + `parser.py` + `json.loads` 路径。nanobind 绑定留待后续阶段需要跨语言传递 `JsonDoc` 时添加。

**依赖**：资源加载模块（文件读取）、诊断模块（BOM 警告）、yyjson（JSON 解析库）。

## 类型体系

| 类型 | 说明 |
|------|------|
| `JsonDoc` | yyjson 不可变文档的 RAII 封装，移动语义，禁止拷贝 |
| `yyjson_val*` | yyjson 值节点指针，通过 `root()` / `raw_doc()` 访问 |

## 文件结构

```
csrc/json/
├── json_cleaner.h       # 文本清洗函数声明
├── json_cleaner.cpp     # fix_missing_commas / strip_duplicate_commas / clean_text
├── json_doc.h           # JsonDoc 类声明
└── json_doc.cpp         # 解析 / 序列化实现
tests/cpp/
└── test_json.cpp        # C++ 单元测试（34 case）
```

## 文本清洗

### 清洗流水线

yyjson 原生支持 `//` 注释和尾随逗号（通过 `YYJSON_READ_ALLOW_COMMENTS` / `YYJSON_READ_ALLOW_TRAILING_COMMAS` flag），C++ `clean_text` 只处理 yyjson 不覆盖的非标准语法：

```
输入文本 → fix_missing_commas → strip_duplicate_commas → 输出
```

对比 Python 版 `clean_json_text` 四步流程（strip_js_comments → strip_trailing_commas → fix_missing_commas → strip_duplicate_commas），C++ 版减少两次遍历。

### C++ API

```cpp
#include "json_cleaner.h"
using namespace sultan;

// 修复缺失逗号：{"a":1 "b":2} → {"a":1, "b":2}
std::string fixed = fix_missing_commas(text);

// 压缩连续逗号：{"a":1,,,"b":2} → {"a":1,"b":2}
std::string stripped = strip_duplicate_commas(text);

// 统一清洗入口
std::string cleaned = clean_text(text);
```

### 实现细节

**fix_missing_commas** — 状态机扫描，移植自 `_fast_json.c`：

- 逐字符处理，识别值结束位置（字符串闭合 `"`、数字末尾、`true`/`false`/`null`、`}`/`]`）
- 值结束后跳过空白，检查下一个非空白是否为 `"key":` 模式（`is_key_start`）
- 是则在空白前插入 `,`，空白本身保留

**strip_duplicate_commas** — 状态机扫描：

- 字符串内原样复制
- 字符串外遇到 `,` 时输出一个 `,`，跳过后续的多余逗号（保留中间空白）

## JsonDoc

### 生命周期

```cpp
auto doc = JsonDoc::parse(text);       // 工厂函数返回值
auto doc2 = std::move(doc);            // 移动语义，doc 变为无效
// doc2 析构时自动调用 yyjson_doc_free
```

RAII 管理 `yyjson_doc*` 裸指针。移动构造/赋值转移所有权，析构时释放。禁止拷贝。

### C++ API

```cpp
#include "json_doc.h"
using namespace sultan;

// ── 解析 ──

// 从文本解析（clean=true 默认执行 clean_text 预处理）
auto doc = JsonDoc::parse(R"({"name":"test","id":42})");
auto doc2 = JsonDoc::parse(text, false);  // 跳过清洗

// 从文件解析（通过 ResourceLoader，BOM 已剥离，含 BOM 诊断）
auto doc3 = JsonDoc::parse_file("config/cards.json");

// ── 序列化 ──

std::string pretty = doc.to_string();          // 4 空格缩进
std::string compact = doc.to_string(true);     // 紧凑输出

// ── 内部访问 ──

bool ok = doc.valid();               // doc 是否持有有效文档
yyjson_val* root = doc.root();       // 根节点
yyjson_doc* raw = doc.raw_doc();     // 底层 yyjson_doc 指针
```

### yyjson 解析 flag

```cpp
YYJSON_READ_ALLOW_TRAILING_COMMAS  // {"a":1,} 合法
YYJSON_READ_ALLOW_COMMENTS         // // 注释合法
YYJSON_READ_ALLOW_BOM              // UTF-8 BOM 容忍（防御性）
```

### parse_file 流程

```
1. resource_loader().read_text(path)      → 读取文件，BOM 由 ResourceLoader 内部静默剥离
2. parse(*content, clean)                  → clean_text + yyjson 解析
```

### 重复键

yyjson 默认保留重复键。解析 `{"a":1,"a":2}` 后内部存储两个 `"a"` 键值对，序列化时完整输出。

## 错误处理

- 解析失败 → `throw std::runtime_error("JSON parse error: <msg> (pos: <n>)")`
- 序列化空文档 → `throw std::runtime_error("Cannot serialize null JsonDoc")`
- 序列化内部失败 → `throw std::runtime_error("JSON serialization failed")`
- 文件不存在 → 由 ResourceLoader 抛出 `runtime_error`

## 与后续模块的关系

### State 模块（TODO 2.1）

```cpp
// state 模块内部
auto doc = JsonDoc::parse_file(path);
auto* root = doc.root();
// 从 root 构建 State 对象
```

### Delta 模块（TODO 2.2）

```cpp
auto doc_a = JsonDoc::parse_file(base_path);
auto doc_b = JsonDoc::parse_file(mod_path);
// 递归比较 doc_a.root() 和 doc_b.root()
```

### Python 层

当前 Python 不直接使用 JsonDoc。nanobind 绑定留待 State/Delta 模块需要从 Python 传递 opaque handle 时添加。

## 现有测试用例

### test_json.cpp（34 case）

**清洗测试（13 case）**

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

**解析测试（9 case）**

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

**重复键测试（2 case）**

| 测试 | 验证内容 |
|------|---------|
| `json: duplicate keys preserved in serialization` | 序列化保留重复键 |
| `json: duplicate keys roundtrip` | parse → to_string → parse → to_string 一致 |

**序列化测试（3 case）**

| 测试 | 验证内容 |
|------|---------|
| `json: to_string pretty` | 包含换行和缩进 |
| `json: to_string compact` | 不含换行 |
| `json: to_string null doc throws` | 空文档抛异常 |

**parse_file 测试（5 case）**

| 测试 | 验证内容 |
|------|---------|
| `json: parse_file basic` | 读取临时文件解析 |
| `json: parse_file nonexistent throws` | 不存在文件抛异常 |
| `json: parse_file with BOM` | 含 BOM 文件正确解析 |
| `json: parse_file with comments and trailing commas` | 非标准语法文件 |
| `json: parse_file with missing commas` | 缺失逗号文件 |

**移动语义测试（2 case）**

| 测试 | 验证内容 |
|------|---------|
| `json: move constructor` | 移动后源无效，目标有效 |
| `json: move assignment` | 移动赋值后源无效 |
