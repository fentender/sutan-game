# 01 — 数据类型

核心类型定义和差异数据结构，是整个合并系统的基础。所有模块都依赖此处定义的类型。

## 涉及文件

| 文件 | 职责 |
|------|------|
| `src/core/infra/types.py` | 类型别名、合并模式枚举、差异树数据结构 |

## 模块依赖

零依赖，是最底层模块。被所有其他模块引用。

---

## 基础类型别名

| 类型别名 | 定义 | 用途 |
|----------|------|------|
| `JsonPrimitive` | `bool \| int \| float \| str \| None` | JSON 标量值 |
| `JsonObject` | `dict[str, object]` | JSON 对象 |
| `JsonArray` | `list[object]` | JSON 数组 |
| `JsonValue` | `JsonPrimitive \| JsonArray \| JsonObject` | 任意 JSON 值 |
| `CancelCheck` | `Callable[[], None]` | 取消检查回调 |
| `ProgressCallback` | `Callable[[int, int], None]` | 进度回调 (完成, 总数) |

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

二进制标志位设计，低 2 位为基础类型，高位为修饰标志：

| 位 | 值 | 含义 |
|----|-----|------|
| bit0-1 | 0 | `ORIGIN` — 未修改，来自 base |
| bit0-1 | 1 | `ADDED` — 新增 |
| bit0-1 | 2 | `DELETED` — 删除 |
| bit0-1 | 3 | `CHANGED` — 修改 |
| bit2 | 4 | `MULTI_MOD` — 被多个 Mod 修改 |
| bit3 | 8 | `OVERRIDE` — 被用户手动覆写 |

提供 `base_kind`、`is_multi_mod`、`is_override` 等便捷属性。

---

## 差异树数据结构

三个 dataclass 组成树形 delta 描述，贯穿 [差异计算](04-diff-engine.md) 和 [合并引擎](05-merge-engine.md) 两个模块。

### FieldDiff

标量字段的差异叶子节点。

| 字段 | 类型 | 说明 |
|------|------|------|
| `kind` | `ChangeKind` | 变化类型（含修饰标志） |
| `value` | `object` | ADDED/CHANGED: 新值; DELETED: None |
| `old_value` | `object` | CHANGED: 旧值; DELETED: 被删除的值 |
| `version` | `int` | 哪次 Mod 迭代修改了此字段（0=原始） |

### DiffDict

dict 的字段级 delta / 全状态注解树，有两种语义：

- 作为**稀疏 delta**（`compute_delta` 产出）：仅含被修改的 key
- 作为**全状态树**（`from_dict` 产出）：包含所有 key，每个标注 ChangeKind

关键方法：

| 方法 | 说明 |
|------|------|
| `from_dict(data)` | 将普通 dict 转为全状态 DiffDict（每个字段初始为 ORIGIN） |
| `to_dict()` | 转回普通 dict（跳过 DELETED 字段） |
| `to_delta_dict()` / `from_delta_dict()` | 可 JSON 序列化的 delta 格式（用于 override 持久化） |

### ArrayFieldDiff

数组的元素级 delta，基于 ID 追踪。

| 字段 | 类型 | 说明 |
|------|------|------|
| `diffs` | `list[FieldDiff]` | 每个变化元素的 diff |
| `base_count` | `int` | base 数组的元素数量 |
| `indices` | `list[int]` | 每个 diff 对应的元素 ID（1-based） |
| `order` | `list[int]` | 应用 delta 后数组的完整排列（含 0/-1 边界标记） |
| `is_duplist` | `bool` | 原始数组是否为 DupList |

ID 规则：原始元素 ID = 1-based 索引，新增元素 ID = base_count + 递增序号。

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
| `ParseFailure` | JSON 解析失败记录（含文件路径、错误信息、所属 Mod） |
| `normalize_rel_path()` | 路径规范化工具函数 |
