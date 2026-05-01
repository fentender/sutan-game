# 01 — 数据类型

核心类型定义和差异数据结构，是整个合并系统的基础。所有模块都依赖此处定义的类型。

## 涉及文件

| 文件 | 职责 |
|------|------|
| `src/core/infra/types.py` | 类型别名、合并模式枚举、差异树数据结构 |

## 模块依赖

仅依赖 `beartype`（运行时类型校验）。被所有其他模块引用。

---

## 基础类型别名

使用 Python 3.12+ `type` 语句定义，支持递归：

| 类型别名 | 定义 | 用途 |
|----------|------|------|
| `JsonPrimitive` | `bool \| int \| float \| str \| None` | JSON 标量值 |
| `JsonValue` | `JsonPrimitive \| list[JsonValue] \| dict[str, JsonValue]` | 任意 JSON 值（递归定义） |
| `JsonObject` | `dict[str, JsonValue]` | JSON 对象 |
| `JsonArray` | `list[JsonValue]` | JSON 数组 |
| `CancelCheck` | `Callable[[], None]` | 取消检查回调 |
| `ProgressCallback` | `Callable[[int, int], None]` | 进度回调 (完成, 总数) |
| `DeltaEntry` | `FieldDiff \| DictFieldDiff \| ArrayFieldDiff` | 差异树节点联合类型 |

---

## MergeMode 枚举

四种合并模式决定 [差异计算](04-diff-engine.md) 和 [合并应用](05-merge-engine.md) 的行为：

| 值 | 描述 |
|----|------|
| `NORMAL` | 全部应用 APPEND / CHANGE / DELETED |
| `SMART` | APPEND / CHANGE 全部应用，DELETED 按字段规则选择性应用 |
| `REPLACE` | 直接用 Mod 文件替换，不做字段级合并 |
| `ADAPTIVE` | 基于 Mod 更新时间匹配历史游戏版本计算差异，合并行为同 SMART |

---

## ChangeKind 标志位枚举

`enum.IntFlag`，二进制标志位设计，低 2 位为基础类型，高位为修饰标志：

| 位 | 值 | 含义 |
|----|-----|------|
| bit0-1 | 0 | `ORIGIN` — 未修改，来自 base |
| bit0-1 | 1 | `ADDED` — 新增 |
| bit0-1 | 2 | `DELETED` — 删除 |
| bit0-1 | 3 | `CHANGED` — 修改 |
| bit2 | 4 | `MULTI_MOD` — 被多个 Mod 修改 |
| bit3 | 8 | `OVERRIDE` — 被用户手动覆写 |

便捷属性：`base_kind`、`is_multi_mod`、`is_override`、`is_origin`、`is_added`、`is_deleted`、`is_changed`。

---

## 差异树数据结构

三个 dataclass 组成树形 delta 描述，联合类型为 `DeltaEntry`。贯穿 [差异计算](04-diff-engine.md) 和 [合并引擎](05-merge-engine.md) 两个模块。

### FieldDiff（叶子节点）

标量字段的差异标签，是 delta 树的叶子节点。

| 字段 | 类型 | 说明 |
|------|------|------|
| `kind` | `ChangeKind` | 变化类型（含修饰标志） |
| `value` | `JsonValue` | 当前值（全量时为 `JsonPrimitive`，稀疏 delta 时为 `JsonValue`） |
| `old_value` | `JsonValue` | CHANGED 时为旧值，DELETED 时为被删除的值 |
| `version` | `int` | 哪次 Mod 迭代修改了此字段（0=原始） |

**类型约束**：`value` 和 `old_value` 只允许 `JsonValue`，不允许 `DeltaEntry`（即不能放 `DictFieldDiff`/`ArrayFieldDiff`/`FieldDiff`）。通过 `__setattr__` 使用 `beartype.door.die_if_unbearable` 进行运行时校验，违规直接抛异常。

序列化格式：`{"__type": "field", "kind": int, "version": int, "value": ..., "old_value": ...}`

### DictFieldDiff（字典节点）

dict 的字段级 delta / 全状态注解树，有两种语义：

- 作为**稀疏 delta**（`compute_delta` 产出）：仅含被修改的 key
- 作为**全状态树**（`from_dict` 产出）：包含所有 key，每个标注 ChangeKind

| 字段    | 类型                    | 说明             |
|---------|-------------------------|------------------|
| `items` | `dict[str, DeltaEntry]` | 子字段的差异映射 |

关键方法：

| 方法 | 说明 |
|------|------|
| `from_dict(data)` | 将普通 dict 转为全状态树（每个字段初始为 ORIGIN） |
| `to_dict()` | 转回普通 dict（跳过 DELETED 字段） |
| `to_delta_dict()` | 序列化为 JSON（带 `__type` 标记） |
| `from_delta_dict(data)` | 从序列化 JSON 恢复 |

序列化格式：`{"__type": "dict_delta", "items": {key: DeltaEntry序列化, ...}}`

### ArrayFieldDiff（数组节点）

数组的元素级 delta，基于 ID 追踪。

| 字段 | 类型 | 说明 |
|------|------|------|
| `diffs` | `list[DeltaEntry]` | 每个变化元素的 diff（可为 FieldDiff/DictFieldDiff/ArrayFieldDiff） |
| `base_count` | `int` | base 数组的元素数量 |
| `indices` | `list[int]` | 每个 diff 对应的元素 ID（1-based），`len(indices) == len(diffs)` |
| `order` | `list[int]` | 应用 delta 后数组的完整排列（含 0/-1 边界标记） |
| `is_duplist` | `bool` | 原始数组是否为 DupList |
| `old_order` | `list[int] \| None` | `apply_array_delta` 重建 order 时保存的旧 order |

ID 规则：

- 原始元素 ID = 1-based 索引（第 1 个元素 ID=1）
- 新增元素 ID = base_count + 递增序号
- 特殊值：0 = 数组开头，-1 = 数组末尾
- 约束：CHANGED/DELETED 的 ID 必须 ≤ base_count

序列化格式：`{"__type": "array_delta", "diffs": [...], "base_count": int, "indices": [...], "order": [...], "is_duplist": bool, "old_order": [...]}`

---

## Delta 序列化协议

三种 `DeltaEntry` 类型统一通过 `__type` 字段区分：

| `__type` 值      | 对应类型         |
|------------------|------------------|
| `"field"`        | `FieldDiff`      |
| `"dict_delta"`   | `DictFieldDiff`  |
| `"array_delta"`  | `ArrayFieldDiff` |

反序列化入口：`_delta_entry_from_dict(raw)` 根据 `__type` 分派到对应类型的恢复方法。

---

## DupList

继承 `list` 的特殊子类，表示游戏 JSON 中同名重复键的值集合。解析时将同名键的多个值收集为 DupList，序列化时还原为重复键。与普通 list 通过类型区分。

详见 [02 JSON 处理管线](02-json-pipeline.md) 中 DupList 处理章节。

---

## 其他类型

| 类型 | 说明 |
|------|------|
| `ArrayMatching` | 数组匹配结果（配对列表 + 未匹配列表 + 置信度），详见 [04 差异计算](04-diff-engine.md) |
| `GlobalFieldEntry` / `FieldInfo` | schema 生成器的字段信息 TypedDict，详见 [03 Schema](03-schema-system.md) |
| `ParseFailure` | JSON 解析失败记录（含文件路径、错误信息、所属 Mod），提供 `from_error()` 工厂方法 |
| `normalize_rel_path()` | 路径规范化工具函数（相对路径 + 分隔符统一为 `/`） |
| `FIELD_SEP` | 路径分隔符常量 `'\x01'`（避免和 JSON key 中的点号冲突） |
