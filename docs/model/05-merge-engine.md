# 05 — 合并引擎

将 [差异计算引擎](04-diff-engine.md) 产出的 delta 逐层应用到全状态 DiffDict 上，产出最终合并结果。同时负责结果缓存和格式化输出。

## 涉及文件

| 文件 | 职责 |
|------|------|
| `src/core/merge/merger.py` | 核心合并算法，将 delta 应用到全状态树 |
| `src/core/merge/cache.py` | 合并结果缓存 + 逐 Mod 中间可视化数据 |
| `src/core/merge/formatter.py` | Diff 格式化（左右对齐文本 + 行级高亮） |

## 模块依赖

```
01 数据类型 --------+
02 JSON 管线 -------+--> merger.py
03 Schema 规则 -----+       |
04 差异计算 --------+       |
07 诊断(MergeContext)       |
                             v
                       cache.py ---> formatter.py
```

> **交叉引用**：合并引擎依赖 [07 诊断与性能](07-diagnostics.md) 中的 `MergeContext` 线程局部上下文，用于在递归深处生成带 Mod 信息的警告消息。

---

## 四层 apply 函数（merger.py）

合并引擎的核心是四个函数，形成统一的递归分派结构：

| 函数 | 处理类型 | 说明 |
|------|---------|------|
| `_apply_delta_entry` | `DiffEntry` | 归一化 + 类型校验 + 三路分派（统一入口） |
| `apply_field_delta` | `FieldDiff` | 标量合并，返回新 FieldDiff |
| `apply_dict_delta` | `DictFieldDiff` | 字典合并，原地修改 |
| `apply_array_delta` | `ArrayFieldDiff` | 数组合并，原地修改 |

### _apply_delta_entry — 统一分派入口

`_apply_delta_entry(diff, existing, schema, field_path, version, is_override) -> DiffEntry | None`

`apply_dict_delta` 和 `apply_array_delta` 的循环体共享同一套逻辑，提取为此函数。返回合并后的值（FieldDiff 为新对象，DictFieldDiff/ArrayFieldDiff 为原地修改后的 existing），返回 None 表示类型校验失败应跳过。

处理流程：

1. **FieldDiff/ArrayFieldDiff 归一化**：一方为 FieldDiff 另一方为 ArrayFieldDiff 时，通过 `ArrayFieldDiff.wrap()` 将 FieldDiff 包装
2. **类型校验**：existing 存在时类型必须一致；existing 为 None 时仅 FieldDiff 合法（DictFieldDiff/ArrayFieldDiff 的 existing 为 None 说明 Mod 基于旧版本）
3. **三路分派**：

| delta 条目类型 | 处理方式 |
|----------------|---------|
| `FieldDiff` | 调用 `apply_field_delta`，返回新 FieldDiff |
| `DictFieldDiff` | 递归 `apply_dict_delta`（原地修改），返回 existing |
| `ArrayFieldDiff` | 递归 `apply_array_delta`（原地修改），返回 existing |

### apply_dict_delta

`apply_dict_delta(base, delta, schema, field_path, version, is_override)` 原地修改 DictFieldDiff。

对 delta 中每个条目：

1. **DupList 归一化**（仅 dict 需要）：任一方为 DupList 时，通过 `ArrayFieldDiff.wrap(value, is_dup=True)` 将另一方包装
2. 调用 `_apply_delta_entry` 完成归一化 + 校验 + 分派
3. 将返回值写回 `base.items[key]`

### apply_array_delta

`apply_array_delta(base, delta, schema, field_path, version, is_override)` 原地修改 ArrayFieldDiff。

ArrayFieldDiff 的 indices 相当于 DictFieldDiff 的 key，`elem_id in id_map` 等价于 `existing is not None`。

流程：

1. `_prepare_array_delta` 统一 delta 的 ID 空间
2. 构建 `id_map`（ID → 位置映射）
3. 对 delta 中每个条目：通过 id_map 查找 existing，调用 `_apply_delta_entry`，写回或 append
4. `_rebuild_array_order` 重建元素顺序

### apply_field_delta

`apply_field_delta(diff, existing, schema, child_path, version, is_override) -> FieldDiff`

纯函数，返回合并后的 FieldDiff。处理 modifier 计算和 schema 类型校验。

### 冲突标记规则

OVERRIDE 和 MULTI_MOD 为并列标志位，可同时存在：

- 字段被多个 Mod 修改 → `modifier |= MULTI_MOD`（`kind |= 4`）
- 用户手动覆写 → `modifier |= OVERRIDE`（`kind |= 8`）

---

## 数组 ID 映射与 order 重建（merger.py）

### _prepare_array_delta — ID 空间统一

`_prepare_array_delta(base, delta, is_override) -> ArrayFieldDiff`

将 delta 的 indices 和 order 统一映射到全状态的 element ID 空间，统一处理 override 和非 override 两种场景：

| 场景 | 映射方式 |
|------|---------|
| override | flat position → element ID（通过 base.order 展开），ADDED 元素分配 `max(base.indices)+1` 起的新 ID |
| 非 override | ADDED 元素 ID 重分配（从 `max(base.indices)+1` 起），避免跨 Mod ID 冲突 |

映射同时应用到 `delta.indices` 和 `delta.order`，使下游主循环和 order 重建无需区分 override。

### _rebuild_array_order — 元素顺序重建

`_rebuild_array_order(base, delta) -> list[int]`

基于 `delta.order` 重建 `base` 的元素顺序，override 和非 override 统一处理：

1. **识别 orphan**：`set(base.indices) - set(delta.order)` — 在 base 中存在但不在 delta.order 中的元素（前 mod 新增的、本次 delta 不涉及的元素）
2. **记录锚点**：遍历 base.order，每个 orphan 记录跟在哪个非 orphan 元素之后
3. **按 delta.order 重建**：遍历 delta.order，依次 append 每个元素，在每个元素后插入其对应的 orphan

override 时 `_prepare_array_delta` 已将 delta.order 映射为完整的 element ID 排序，orphan 为空集，算法退化为直接使用 delta.order。

DELETED 元素保留在 order 中（`to_list()` 和 formatter 都已跳过 DELETED），无需从 order 中移除。

---

## 字段合并策略分发

`_resolve_merge_strategy()` 根据 [Schema 规则](03-schema-system.md) 决定处理方式：

| 策略 | 说明 |
|------|------|
| `replace` | 直接用新值替换旧值 |
| `merge` | 递归合并 dict 字段 |
| `coerce` | 类型兼容转换后替换 |
| `append` | 数组追加新元素 |
| `smart_match` | 按 match key 匹配数组元素后逐元素合并 |

类型不匹配时通过 `_build_warn_msg()` 生成警告（读取 [MergeContext](07-diagnostics.md) 上下文信息）并记录到 `diag`。

---

## merge_all_files — 批量合并（merger.py）

遍历所有文件，通过 `MergeCache` 获取合并结果，输出到 `merged_output/`。

特殊处理：
- `WHOLE_FILE_REPLACE` 集合（如 `sfx_config.json`）跳过字段级合并
- `tag.json` 验证 name 字段一致性

---

## merge_file — 单文件合并（merger.py）

单个文件的完整合并流程：

```
base_data → DictFieldDiff.from_dict() → 全状态树
    |
    v
for each mod (按优先级):
    设置 MergeContext 上下文
    apply_dict_delta(current, delta, version=step)
    apply_dict_delta(current, override_delta)  // 若有用户覆写
    |
    v
current.to_dict() → 合并后 JSON
```

---

## MergeCache 结果缓存（cache.py）

按文件缓存合并结果，统一 diff_dialog 和 merger 的计算逻辑。

- `need_steps=True`：同时产出 `StepState`（逐 Mod 中间状态：左右对齐文本 + 每行 ChangeKind），供 [DiffDialog](09-gui.md) 可视化
- `need_steps=False`：跳过格式化
- 缓存在 override 变更时按文件失效

---

## format_delta_json — Diff 格式化（formatter.py）

### 核心格式化入口

```python
left_lines, right_lines, left_kinds, right_kinds = format_delta_json(
    delta, highlight_version=mod_version,
)
```

将全状态 DiffDict 序列化为**预对齐**的左右文本 + 每行高亮类型。根据 `highlight_version` 过滤：只有当前版本的字段参与高亮，其他字段视为 ORIGIN。

输出保证 `len(left_lines) == len(right_lines)`，已插入空行对齐。

### 行级 diff 工具

| 函数 | 说明 |
|------|------|
| `diff_opcodes(a, b)` | 行哈希 + rapidfuzz C++ 后端 diff（高性能） |
| `build_padded_texts(...)` | 根据 opcodes 在行数少的一侧插入空行对齐 |
