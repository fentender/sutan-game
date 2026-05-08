# Delta 模块

## 概述

C++ 层 Delta 计算与应用模块。计算两个 JSON 文档之间的差异（稀疏 delta），将 delta 应用到 State 树，以及 delta 的序列化/反序列化。

**定位**：C++ 核心模块 + nanobind 绑定（`sultan_core.delta` 子模块）。Python 通过函数式 API 访问（`compute_and_apply`、`compute_and_serialize`、`deserialize_and_apply`）。

**依赖**：Json 模块（JsonDoc/JsonVal/MutDoc/MutVal）、State 模块（StateBase 派生类、JsonState）。

## 类型体系

| 类型 | 说明 |
|------|------|
| `DeltaType` | 类型枚举（Element/Dict/Array），用于 switch 分发 |
| `DeltaBase` | 抽象基类，所有 delta 节点的公共接口 |
| `DeltaElement` | 叶子节点：标量差异（含 value/old_value/value_node/version） |
| `DeltaDict` | 字典差异：`unordered_map<string, DeltaNodePtr>` |
| `DeltaArray` | 数组差异：基于 ID 追踪的元素差异列表 |
| `ArrayMatching` | 数组匹配结果（pairs/unmatched_mod/unmatched_base/confidence） |

## 文件结构

```
csrc/delta/
├── similarity.h          # Levenshtein 距离 + ratio
├── similarity.cpp
├── delta_node.h           # DeltaType 枚举 + DeltaBase + 三个派生类
├── delta_node.cpp         # 类型转换 + clone + 工厂 + 序列化/反序列化
├── delta_rules.h          # SMART 模式删除判定
├── delta_rules.cpp
├── array_match.h          # ArrayMatching 结构 + match_by_heuristic
├── array_match.cpp        # 四阶段数组匹配算法
├── compute_delta.h        # compute_delta + remap_delta_to_current
├── compute_delta.cpp      # 递归 delta 计算
├── apply_delta.h          # apply_dict/array/field_delta
└── apply_delta.cpp        # delta 应用 + 类型归一化
tests/cpp/
└── test_delta.cpp         # C++ 单元测试（59 case）
```

## DeltaType 枚举

```cpp
enum class DeltaType : uint8_t { Element, Dict, Array };
```

与 State 模块的虚函数/RTTI 不同，Delta 模块使用枚举类型标签做 switch 分发。

## DeltaBase 抽象基类

```cpp
struct DeltaBase {
    virtual DeltaType type() const = 0;
    virtual ChangeKind kind() const = 0;
    virtual DeltaNodePtr clone() const = 0;
    as_element() / as_dict() / as_array();  // 类型转换，失败抛异常
};
```

**与 StateBase 区别**：无 `is_modified()` 方法（稀疏 delta 按定义已修改）。

## DeltaElement

```cpp
struct DeltaElement : DeltaBase {
    ChangeKind kind_;
    ScalarValue value;           // 新值（ADDED/CHANGED）
    ScalarValue old_value;       // 旧值（DELETED/CHANGED）
    StateNodePtr value_node;     // ADDED 复杂值（obj/arr → State 子树）
    int version = 0;
};
```

`value_node` 用于存储 ADDED 的复杂值（对象/数组）。apply 时 clone 后插入 State 树。

## 数组匹配（四阶段）

### match_by_heuristic(JsonVal base_arr, JsonVal mod_arr) → ArrayMatching

| 阶段 | 策略 | 门槛 |
|------|------|------|
| 1 | COMMON_MATCH_KEYS（guid/id/tag/key）精确匹配 | 唯一命中 |
| 2 | 内容字段（result_text/condition/action/result/result_title）Levenshtein 模糊匹配 | 距离 < 33% |
| 3 | 整体相似度匹配（JSON 序列化后 string_ratio） | > 0.5 |
| 4 | 位置间隙对应（同一间隙内按顺序配对） | dict > 0.5 |

## SMART 模式删除规则

```cpp
bool smart_allow_deletion(const vector<string>& field_path, bool is_array_element);
```

- 数组元素一律禁删
- 路径含 `condition`/`action`/`result` → 允许
- 字段名为 `result_title`/`result_text` → 允许
- 默认禁止

## C++ API

### 计算

```cpp
DeltaNodePtr compute_delta(
    const JsonDoc& base, const JsonDoc& mod,
    MergeMode merge_mode = MergeMode::Normal);
```

### 应用

```cpp
void apply_delta_to_state(
    JsonState& state, const DeltaDict& delta,
    const vector<string>* field_path = nullptr,
    int version = 0, bool is_override = false);
```

### ADAPTIVE 模式重映射

```cpp
DeltaNodePtr remap_delta_to_current(
    const DeltaDict& delta,
    const JsonDoc& hist_base,
    const JsonDoc& current_base);
```

将基于历史 base 计算的 delta 重映射到当前 base 的索引/key 体系。规则：

- DELETED 字段不在 current 中 → 丢弃
- ADDED 字段已存在于 current 且值相同 → 丢弃
- ADDED/CHANGED 字段已存在于 current 但值不同 → 重新计算 delta
- CHANGED 字段不在 current 中 → 转为 ADDED
- 数组元素通过 match_by_heuristic 重建 hist→current 索引映射

### 序列化

```cpp
JsonDoc serialize_delta(const DeltaBase& delta);
DeltaNodePtr deserialize_delta(const JsonDoc& doc);
```

序列化格式兼容 Python `to_delta_dict` / `from_delta_dict`：
- DeltaElement → `{"__type":"field", "kind":<int>, "version":<int>, "value":..., "old_value":...}`
- DeltaDict → `{"__type":"dict_delta", "kind":<int>, "items":{...}}`
- DeltaArray → `{"__type":"array_delta", "kind":<int>, "base_count":<int>, "indices":[...], "order":[...], "diffs":[...], "is_duplist":<bool>}`

## nanobind 绑定

`sultan_core.delta` 子模块提供函数式 API（不暴露 Delta 节点类型）：

```python
from sultan_core.delta import (
    compute_and_apply, compute_and_serialize,
    deserialize_and_apply, remap_delta,
)

# 计算并应用
compute_and_apply(base_doc, mod_doc, state, version=1, merge_mode=MergeMode.NORMAL)

# 计算并序列化为 JsonDoc
delta_doc = compute_and_serialize(base_doc, mod_doc, merge_mode=MergeMode.NORMAL)

# 反序列化并应用
deserialize_and_apply(delta_doc, state, version=1, is_override=False)

# ADAPTIVE 模式：重映射 delta 到当前 base（返回 JsonDoc 或 null doc）
remapped_doc = remap_delta(delta_doc, hist_base, current_base)
```

## 错误处理

- `compute_delta`：base/mod 无效 → 返回 nullptr
- `apply_delta_to_state`：state 无效或非 dict 根 → 抛异常
- `deserialize_delta`：JSON 格式不符 → 抛异常
- `remap_delta_to_current`：hist/current 无效 → 返回 nullptr；非 obj 根 → 保守返回原 delta
- `smart_allow_deletion`：纯函数，无异常

## 现有测试用例

### test_delta.cpp（71 case）

**相似度（9 case）**

| 测试 | 验证内容 |
|------|---------|
| `levenshtein empty strings` | 空字符串距离 0 |
| `levenshtein one empty` | 单空距离等于另一方长度 |
| `levenshtein identical` | 相同字符串距离 0 |
| `levenshtein classic` | kitten→sitting = 3 |
| `levenshtein single char` | 单字符比较 |
| `string_ratio identical` | 相同 → 1.0 |
| `string_ratio both empty` | 双空 → 1.0 |
| `string_ratio completely different` | 完全不同 → 接近 0 |
| `string_ratio partial` | 部分相同 → 0.4~0.6 |

**删除规则（8 case）**

| 测试 | 验证内容 |
|------|---------|
| `rules array element always denied` | 数组元素禁删 |
| `rules condition/action/result context allowed` | 三个允许上下文 |
| `rules result_text/result_title field allowed` | 两个允许字段 |
| `rules default denied` | 默认禁止 |
| `rules empty path denied` | 空路径禁止 |

**Delta 节点（12 case）**

| 测试 | 验证内容 |
|------|---------|
| `make_delta_element basic / with old_value` | 工厂函数 |
| `make_delta_dict insert and find` | 字典操作 |
| `make_delta_array basic` | 数组初始状态 |
| `delta_array wrap with value / nullptr` | wrap 逻辑 |
| `clone element / dict / array` | 深拷贝独立性 |
| `as_* throws on wrong type` | 类型转换异常 |

**数组匹配（6 case）**

| 测试 | 验证内容 |
|------|---------|
| `match empty arrays` | 空数组 |
| `match identical scalar arrays` | 相同数组全配对 |
| `match by guid key` | GUID 精确匹配 |
| `match by guid key reorder` | 乱序 GUID |
| `match with added/deleted elements` | 新增/删除检测 |

**compute_delta（9 case）**

| 测试 | 验证内容 |
|------|---------|
| `compute identical docs returns nullptr` | 无差异 → nullptr |
| `compute scalar field changed/added/deleted` | 标量字段变化 |
| `compute nested dict change` | 嵌套 dict |
| `compute array element added` | 数组元素新增 |
| `compute smart mode blocks/allows deletion` | SMART 模式过滤 |
| `compute added complex value has value_node` | 复杂值存储 |

**apply_delta（9 case）**

| 测试 | 验证内容 |
|------|---------|
| `direct state insert replaces entry` | base.insert 替换验证 |
| `apply_field_delta basic` | 字段级应用 |
| `apply added/changed/deleted scalar field` | 三种变化应用 |
| `apply multi_mod/override marking` | 标志位标记 |
| `apply nested dict delta` | 嵌套应用 |
| `apply added complex value with value_node` | 复杂值应用 |

**端到端（2 case）**

| 测试 | 验证内容 |
|------|---------|
| `e2e compute then apply scalar changes` | 标量端到端 |
| `e2e compute then apply nested changes` | 嵌套端到端 |

**序列化（4 case）**

| 测试 | 验证内容 |
|------|---------|
| `serialize element/dict/array/nested dict roundtrip` | 序列化往返一致 |

**remap（12 case）**

| 测试 | 验证内容 |
|------|---------|
| `remap deleted field not in current drops` | DELETED 字段不在 current → 丢弃 |
| `remap deleted field in current updates old_value` | DELETED 字段在 current → 更新 old_value |
| `remap added field not in current keeps` | ADDED 字段不在 current → 保留 |
| `remap added field same in current drops` | ADDED 字段已存在且值相同 → 丢弃 |
| `remap added field different in current recomputes` | ADDED 字段值不同 → 变 CHANGED |
| `remap changed field not in current converts to added` | CHANGED 字段不在 current → 变 ADDED |
| `remap changed field same in current drops` | CHANGED 目标值与 current 相同 → 丢弃 |
| `remap changed field different in current recomputes` | CHANGED 值不同 → 重算 old_value |
| `remap nested dict recursion` | 嵌套 dict 递归重映射 |
| `remap array reindex` | 数组元素位置变化后索引重映射 |
| `remap added complex value` | ADDED 复杂值（obj/arr）保留 |
| `remap e2e compute remap apply` | 端到端：计算→重映射→应用→验证 |
