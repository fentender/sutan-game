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

## apply_delta — 核心合并算法（merger.py）

`apply_delta(base, delta, schema, field_path, version, is_override)` 原地修改 DiffDict。

对 delta 中每个条目按类型分发：

| delta 条目类型 | 处理方式 |
|----------------|---------|
| `FieldDiff` | DELETED/ADDED/CHANGED 直接替换，标记 MULTI_MOD 或 OVERRIDE |
| `DiffDict` | 递归调用 `apply_delta` |
| `ArrayFieldDiff` | 调用 `apply_array_delta` |

### 冲突标记规则

- 字段被多个 Mod 修改 -> 标记 `MULTI_MOD`（`kind |= 4`）
- 用户手动覆写 -> 标记 `OVERRIDE`（`kind |= 8`，不触发 MULTI_MOD）

### 字段合并策略分发

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

## apply_array_delta — 数组级合并（merger.py）

处理 ArrayFieldDiff 的应用：

- CHANGED：递归 apply 到匹配的元素
- ADDED：ID 重分配后追加
- DELETED：标记删除
- order 重建：保持 Mod 元素顺序 + 保留前 Mod 新增元素位置

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
base_data → DiffDict.from_dict() → 全状态树
    |
    v
for each mod (按优先级):
    设置 MergeContext 上下文
    apply_delta(current, delta, version=step)
    apply_delta(current, override_delta)  // 若有用户覆写
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
