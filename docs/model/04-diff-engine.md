# 04 — 差异计算引擎

负责计算每个 Mod 相对游戏本体（或历史版本）的字段级差异。输入是两份 JSON 数据（base + mod），输出是仅含变化部分的稀疏 DiffDict。包含数组元素匹配和 SMART 模式删除过滤。

## 涉及文件

| 文件 | 职责 |
|------|------|
| `src/core/merge/delta.py` | 全局 Delta 缓存管理器，递归 diff 计算 |
| `src/core/merge/array_match.py` | 四种数组匹配策略 |
| `src/core/merge/rules.py` | SMART 模式删除判定规则 |

## 模块依赖

```
01 数据类型 ------+
02 JSON 管线 -----+--> delta.py --+--> array_match.py
03 Schema 规则 ---+               +--> rules.py
08 平台集成 ------+ (ADAPTIVE 模式)
```

---

## ModDelta 缓存管理器（delta.py）

纯静态类，管理全局 Delta 缓存。启动时调用 `init()` 预计算所有 Mod 的 delta，后续通过 `get()` 直接取缓存。

| 方法 | 说明 |
|------|------|
| `init(mod_ids, schema_dir, ...)` | 预计算所有 Mod delta 并缓存 |
| `get(mod_id, rel_path)` -> DiffDict | 获取缓存的 delta |
| `has(mod_id, rel_path)` -> bool | 是否有缓存 |
| `invalidate()` | 清空缓存 |

### 多线程并行

任务量 > 20 时使用 `ThreadPoolExecutor` 并行计算。有 REPLACE 模式的 Mod 时按文件分组串行（REPLACE delta 依赖累积合并状态）。

---

## compute_delta — 递归差异计算（delta.py）

`compute_delta(base_data, mod_data, file_type, schema, root_key, merge_mode)` 是差异计算的主入口，根据 [文件分类](02-json-pipeline.md) 结果分发：

- **dictionary**：按条目级 + 字段级递归 diff（顶层键是实体 ID，Mod 不包含某 ID 不代表删除）
- **entity/config**：按字段级递归 diff

### _recursive_delta 三路递归比较

| 数据类型 | 处理方式 |
|----------|---------|
| dict | 逐 key 递归，新增字段产出 ADDED，消失字段产出 DELETED（受 SMART 模式过滤） |
| list | 先选择数组匹配策略，再按匹配结果产出元素级 diff |
| 标量 | 值不同则产出 CHANGED |

---

## ADAPTIVE 模式（delta.py）

基于 Mod 更新时间匹配历史游戏版本计算差异（依赖 [08 平台集成](08-platform.md) 的历史版本管理）：

1. 从 `.acf` 文件读取 Mod 的 `update_time`（Steam 时间戳）
2. 从 `history_config/` 中找最接近的游戏版本（时间戳）
3. 用该版本的 `base_data` 而非当前本体计算 delta
4. 通过 `_remap_delta_to_current()` 重映射到当前本体的索引/key 体系
5. 合并行为等同 SMART

优势：应对游戏本体大版本更新导致的 Mod 过时问题。

---

## 数组匹配策略（array_match.py）

四种匹配策略，由 `_select_array_matching` 根据数据特征自动选择：

| 函数 | 策略 | 说明 |
|------|------|------|
| `match_by_keys` | 按键匹配 | 按 match_keys 分组 + 多对多相似度配对 |
| `match_by_index` | 按位置 | 索引一一对应 |
| `match_by_consume` | 消耗式 | 按值相等消耗匹配 |
| `match_by_heuristic` | 启发式 | 四阶段全局匹配（默认使用） |

### 启发式匹配四阶段

`match_by_heuristic()` 假设 Mod 作者不调整原有元素的相对顺序：

1. **COMMON_MATCH_KEYS 精确匹配**：按 `guid`/`id`/`tag`/`key` 滑动窗口匹配，未匹配不推进窗口
2. **内容字段模糊匹配**：对 `result_text`/`condition`/`action` 等字段，使用 Levenshtein 距离做全局双向最优分配（阈值 < 33%）
3. **兜底相似度匹配**：对剩余未配对元素，使用序列化字符串 fuzzy ratio 做全局匹配（阈值 > 50%）
4. **位置间隙对应**：将未配对元素按已匹配对之间的间隙分组，同一间隙内按顺序一一配对

匹配结果包含置信度：阶段 1 完全覆盖时为 1.0，使用了兜底匹配则降为 0.3。

其他工具：`COMMON_MATCH_KEYS = ('guid', 'id', 'tag', 'key')`，`item_similarity()` 序列化后 fuzzy ratio，`resolve_duplicates()` 多对多贪心配对。

---

## SMART 删除规则（rules.py）

### smart_allow_deletion(field_path, is_array_element) -> bool

SMART 模式的核心原则：**保守策略——默认禁止删除**，仅在特定上下文允许。

| 条件 | 结果 |
|------|------|
| 数组元素（`is_array_element=True`） | 禁止 |
| 路径中包含 `condition` / `action` / `result` | 允许 |
| 字段名为 `result_title` / `result_text` | 允许 |
| 其他情况 | 禁止 |

设计目的：防止版本落后的 Mod 误删游戏新增的元素和字段。
