# 合并引擎层 (merge)

`src/core/merge/` 是合并管理器的核心计算层，负责差异计算、合并应用、数组匹配、结果缓存和格式化输出。

## 文件列表

| 文件              | 职责                                              |
| ----------------- | ------------------------------------------------- |
| `delta.py`        | 全局 Delta 缓存管理器，递归 diff 计算              |
| `merger.py`       | 核心合并算法，将 delta 应用到全状态树               |
| `array_match.py`  | 四种数组匹配策略                                   |
| `cache.py`        | 合并结果缓存 + 逐 Mod 中间可视化数据               |
| `formatter.py`    | Diff 格式化（左右对齐文本 + 行级高亮）              |
| `rules.py`        | SMART 模式删除判定规则                              |

## 依赖关系

```
infra + json + schema
        |
        v
  array_match.py (独立工具)
        |
        v
    delta.py (差异计算 + 缓存)
        |
        v
    merger.py (合并应用)
        |
        +---> rules.py (删除规则)
        |
        v
    cache.py (结果缓存)
        |
        v
  formatter.py (格式化输出)
```

---

## delta.py — 全局 Delta 缓存管理器

### ModDelta 类

纯静态类，管理全局 Delta 缓存。启动时调用 `init()` 预计算所有 Mod 的 delta，后续通过 `get()` 直接取缓存。

| 方法                                    | 说明                                     |
| --------------------------------------- | ---------------------------------------- |
| `init(mod_ids, schema_dir, ...)`        | 预计算所有 Mod delta 并缓存               |
| `get(mod_id, rel_path)` -> DiffDict     | 获取缓存的 delta                         |
| `has(mod_id, rel_path)` -> bool         | 是否有缓存                               |
| `invalidate()`                          | 清空缓存                                 |

### compute_delta() — 递归差异计算

`compute_delta(base_data, mod_data, file_type, schema, root_key, merge_mode)` 是差异计算的主入口，根据文件类型分发：

- **dictionary**：按条目级 + 字段级递归 diff（顶层键是实体 ID，Mod 不包含某 ID 不代表删除）
- **entity/config**：按字段级递归 diff

内部调用 `_recursive_delta()` 进行三路递归比较：

| 数据类型 | 处理方式                                       |
| -------- | ---------------------------------------------- |
| dict     | 逐 key 递归，新增字段产出 ADDED，消失字段产出 DELETED（受 SMART 模式过滤） |
| list     | 先选择数组匹配策略，再按匹配结果产出元素级 diff  |
| 标量     | 值不同则产出 CHANGED                            |

### ADAPTIVE 模式

基于 Mod 更新时间匹配历史游戏版本计算差异：以历史版本作为 base 计算 delta，再通过 `_remap_delta_to_current()` 重映射到当前本体的索引/key 体系，合并行为等同 SMART。

### 多线程并行

任务量 > 20 时使用 `ThreadPoolExecutor` 并行计算。有 REPLACE 模式的 Mod 时按文件分组串行（REPLACE delta 依赖累积合并状态）。

---

## merger.py — 核心合并算法

### apply_delta() — 将 delta 应用到全状态树

`apply_delta(base, delta, schema, field_path, version, is_override)` 原地修改 DiffDict：

对 delta 中每个条目按类型分发：

| delta 条目类型  | 处理方式                                                |
| --------------- | ------------------------------------------------------- |
| `FieldDiff`     | DELETED/ADDED/CHANGED 直接替换，标记 MULTI_MOD 或 OVERRIDE |
| `DiffDict`      | 递归调用 `apply_delta`                                   |
| `ArrayFieldDiff` | 调用 `apply_array_delta`                                |

冲突标记规则：
- 字段被多个 Mod 修改 -> 标记 `MULTI_MOD`
- 用户手动覆写 -> 标记 `OVERRIDE`（不触发 MULTI_MOD）

### apply_array_delta() — 数组级合并

处理 CHANGED（递归 apply）、ADDED（ID 重分配后追加）、DELETED（标记删除）、order 重建（保持 Mod 元素顺序 + 保留前 Mod 新增元素位置）。

### merge_all_files() — 批量合并

遍历所有文件，通过 `MergeCache` 获取合并结果，输出到 `merged_output/`。`WHOLE_FILE_REPLACE` 集合（如 `sfx_config.json`）跳过字段级合并；`tag.json` 验证 name 字段一致性。

### 字段合并策略

| 策略          | 说明                                     |
| ------------- | ---------------------------------------- |
| `replace`     | 直接用新值替换旧值                        |
| `merge`       | 递归合并 dict 字段                        |
| `coerce`      | 类型兼容转换后替换                        |
| `append`      | 数组追加新元素                            |
| `smart_match` | 按 match key 匹配数组元素后逐元素合并     |

---

## array_match.py — 数组匹配策略

### 四种匹配策略

| 函数               | 策略       | 说明                                            |
| ------------------ | ---------- | ----------------------------------------------- |
| `match_by_keys`    | 按键匹配   | 按 match_keys 分组 + 多对多相似度配对            |
| `match_by_index`   | 按位置     | 索引一一对应                                     |
| `match_by_consume` | 消耗式     | 按值相等消耗匹配                                 |
| `match_by_heuristic` | 启发式   | 四阶段全局匹配（默认使用）                       |

### 启发式匹配四阶段

`match_by_heuristic()` 假设 Mod 作者不调整原有元素的相对顺序：

1. **COMMON_MATCH_KEYS 精确匹配**：按 `guid`/`id`/`tag`/`key` 滑动窗口匹配，未匹配不推进窗口
2. **内容字段模糊匹配**：对 `result_text`/`condition`/`action` 等字段，使用 Levenshtein 距离做全局双向最优分配（阈值 < 33%）
3. **兜底相似度匹配**：对剩余未配对元素，使用序列化字符串 fuzzy ratio 做全局匹配（阈值 > 50%）
4. **位置间隙对应**：将未配对元素按已匹配对之间的间隙分组，同一间隙内按顺序一一配对

匹配结果包含置信度：阶段 1 完全覆盖时为 1.0，使用了兜底匹配则降为 0.3。

其他：`COMMON_MATCH_KEYS = ('guid', 'id', 'tag', 'key')`，`item_similarity()` 序列化后 fuzzy ratio，`resolve_duplicates()` 多对多贪心配对。

---

## cache.py — 合并结果缓存

### MergeCache 单例

按文件缓存合并结果，统一 diff_dialog 和 merger 的计算逻辑。`need_steps=True` 时同时产出 `StepState`（逐 Mod 中间状态：左右对齐文本 + 每行 ChangeKind），供 diff_dialog 可视化。`need_steps=False` 时跳过格式化。缓存在 override 变更时按文件失效。

---

## formatter.py — Diff 格式化

### format_delta_json() — 核心格式化入口

```python
left_lines, right_lines, left_kinds, right_kinds = format_delta_json(
    delta, highlight_version=mod_version,
)
```

将全状态 DiffDict 序列化为**预对齐**的左右文本 + 每行高亮类型。根据 `highlight_version` 过滤：只有当前版本的字段参与高亮，其他字段视为 ORIGIN。

输出保证 `len(left_lines) == len(right_lines)`，已插入空行对齐。

### 行级 diff 工具

| 函数                         | 说明                                          |
| ---------------------------- | --------------------------------------------- |
| `diff_opcodes(a, b)`        | 行哈希 + rapidfuzz C++ 后端 diff（高性能）      |
| `build_padded_texts(...)`    | 根据 opcodes 在行数少的一侧插入空行对齐         |

---

## rules.py — SMART 模式删除判定

### smart_allow_deletion(field_path, is_array_element) -> bool

SMART 模式的核心原则：**保守策略——默认禁止删除**，仅在特定上下文允许。

| 条件                                                  | 结果     |
| ----------------------------------------------------- | -------- |
| 数组元素（`is_array_element=True`）                    | 禁止     |
| 路径中包含 `condition` / `action` / `result`           | 允许     |
| 字段名为 `result_title` / `result_text`                | 允许     |
| 其他情况                                               | 禁止     |

设计目的：防止版本落后的 Mod 误删游戏新增的元素和字段。
