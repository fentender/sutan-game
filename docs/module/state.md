# State 模块

## 概述

C++ 层 JSON 状态管理模块，描述一个 JSON 文件的变动状态（每个字段标注 ChangeKind：新增/修改/删除/未变）。供后续 Delta 模块使用：Delta 计算产出差异，State 持有合并中间结果。

**定位**：C++ 核心模块 + nanobind 绑定。Python 通过 `sultan_core.state` 子模块访问 ChangeKind/MergeMode/JsonState/FormatResult。

**依赖**：Json 模块（JsonDoc 解析/序列化）、yyjson（JSON 库）。

## 类型体系

| 类型 | 说明 |
|------|------|
| `ChangeKind` | 变化类型枚举（二进制标志位：低 2 位基础类型 + bit2 MultiMod + bit3 Override） |
| `MergeMode` | 合并模式枚举（Normal/Smart/Replace/Adaptive） |
| `ScalarValue` | 标量值类型（`variant<nullptr_t, bool, int64_t, double, string>`） |
| `StateBase` | 抽象基类，所有状态节点的公共接口 |
| `JsonElementState` | 叶子节点：标量元素（含 value/old_value/version） |
| `JsonDictState` | 字典节点：`unordered_map<string, StateNodePtr>` |
| `JsonArrayState` | 数组节点：基于 ID 追踪的元素列表 |
| `JsonState` | 顶层包装：工厂方法 + 转换 + 格式化入口 |
| `FormatResult` | 格式化输出：预对齐的左右文本行 + 每行 ChangeKind |

## 文件结构

```
csrc/state/
├── change_kind.h           # ChangeKind / MergeMode 枚举 + 位运算辅助
├── state_node.h            # StateBase 基类 + 三个派生类声明
├── state_node.cpp          # clone / serialize_scalar / is_modified 实现
├── json_state.h            # JsonState 类声明
├── json_state.cpp          # from_doc / to_doc / clone / from_text / from_file
├── state_formatter.h       # FormatResult + format_state 声明
└── state_formatter.cpp     # format_state 递归格式化实现
tests/cpp/
└── test_state.cpp          # C++ 单元测试（41 case）
```

## ChangeKind 标志枚举

### 位结构

```
bit3  bit2  bit1  bit0
 O     M    ───基础类型───
 ↓     ↓
Override  MultiMod
```

| 值 | 名称 | 说明 |
|----|------|------|
| 0 | Origin | 未修改 |
| 1 | Added | 新增 |
| 2 | Deleted | 删除 |
| 3 | Changed | 修改 |
| 4 | MultiMod | 标志位：多 mod 冲突 |
| 8 | Override | 标志位：用户手动覆写 |

数值与 Python `ChangeKind(IntFlag)` 完全一致。

### C++ API

```cpp
#include "change_kind.h"
using namespace sultan;

// 位运算
auto combined = ChangeKind::Added | ChangeKind::MultiMod;  // == 5

// 提取
ChangeKind base = base_kind(combined);    // Added
ChangeKind flags = change_flags(combined); // MultiMod

// 判断
bool origin  = is_origin(k);
bool added   = is_added(k);
bool deleted  = is_deleted(k);
bool changed  = is_changed(k);
bool multi   = is_multi_mod(k);
bool ovr     = is_override(k);
```

## StateBase 树结构

### StateBase（抽象基类）

```cpp
struct StateBase {
    virtual ChangeKind kind() const = 0;
    virtual bool is_modified() const = 0;
    virtual StateNodePtr clone() const = 0;

    bool is_element() const;
    bool is_dict() const;
    bool is_array() const;
    JsonElementState& as_element();    // 类型不匹配 throw
    JsonDictState& as_dict();
    JsonArrayState& as_array();
};
```

### JsonElementState

```cpp
struct JsonElementState : StateBase {
    ChangeKind kind_;
    ScalarValue value;
    ScalarValue old_value;  // 无旧值时为 nullptr
    int version = 0;        // 修改此字段的 mod 迭代号
};
```

### JsonDictState

```cpp
struct JsonDictState : StateBase {
    ChangeKind kind_;
    unordered_map<string, StateNodePtr> entries;

    StateBase* find(const string& key) const;
    void insert(string key, StateNodePtr node);
    size_t size() const;
};
```

不保序——格式化和序列化时按键名排序。

### JsonArrayState

```cpp
struct JsonArrayState : StateBase {
    ChangeKind kind_;
    vector<StateNodePtr> diffs;
    int base_count = 0;
    vector<int> indices;     // 元素 ID (1-based)
    vector<int> order;       // 完整顺序 (含边界 0/-1)
    bool is_duplist = false; // 重复键展开列表
    vector<int> old_order;
};
```

**ID 规则**：原始元素 ID = 1-based 索引，新增元素 ID > base_count，边界标记 0=开头 / -1=末尾。

## JsonState

### 生命周期

```cpp
auto state = JsonState::from_text(R"({"a": 1})");  // 工厂
auto state2 = std::move(state);                      // 移动语义
// state2 析构时递归释放状态树
```

禁止拷贝，支持移动。

### C++ API

```cpp
#include "json_state.h"
using namespace sultan;

// ── 构建 ──
auto state = JsonState::from_doc(json_doc);           // 从 JsonDoc
auto state = JsonState::from_text(text, clean=true);  // 从文本
auto state = JsonState::from_file(path, clean=true);  // 从文件
auto state = JsonState::from_node(std::move(node));   // 从 StateNodePtr

// ── 转换 ──
JsonDoc doc = state.to_doc();         // 剥离 ChangeKind，跳过 DELETED

// ── 格式化 ──
FormatResult r = state.format(1);     // highlight_version=1

// ── 克隆 ──
JsonState cloned = state.clone();     // 递归深拷贝

// ── 访问 ──
StateBase& root = state.root();
bool ok = state.valid();
```

### from_doc 流程

```
yyjson_val* 树
  ├─ object → JsonDictState（检测重复键 → is_duplist=true 的 JsonArrayState）
  ├─ array  → JsonArrayState（indices=[1..n], order=[0,1..n,-1]）
  └─ scalar → JsonElementState
所有 kind = Origin
```

### to_doc 流程

```
StateBase 树
  → 创建 yyjson_mut_doc
  → 递归转为 yyjson_mut_val*
  → 跳过 base_kind == Deleted 的节点
  → DupList 展开为重复键
  → yyjson_mut_doc_imut_copy → JsonDoc
```

## 格式化输出（FormatResult）

```cpp
struct FormatResult {
    vector<string> left_lines;   // 变更前文本行
    vector<string> right_lines;  // 变更后文本行
    vector<int8_t> left_kinds;   // -1=填充行, 0-15=ChangeKind
    vector<int8_t> right_kinds;
    size_t size() const;
};
```

### 语义

- `len(left_lines) == len(right_lines)` 始终成立（预对齐）
- 填充行：text="" , kind=-1
- ADDED 字段：右侧有值，左侧填充
- DELETED 字段：左侧有值，右侧填充
- CHANGED 字段：左侧旧值，右侧新值
- ORIGIN 字段：两侧相同文本
- `highlight_version` 过滤：只有 `version == highlight_version` 的字段参与高亮

### 与 Python formatter.py 的对应

C++ `format_state` 完整移植 Python `format_delta_json` 及其 10 个辅助函数（emit / emit_changed / get_field_kind / collect_dict_entries / collect_array_elements / format_entry / format_dict / format_array / format_duplist / serialize_node）。

## nanobind 绑定

通过 `sultan_core.state` 子模块暴露：

```python
from sultan_core.state import (
    ChangeKind, MergeMode,
    JsonState, FormatResult,
    base_kind, change_flags,
    is_origin, is_added, is_deleted, is_changed,
    is_multi_mod, is_override,
)

# 构建
state = JsonState.from_text('{"a": 1}')
state = JsonState.from_file("config/cards.json")

# 格式化
result = state.format(highlight_version=1)
for left, right, lk, rk in zip(
    result.left_lines, result.right_lines,
    result.left_kinds, result.right_kinds
):
    if lk >= 0 and is_deleted(ChangeKind(lk)):
        print(f"DELETED: {left}")

# 转回 JsonDoc
doc = state.to_doc()
json_text = doc.to_string()

# 克隆
state2 = state.clone()
```

ChangeKind 使用 `nb::is_arithmetic()` 支持位运算。FormatResult.left_kinds/right_kinds 为 `list[int]`（-1=填充行）。

## 与后续模块的关系

### Delta 模块（TODO 2.2）

```cpp
// Delta 修改 State 的 ChangeKind 和值
auto& elem = state.root().as_dict().find("field")->as_element();
elem.kind_ = ChangeKind::Changed;
elem.old_value = elem.value;
elem.value = new_value;
elem.version = mod_version;

// 格式化输出差异
auto result = state.format(mod_version);
```

## 错误处理

- `from_doc` 传入无效 JsonDoc → `throw runtime_error`
- `to_doc` 空 State → `throw runtime_error`
- `as_element()`/`as_dict()`/`as_array()` 类型不匹配 → `throw runtime_error`
- JSON 解析失败 → 由 JsonDoc 抛出 `runtime_error`

## 现有测试用例

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
| `state: from_doc array` | 数组（indices/order） |
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
| `state: to_doc skips deleted` | 跳过 DELETED |
| `state: to_doc duplist expansion` | DupList 展开为重复键 |
| `state: to_doc array order` | 按 order 顺序输出 |

**clone（3 case）**

| 测试 | 验证内容 |
|------|---------|
| `state: clone deep independence` | 修改 clone 不影响原树 |
| `state: clone preserves structure` | 结构一致性 |
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
