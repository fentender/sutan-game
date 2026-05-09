# JSON 操作模块

## 概述

C++ 层 JSON 数据操作模块。对不可变 `JsonDoc` 进行分类判定、递归遍历、按字段名提取值或按映射表替换值/键。

**定位**：C++ 模块，通过 nanobind 暴露 Python API。Python 侧通过 opaque `JsonDoc` handle 调用。

**依赖**：Json 模块（`JsonDoc`）、yyjson（可变文档 API）。

## 文件结构

```
csrc/json_ops/
├── json_ops.h       # API 声明
└── json_ops.cpp     # 提取 / 替换实现
tests/cpp/
└── test_json_ops.cpp  # C++ 单元测试（17 case）
```

## C++ API

```cpp
#include "json_ops.h"
using namespace sultan;

auto doc = JsonDoc::parse(R"({"cards":{"100":{"id":100},"200":{"id":200}}})");

// ── 提取 ──

// 递归查找所有 key == "id" 的字符串类型值
auto names = extract_string_values(doc, "name");

// 递归查找所有 key == "id" 的整数类型值
auto ids = extract_int_values(doc, "id");
// ids == {100, 200}

// ── 按字段名定向替换（返回新 JsonDoc，原文档不变） ──

// 递归查找所有 key == "id" 的字段，替换其整数值
auto doc2 = replace_field_ints(doc, "id", {{100, 999}});
// doc 中 id 仍为 100，doc2 中 id 变为 999
// 同值但不同字段名（如 "count": 100）不受影响

// 递归查找所有 key == "name" 的字段，替换其字符串值
auto doc3 = replace_field_strs(doc, "name", {{"old_name", "new_name"}});

// 仅替换根级对象键（不递归到嵌套对象）
auto doc4 = replace_root_keys(doc, {{"100", "999"}});

// ── 分类 ──

// 判定 JSON 文件类型
auto type = classify_json(doc);  // "dictionary" | "entity" | "config"
```

## Python API

```python
import sultan_core

# JsonDoc opaque handle
doc = sultan_core.json.JsonDoc.parse('{"id": 42, "name": "test"}')
doc = sultan_core.json.JsonDoc.parse_file("config/cards.json")
text = doc.to_string(compact=True)

# 分类
file_type = sultan_core.json_ops.classify_json(doc)  # "entity"

# 字段提取
ids = sultan_core.json_ops.extract_int_values(doc, "id")       # [42]
names = sultan_core.json_ops.extract_string_values(doc, "name") # ["test"]

# 按字段名定向替换（返回新文档）
new_doc = sultan_core.json_ops.replace_field_ints(doc, "id", {42: 99})
new_doc = sultan_core.json_ops.replace_field_strs(doc, "name", {"test": "prod"})
new_doc = sultan_core.json_ops.replace_root_keys(doc, {"old_key": "new_key"})
```

## 内部实现

### 提取

递归遍历不可变 `yyjson_val*` 树：
- 对象：`yyjson_obj_iter` 遍历键值对，键匹配时收集值；递归遍历所有子值
- 数组：`yyjson_arr_iter` 遍历元素，递归遍历
- 标量：叶子节点

使用 `yyjson_equals_strn` 比较键名，避免额外字符串构造。

### replace_field_ints / replace_field_strs

递归遍历可变树，仅在**键匹配 field_name** 时才替换对应的值：

1. `yyjson_doc_mut_copy(doc, nullptr)` → 深拷贝为可变文档
2. 递归遍历：对象中键匹配 field_name 且值类型匹配时替换
   - **整数**：`yyjson_mut_set_sint(val, new_val)` — 原地修改
   - **字符串**：`yyjson_mut_set_strn(val, str, len)` — 指向 mapping 中的字符串
3. `yyjson_mut_doc_imut_copy(mut, nullptr)` → 深拷贝回不可变文档
4. `JsonDoc::from_raw(result)` → 包装为 JsonDoc 返回

同值但不同字段名的字段不受影响。

### replace_root_keys

仅遍历根级对象的键，不递归到嵌套对象。嵌套层级中的同名键不受影响。

原文档不受影响（所有替换操作在可变副本上进行）。

## 错误处理

- 无效文档（moved-from） → `throw std::runtime_error("Failed to create mutable copy")`
- 空映射表 → 跳过遍历，直接返回深拷贝
- 字段不存在 → 返回空 vector（不报错）

## 与后续模块的关系

### id_remap（Python → C++ 迁移）

```python
# 当前 Python _remap_dict_keys（cards.json）：
# 替换根级键 + 设置内部 "id" 字段
for key, value in data.items():
    new_key = remap.cards.get(key, key)
    value["id"] = int(new_key)

# C++ json_ops 组合：
doc = sultan_core.json.JsonDoc.parse_file(path)
doc = sultan_core.json_ops.replace_root_keys(doc, cards_mapping)
doc = sultan_core.json_ops.replace_field_ints(doc, "id", id_mapping)
```

### State / Delta 模块

State/Delta 模块可能需要从 JsonDoc 提取特定字段用于匹配和比较。

## 现有测试用例

### test_json_ops.cpp（17 case）

**提取测试（7 case）**

| 测试 | 验证内容 |
|------|---------|
| `json_ops: extract_string_values basic` | 从对象提取字符串字段值 |
| `json_ops: extract_string_values nested` | 深层嵌套对象中提取 |
| `json_ops: extract_string_values in array` | 数组内对象中提取 |
| `json_ops: extract_string_values no match` | 字段不存在返回空 |
| `json_ops: extract_int_values basic` | 提取整数字段值 |
| `json_ops: extract_int_values mixed types` | 同名字段不同类型，仅收集整数 |
| `json_ops: extract from empty doc` | 空对象/空数组 |

**替换测试（10 case）**

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
