# JSON 清洗模块（json_cleaner）

## 概述

C++ 层 JSON 文本修复模块。将非标准/损坏的 JSON 文本修复为合法 JSON，基于 **jsonrepair v3.14.0**（TypeScript）严格翻译为 C++。

**定位**：`JsonDoc::parse(text, clean=true)` 的预处理阶段。Mod JSON 频繁出现格式问题（中文引号、缺失逗号、注释、尾随逗号等），此模块在 yyjson 解析前完成修复。

**依赖**：无外部依赖。纯字符串处理，不依赖 yyjson 或其他模块。

## 文件结构

```
csrc/json/
├── json_cleaner.h       # 公共 API：仅暴露 clean_text
└── json_cleaner.cpp     # JsonRepairer 类实现（~810 行）
tests/cpp/
└── test_json.cpp        # 清洗相关测试（82 case，含解析/序列化集成测试）
```

## C++ API

```cpp
#include "json_cleaner.h"
using namespace sultan;

std::string cleaned = clean_text(R"({"a":1 "b":2})");
// → {"a":1, "b":2}
```

唯一公共接口。空输入返回空字符串，无效 JSON 抛 `std::runtime_error`。

## 架构：递归下降修复器

严格翻译 jsonrepair 的递归下降解析器。**单遍扫描**，边解析边修复，输出修复后的 JSON 文本。

```
输入文本 → JsonRepairer::repair() → 修复后文本
```

取代旧版三函数流水线（`fix_missing_commas` → `strip_duplicate_commas` → 输出）。

### 核心类 `JsonRepairer`

```
JsonRepairer
├── repair()                          # 入口：解析顶层值 + 清理尾部
├── parseValue()                      # 分发：object / array / string / number / keywords / unquoted
├── parseObject()                     # 解析对象：处理键值对循环
├── parseArray()                      # 解析数组：处理元素循环
├── parseString()                     # 解析字符串：引号族匹配 + 两阶段策略
├── parseNumber()                     # 解析数字：截断修复
├── parseKeywords()                   # true/false/null/True/False/None
├── parseUnquotedString(isKey)        # 无引号字符串 → 加引号
├── parseWhitespaceAndSkipComments()  # 空白保留 + 注释剥离
├── skipEllipsis()                    # 省略号 ... 剥离
└── 辅助：insertBeforeLastWhitespace / stripLastOccurrence / prevNonWhitespaceIndex
```

## 修复能力（22 项）

### 结构修复

| 问题 | 示例 | 修复结果 |
|------|------|---------|
| 缺失逗号（对象） | `{"a":1 "b":2}` | `{"a":1, "b":2}` |
| 缺失逗号（数组） | `[1 2 3]` | `[1, 2, 3]` |
| 重复逗号 | `{"a":1,,,"b":2}` | `{"a":1,"b":2}` |
| 尾随逗号 | `{"a":1,}` | `{"a":1}` |
| 前导逗号 | `{,"a":1}` | `{"a":1}` |
| 缺失冒号 | `{"a" 1}` | `{"a": 1}` |
| 缺失闭合括号 | `{"a":1` | `{"a":1}` |
| 多余闭合括号 | `{"a":1}}` | `{"a":1}` |
| 缺失对象值 | `{"a":,"b":2}` | `{"a":null,"b":2}` |
| 省略号 | `[1,2,3,...]` | `[1,2,3]` |

### 引号修复

| 问题 | 示例 | 修复结果 |
|------|------|---------|
| 中文双引号 `""“”` | `{"a":1}` → `{"a":1}` | ASCII 双引号 |
| 中文单引号 `'‘’` | `{'a':1}` → `{"a":1}` | ASCII 双引号 |
| 单引号 | `{'a':'hello'}` | `{"a":"hello"}` |
| 反引号 | `` {`a`:1} `` | `{"a":1}` |
| 无引号键名 | `{name:"test"}` | `{"name":"test"}` |

### 值修复

| 问题 | 示例 | 修复结果 |
|------|------|---------|
| Python `True/False/None` | `[True]` | `[true]` |
| 截断小数 | `[2.]` | `[2.0]` |
| 截断指数 | `[1e]` | `[1e0]` |
| 无效转义 | `{"a":"hello\xworld"}` | `{"a":"helloxworld"}` |

### 空白与注释

| 问题 | 示例 | 修复结果 |
|------|------|---------|
| 行注释 | `// comment\n{"a":1}` | `\n{"a":1}` |
| 块注释 | `/* ... */{"a":1}` | `{"a":1}` |
| 全角空格 / NBSP | `{　"a":1}` | `{ "a":1}` |

## 关键实现细节

### 引号族匹配

严格对照 jsonrepair 的 `isEndQuote` 策略：

```
开头引号          → 关闭条件
ASCII "           → 只有 ASCII " 能关闭
ASCII '           → 只有 ASCII ' 能关闭
花式双引号 “/”  → 任何 double-quote-like 能关闭
花式单引号 ‘/’  → 任何 single-quote-like 能关闭
```

**意义**：ASCII `"` 开头的字符串内，中文引号 `“”` 是普通内容，不会被误判为结束引号。

C++ 实现通过 `EndQuoteMode` 枚举 + `matchEndQuote()` 分发：

```cpp
enum class EndQuoteMode {
    AsciiDouble,      // 只有 ASCII " 能关闭
    AsciiSingle,      // 只有 ASCII ' 能关闭
    DoubleQuoteLike,  // 任何花式双引号能关闭
    SingleQuoteLike   // 任何花式单引号能关闭
};
```

### UTF-8 字节级匹配

jsonrepair（TypeScript）使用 `charCodeAt()` 操作 UTF-16 码元。C++ 版改为 UTF-8 字节序列匹配：

| 字符 | UTF-8 字节 | 匹配函数 |
|------|-----------|---------|
| `“` | `E2 80 9C` | `matchDoubleQuoteLike` |
| `”` | `E2 80 9D` | `matchDoubleQuoteLike` |
| `＂` | `EF BC 82` | `matchDoubleQuoteLike` |
| `‘` | `E2 80 98` | `matchSingleQuoteLike` |
| `’` | `E2 80 99` | `matchSingleQuoteLike` |
| `´` | `C2 B4` | `matchSingleQuoteLike` |
| `　` | `E3 80 80` | `matchSpecialWhitespace` |
| ` ` | `C2 A0` | `matchSpecialWhitespace` |

### 两阶段字符串解析

对照 jsonrepair `parseString(stopAtDelimiter, stopAtIndex)`：

1. **乐观阶段**（`stopAtDelimiter=false`）：找到匹配关闭引号后，检查后续字符是否为分隔符/空白。如果不是（说明引号可能是内容），回退重试。
2. **保守阶段**（`stopAtDelimiter=true`）：遇到分隔符即视为字符串结束（缺失关闭引号）。

### 重复逗号处理

jsonrepair 正式版（regular）不处理重复逗号。但 CLI 使用 streaming 版本，streaming 版通过状态机处理。本实现扩展正式版，在 `parseObject` 和 `parseArray` 中增加重复逗号检测：

```
parseObject 循环中：
  解析键失败 + 下一字符是 ',' → stripLastOccurrence(',')，continue

parseArray 循环中：
  解析值失败 + 下一字符是 ',' → stripLastOccurrence(',')，continue
```

### 字符串操作

严格对照 jsonrepair 的 `stringUtils.js`：

- **`insertBeforeLastWhitespace(text, ch)`**：在文本末尾空白之前插入字符。用于插入缺失的 `,`、`:`、`"`、`}`、`]`。
- **`stripLastOccurrence(text, ch)`**：从整个字符串末尾回搜，删除最后一个匹配字符。用于去除尾随逗号。使用 `rfind` 全文搜索，对照 TS 的 `lastIndexOf`。

## 与 jsonrepair 的差异

| 差异 | 原因 |
|------|------|
| UTF-8 字节级处理（非 JS charCode/UTF-16） | C++ 无 UTF-16 内置支持 |
| 注释**剥离**而非跳过 | 需求明确：Mod JSON 注释无需保留 |
| 排除 NDJSON/MongoDB/JSONP/Regex/Markdown/字符串拼接 | 游戏 Mod 场景不涉及 |
| `＂`（全角双引号）加入双引号族 | 游戏 Mod 可能用到 |
| 前导零 `007` 保持为数字不转字符串 | 游戏 ID 语义 |
| 重复逗号修复（扩展自 streaming 版） | 旧版已支持，需保持兼容 |

## 错误处理

修复失败时抛 `std::runtime_error`，消息格式：

| 错误 | 触发条件 |
|------|---------|
| `unexpected end of json string` | 空输入（`parseValue` 返回 false） |
| `unexpected character 'X' at position N` | 修复后仍有无法消费的字符 |
| `object key expected at position N` | 对象内遇到非键名非结束符 |
| `colon expected at position N` | 键名后既非冒号也非值开头 |
| `invalid unicode character at position N` | `\u` 后跟非法 hex |

## 调用链

```
JsonDoc::parse(text, clean=true)
  → clean_text(text)              // json_cleaner.cpp
    → JsonRepairer(text).repair()
  → yyjson_read_opts(cleaned, ...)
```

```
JsonDoc::parse(text, clean=false)
  → yyjson_read_opts(text, ...)   // 跳过清洗
```

## 测试用例（test_json.cpp）

### 清洗测试 — 缺失逗号（9 case）

| 测试 | 输入 | 期望 |
|------|------|------|
| `clean missing comma after string value` | `{"a":"x" "b":"y"}` | `{"a":"x", "b":"y"}` |
| `clean missing comma after number` | `{"a":1 "b":2}` | `{"a":1, "b":2}` |
| `clean missing comma after bool` | `{"a":true "b":false}` | `{"a":true, "b":false}` |
| `clean missing comma after null` | `{"a":null "b":1}` | `{"a":null, "b":1}` |
| `clean missing comma after close brace` | `{"a":{} "b":1}` | `{"a":{}, "b":1}` |
| `clean missing comma after close bracket` | `{"a":[] "b":1}` | `{"a":[], "b":1}` |
| `clean missing comma after negative number` | `{"a":-1 "b":2}` | `{"a":-1, "b":2}` |
| `clean no false positive` | `{"a":1,"b":"hello","c":true}` | 不变 |
| `clean preserves strings` | `{"a":"hello world" "b":1}` | `{"a":"hello world", "b":1}` |

### 清洗测试 — 重复逗号（4 case）

| 测试 | 输入 | 期望 |
|------|------|------|
| `clean duplicate commas basic` | `{"a":1,,,"b":2}` | `{"a":1,"b":2}` |
| `clean duplicate commas with whitespace` | `{"a":1, , "b":2}` | `{"a":1 , "b":2}` |
| `clean duplicate commas preserves strings` | `{"a":",,","b":1}` | 不变 |
| `clean combined missing and duplicate` | `{"a":1 "b":2,,,"c":3}` | `{"a":1, "b":2,"c":3}` |

### 清洗测试 — 数组缺逗号（7 case）

| 测试 | 输入 | 期望 |
|------|------|------|
| `clean array missing comma strings` | `["a" "b" "c"]` | `["a", "b", "c"]` |
| `clean array missing comma numbers` | `[1 2 3]` | `[1, 2, 3]` |
| `clean array missing comma booleans` | `[true false null]` | `[true, false, null]` |
| `clean array missing comma objects` | `[{"a":1} {"b":2}]` | `[{"a":1}, {"b":2}]` |
| `clean array missing comma arrays` | `[[1] [2]]` | `[[1], [2]]` |
| `clean array missing comma mixed` | `[1 "a" true null]` | `[1, "a", true, null]` |
| `clean array missing comma negative` | `[1 -2 3]` | `[1, -2, 3]` |

### 清洗测试 — 引号修复（10 case）

| 测试 | 验证内容 |
|------|---------|
| `clean chinese double quotes pair` | `“`/`”` 对 → ASCII `"` |
| `clean chinese double quotes left-ascii` | `“` 开 + ASCII `"` 关 |
| `clean chinese double quotes in value` | 值位置中文引号 |
| `clean chinese quotes content preserved` | ASCII `"` 内中文引号为内容，不转义 |
| `clean single quotes` | `'` → `"` |
| `clean backtick quotes` | `` ` `` → `"` |
| `clean fancy single quotes` | `‘`/`’` → `"` |
| `clean unquoted keys` | `name:"test"` → `"name":"test"` |
| `clean unquoted key with underscore` | `my_key:1` → `"my_key":1` |
| `clean unquoted key with digits` | `key123:1` → `"key123":1` |

### 清洗测试 — 其他（21 case）

缺少冒号、注释剥离、尾随/前导逗号、缺失/多余括号、缺失对象值、截断数字、Python 关键字、省略号、转义处理、特殊空白、换行自动关闭字符串、集成测试。

### 解析/序列化/文件/移动语义（31 case）

验证 `clean_text` 与 `JsonDoc::parse` 的集成、文件 I/O、移动语义、重复键保留等。
