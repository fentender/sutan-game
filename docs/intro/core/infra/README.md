# 基础设施层 (infra)

`src/core/infra/` 是零依赖的最底层模块，为整个 core 提供类型定义、诊断信息收集和性能评估能力。

## 文件列表

| 文件              | 职责                                                   |
| ----------------- | ------------------------------------------------------ |
| `types.py`        | 全局类型别名、合并模式枚举、delta 差异数据结构          |
| `diagnostics.py`  | 线程安全的全局诊断信息收集器                            |
| `profiler.py`     | 可选的性能评估模块（装饰器 + 上下文管理器）             |

---

## types.py — 全局类型与差异数据结构

### 基础类型别名

| 类型别名 | 定义 | 用途 |
| --- | --- | --- |
| `JsonPrimitive` | `bool \| int \| float \| str \| None` | JSON 标量值 |
| `JsonObject` | `dict[str, object]` | JSON 对象 |
| `JsonArray` | `list[object]` | JSON 数组 |
| `JsonValue` | `JsonPrimitive \| JsonArray \| JsonObject` | 任意 JSON 值 |
| `CancelCheck` | `Callable[[], None]` | 取消检查回调 |
| `ProgressCallback` | `Callable[[int, int], None]` | 进度回调 (完成, 总数) |

### MergeMode 枚举

四种合并模式决定 delta 计算和应用的行为：

| 值         | 描述                                                                      |
| ---------- | ------------------------------------------------------------------------- |
| `NORMAL`   | 全部应用 APPEND / CHANGE / DELETED                                        |
| `SMART`    | APPEND / CHANGE 全部应用，DELETED 按字段规则选择性应用                     |
| `REPLACE`  | 直接用 Mod 文件替换，不做字段级合并                                        |
| `ADAPTIVE` | 基于 Mod 更新时间匹配历史游戏版本计算差异，合并行为同 SMART               |

### ChangeKind 标志位枚举

二进制标志位设计，低 2 位为基础类型，高位为修饰标志：

| 位    | 值   | 含义                           |
| ----- | ---- | ------------------------------ |
| bit0-1 | 0   | `ORIGIN` — 未修改，来自 base   |
| bit0-1 | 1   | `ADDED` — 新增                 |
| bit0-1 | 2   | `DELETED` — 删除               |
| bit0-1 | 3   | `CHANGED` — 修改               |
| bit2   | 4   | `MULTI_MOD` — 被多个 Mod 修改  |
| bit3   | 8   | `OVERRIDE` — 被用户手动覆写    |

提供 `base_kind`、`is_multi_mod`、`is_override` 等便捷属性。

### 差异树数据结构

三个 dataclass 组成树形 delta 描述：

#### FieldDiff

标量字段的差异叶子节点。

| 字段        | 类型          | 说明                                    |
| ----------- | ------------- | --------------------------------------- |
| `kind`      | `ChangeKind`  | 变化类型（含修饰标志）                   |
| `value`     | `object`      | ADDED/CHANGED: 新值; DELETED: None       |
| `old_value` | `object`      | CHANGED: 旧值; DELETED: 被删除的值       |
| `version`   | `int`         | 哪次 Mod 迭代修改了此字段（0=原始）      |

#### DiffDict

dict 的字段级 delta / 全状态注解树。

- 作为 **稀疏 delta**（`compute_delta` 产出）：仅含被修改的 key
- 作为 **全状态树**（`from_dict` 产出）：包含所有 key，每个标注 ChangeKind

关键方法：
- `from_dict(data)` — 将普通 dict 转换为全状态 DiffDict（每个字段初始为 ORIGIN）
- `to_dict()` — 转换回普通 dict（跳过 DELETED 字段）
- `to_delta_dict()` / `from_delta_dict()` — 可 JSON 序列化的 delta 格式（用于 override 持久化）

#### ArrayFieldDiff

数组的元素级 delta，基于 ID 追踪。

| 字段         | 类型              | 说明                                           |
| ------------ | ----------------- | ---------------------------------------------- |
| `diffs`      | `list[FieldDiff]` | 每个变化元素的 diff                             |
| `base_count` | `int`             | base 数组的元素数量                             |
| `indices`    | `list[int]`       | 每个 diff 对应的元素 ID（1-based）              |
| `order`      | `list[int]`       | 应用 delta 后数组的完整排列（含 0/-1 边界标记） |
| `is_duplist` | `bool`            | 原始数组是否为 DupList                          |

ID 规则：原始元素 ID = 1-based 索引，新增元素 ID = base_count + 递增序号。

### DupList

继承 `list` 的特殊子类，表示游戏 JSON 中同名重复键的值集合。解析时将同名键的多个值收集为 DupList，序列化时还原为重复键。与普通 list 通过类型区分。

### 其他类型

- `ArrayMatching` — 数组匹配结果（配对列表 + 未匹配列表 + 置信度）
- `GlobalFieldEntry` / `FieldInfo` — schema 生成器的字段信息 TypedDict
- `ParseFailure` — JSON 解析失败记录（含文件路径、错误信息、所属 Mod）
- `normalize_rel_path()` — 路径规范化工具函数

---

## diagnostics.py — 全局诊断信息收集器

### Diagnostics 单例

替代 Python `logging` 模块，线程安全地收集所有诊断消息。

```python
diag = Diagnostics()  # 模块级单例
```

| 方法                        | 说明                                           |
| --------------------------- | ---------------------------------------------- |
| `diag.info(category, msg)`  | 追加信息级别消息                                |
| `diag.warn(category, msg)`  | 追加警告级别消息                                |
| `diag.error(category, msg)` | 追加错误级别消息                                |
| `diag.snapshot(*categories)` | 返回指定类别的消息并清空（不传参则返回全部）   |

四个标准类别：`parse`、`scan`、`merge`、`schema`。

### MergeContext 线程局部上下文

线程本地变量，由 `merge_file` 设置，供 `apply_delta` 内部的警告生成读取当前 Mod 信息。

```python
merge_ctx = MergeContext()  # 模块级线程局部实例
```

| 字段          | 类型   | 说明                    |
| ------------- | ------ | ----------------------- |
| `mod_name`    | `str`  | 当前正在处理的 Mod 名称  |
| `mod_id`      | `str`  | 当前 Mod ID              |
| `rel_path`    | `str`  | 当前文件相对路径         |
| `source_file` | `str`  | Mod 源文件绝对路径       |

#### 设计动机

`apply_delta()` 是一个递归函数，调用层次很深。当合并过程中发现类型不匹配或未知字段等问题时，需要输出包含"哪个 Mod、哪个文件"的警告消息。但 `apply_delta` 的签名不包含 mod 信息——把这些参数逐层传递会污染每一级递归调用的签名。

`MergeContext` 继承 `threading.local`，提供线程本地的隐式上下文，避免侵入函数签名。调用者在进入合并循环前设置上下文，`apply_delta` 内部的警告生成函数直接读取。

#### 用法示例

**写入端** — 在 `merge_file()` 的 mod 循环中设置上下文（`merge/merger.py`）：

```python
from src.core.infra import merge_ctx

for step, (mod_id, mod_name, delta, source_file) in enumerate(mod_data_list, 1):
    # 在调用 apply_delta 之前设置当前 mod 的上下文
    merge_ctx.mod_name = mod_name
    merge_ctx.mod_id = mod_id
    merge_ctx.rel_path = rel_path
    merge_ctx.source_file = source_file

    apply_delta(current, delta, schema, fp, version=step)
```

**读取端** — `_build_warn_msg()` 从上下文拼接警告前缀（`merge/merger.py`）：

```python
from src.core.infra import merge_ctx

def _build_warn_msg(field_path: list[str] | None, msg: str) -> str:
    parts: list[str] = []
    if merge_ctx.mod_name:
        parts.append(f"[{merge_ctx.mod_name}]")
    if merge_ctx.source_file:
        parts.append(merge_ctx.source_file)
    elif merge_ctx.rel_path:
        parts.append(merge_ctx.rel_path)
    if field_path:
        parts.append(".".join(field_path))
    prefix = " > ".join(parts)
    return f"{prefix}: {msg}" if prefix else msg
```

**输出效果**：`[超级武器Mod] > D:/mods/12345/config/cards.json > attack.damage: 类型不匹配，期望 int 实际为 str`

#### 生命周期

1. **初始化** — 模块加载时创建全局单例 `merge_ctx`，所有字段为空字符串
2. **设置** — `merge_file()` 或 `MergeCache._compute_file()` 在每个 mod 循环迭代前写入四个字段
3. **读取** — `apply_delta()` 递归深处若触发警告，`_build_warn_msg()` 读取当前上下文生成带定位信息的消息
4. **覆盖** — 下一个 mod 迭代时字段被覆写；`threading.local` 保证各线程互不干扰

#### 使用位置

| 文件 | 操作 | 说明 |
| --- | --- | --- |
| `merge/merger.py` `merge_file()` | 写入 | 合并主流程中逐 mod 设置上下文 |
| `merge/cache.py` `_compute_file()` | 写入 | 缓存重算流程中逐 mod 设置上下文 |
| `merge/merger.py` `_build_warn_msg()` | 读取 | 拼接带 mod 名称和文件路径的警告消息 |

---

## profiler.py — 可选性能评估模块

全局开关控制，默认关闭。关闭时 `@profile` 和 `profile_block` 零开销（直接透传）。

### 接口

| 接口                    | 说明                                   |
| ----------------------- | -------------------------------------- |
| `enable()` / `disable()` | 全局开关                              |
| `@profile`              | 装饰器，记录函数执行时间               |
| `profile_block(name)`   | 上下文管理器，记录代码块执行时间        |
| `get_report(top_n=20)`  | 生成性能报告（按总耗时降序排列）        |
| `reset()`               | 清空所有统计数据                       |

通过 `user_config.json` 的 `enable_profiler` 字段控制启动时是否自动开启。

### 统计信息

每个被监控的函数/代码块记录：调用次数、总耗时、最小耗时、最大耗时。
