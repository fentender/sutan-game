# TODO — 苏丹的游戏 Mod 合并管理器

> 基于 plans1.md 整理，按依赖关系分阶段排列。
> 阶段 0 最高优先级（用户明确要求）。阶段 2/3/4 可并行。阶段 5 等所有功能稳定后再做。

---

## 阶段 0：dynamic_key 调查与简化（最高优先级）

### 0.1 打印 dynamic_key 频数报告 [plans1 #16]

在 `generate_schemas.py` 的 `build_field_def` 中，当 object 被判定为 `dynamic_keys=true` 时，
打印所有子 key 及出现次数（按频次降序）。

两类 dynamic_key 需分别观察：
- **KNOWN_DYNAMIC_FIELDS**（condition/result/action/effect/tag/cards_slot/no_show/choose）：
  key 是 DSL 表达式（如 `counter.7000617=`、`s1.is`）或属性名（如 `体魄`），value 是基本类型
- **阈值触发**（child_keys > 30）：
  文件级，如 ui.json（`GAME_TITLE`→`{zhCN:..}`）、variable.json 等

目的：确认是否有被误判为 dynamic 的固定结构字段，决定后续简化方案。

### 0.2 简化 dynamic_key 处理逻辑 [plans1 #14]

**当前调查结论**：dynamic_key 概念仍需保留，但处理逻辑可大幅简化：
- DSL 字段的 value 都是基本类型 → 合并策略统一 **replace**，不需要 `dynamic_value` 定义
- 文件级 dynamic_key → 本质是字典，复用 dictionary 按 key merge 逻辑

具体改动：
1. `schema_loader.py:get_field_def` → 遇到 `dynamic_keys` 子字段，返回 `None`（默认 replace）
2. `generate_schemas.py` → 不再生成 `dynamic_value` 节点
3. `merger.py` → 删除 `merge_by_type` 相关逻辑

> ⚠ 此方案需 0.1 的频数数据验证后再确认

---

## 阶段 1：架构基础

### 1.1 全局状态统一管理 [plans1 #1]

`parse_warnings`、`scan_errors`、`merge_warnings` 是三个裸模块级列表，
线程不安全（`merge_warnings` 在工作线程 append，主线程 snapshot/clear）。

新建 `src/core/diagnostics.py`，提供线程安全的单例日志收集器：
- `warn(msg)` / `error(msg)` — 线程安全写入
- `snapshot()` — 返回当前快照并清空
- 各模块注入此实例，不再维护自己的列表

同时解决探索中发现的 `app.py:52` 线程竞态问题。

**涉及文件**：`json_parser.py`、`mod_scanner.py`、`merger.py`、`app.py`

### 1.2 共享类型工具函数 [plans1 #15]

`generate_schemas.py` 的 `get_type_str` 与 `schema_loader.py` 的 `_get_actual_type` 功能相同。

提取到 `src/core/type_utils.py`，两处统一引用。

### 1.3 移除过度防御代码 [plans1 #3]

让错误暴露，不做静默兜底：
- `conflict.py` `analyze_all_overrides`：`base_file.exists()` 为 False 时应报警告而非用空 dict 兜底
- `generate_schemas.py:96-97` 非 dict 时无声 return → 改为 raise
- `generate_schemas.py:240-241, 274` 子路径缺失无声 continue → 改为报错

---

## ~~阶段 2：generate_schemas 集成与修复（依赖阶段 1.2）~~ ✅ 已完成

### ~~2.1 迁移到项目主体 [plans1 #7]~~ ✅

将 `scripts/generate_schemas.py` 迁移到 `src/core/schema_generator.py`。
print 替换为 `diag.info/warn`，`generate_all` 增加 `progress_callback` 支持。
`main.py` 启动时通过 QProgressDialog 自动检查并生成 schema。
`scripts/generate_schemas.py` 已删除。

### ~~2.2 修复 array\<float\> 重复 bug [plans1 #12]~~ ✅

提取 `_collapse_int_float` 辅助函数，`infer_type` 和 `_infer_type_from_counts` 统一使用。
用显式 discard/add 替代集合推导。

### ~~2.3 多类型数组应记录所有类型 [plans1 #9]~~ ✅

`analyze_value_type` 多类型分支返回 `"array<int,string>"` 形式。
`schema_loader.py:_type_compatible` 适配多类型数组兼容性判断。

### ~~2.4 移除 max_depth 限制 [plans1 #10]~~ ✅

`collect_field_info` 改用 `_visited` 集合（基于 `id(obj)`）防无限递归。

### ~~2.5 移除采样数限制 [plans1 #11]~~ ✅

移除 `sample_values` 3 个限制和 dictionary 100 条采样限制。

### ~~2.6 加强列表类型检查 [plans1 #13]~~ ✅

新增 `_validate_type_combination`，检测标量+对象+数组异常组合并通过 `diag.warn` 报告。

### 附加：diagnostics 增加日志级别

`diagnostics.py` 增加 `info/error` 方法，消息存储为 `(level, msg)` 元组。
`app.py` 适配 snapshot 返回值变化。GUI 面板级别筛选留待后续阶段。

---

## 阶段 3：核心合并逻辑改进（依赖阶段 1）

### 3.1 conflict.py：增加删除检测 [plans1 #4]

`_collect_field_diffs`（第 58-60 行）未处理 `key in base and key not in mod_data` 的情况。
应添加删除检测分支。是否执行删除是上层决策，diff 层只负责如实报告。

### 3.2 conflict.py：路径分隔符冲突 [plans1 #5]

当前用 `"."` 连接 field_path（第 57 行），但游戏 JSON key 含 `"."`
（如 `counter.7000617=`、`s1.is`）。

改用 `generate_schemas.py` 已有的 `SEP = '\x01'`。GUI 展示时替换为可读分隔符。

### 3.3 conflict.py：数组比较原子化 [plans1 #6]

当前数组差异只记录 `"[N项]"`（第 64-68 行），丢失所有细节。

改进：
- **基本类型数组**：逐元素比较，报告增删改
- **dict 数组**：按 `guid`/`id` 匹配 → 递归 dict 差异比较；无标识字段 → 按索引对应
- **array of array**：直接报错
- **类型不匹配**（dict↔scalar、dict↔array）：报错
- `FieldOverride.base_value` 最终比较单位应为基本类型

### 3.4 merger.py：处理删除情况 [plans1 #17]

三处缺失：
- `merger.py:470` — `allow_deletions` 仅在 dictionary 分支生效，应提升为全局开关
- `merger.py:481-504`（`_object_array_delta`）— 数组中删除的元素未标记
- `merger.py:509-518`（`_recursive_delta`）— dict 中删除的 key 未标记

所有合并路径（dictionary/entity/config）均需支持 `allow_deletions`。

---

## 阶段 4：GUI 改进

### 4.1 diff_dialog 可编辑与持久化 [plans1 #19]

当前 diff_dialog 左右 QTextEdit 均为只读（`diff_dialog.py:186-193`）。

改进：
1. 右侧 QTextEdit 去掉 `setReadOnly`
2. 持久化到 `mod_overrides/{mod_id}/{rel_path}.json`
3. 打开 diff_dialog 时优先加载已保存内容
4. 增加「重置为默认」按钮
5. QTextEdit 原生支持 Ctrl+Z，无需额外实现

**涉及文件**：`diff_dialog.py`、`config.py`（新增 overrides 路径）

### 4.2 MergeWorker 安全退出

`app.py:423-428` 用 `terminate()` 硬杀线程不安全。
改用协作式取消：在 `MergeWorker` 中检查取消标志，`closeEvent` 设标志后 `wait()`。

---

## 阶段 5：类设计重构（依赖阶段 1-4）

### 5.1 Mod 全局管理类 [plans1 #2]

新建 `src/core/mod_manager.py`，管理 Mod 的加载/排序/启用状态/覆盖持久化。
GUI 只做展示，不直接操作 Mod 数据。

### 5.2 MergeEngine 类 [plans1 #18]

将 `merger.py` 中散落的函数封装为 `MergeEngine` 类，状态内聚。

### 5.3 SchemaManager 类 [plans1 #18]

合并 `schema_loader.py` 和 `schema_generator.py`（阶段 2.1 迁移后的模块），
统一管理 schema 的生成、加载、查询。

---

## 依赖关系

```
阶段 0（dynamic_key 调查）── 独立，最高优先级
    │
    ▼
阶段 1（架构基础）── 为后续阶段提供基础设施
    │
 ┌──┼──┐
 ▼  ▼  ▼
 2  3  4  ── 三者可并行
 │  │  │
 └──┼──┘
    ▼
阶段 5（类重构）── 所有功能稳定后做
```

## plans1 索引

| # | 阶段 | 描述 | 备注 |
|:-:|:----:|------|------|
| 1 | 1.1 | 全局状态统一管理 | |
| 2 | 5.1 | Mod 全局管理类 | |
| 3 | 1.3 | 移除过度防御代码 | |
| 4 | 3.1 | conflict 删除检测 | |
| 5 | 3.2 | 路径分隔符冲突 | |
| 6 | 3.3 | 数组比较原子化 | |
| 7 | 2.1 | generate_schemas 集成 | |
| 8 | — | object vs dict | **非问题，已关闭** |
| 9 | 2.3 | 多类型数组记录 | |
| 10 | 2.4 | 移除 max_depth | |
| 11 | 2.5 | 移除采样数限制 | |
| 12 | 2.2 | array\<float\> 重复 bug | |
| 13 | 2.6 | 列表类型检查 | |
| 14 | 0.2 | dynamic_key 简化 | |
| 15 | 1.2 | 共享类型工具函数 | |
| 16 | 0.1 | 打印 dynamic_key 频数 | |
| 17 | 3.4 | merger 处理删除 | |
| 18 | 5.2/5.3 | 类设计重构 | |
| 19 | 4.1 | diff_dialog 可编辑 | |
| 新 | 4.2 | MergeWorker 安全退出 | 探索中发现 |
