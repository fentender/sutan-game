# 模块文档总览

## 项目简介

「苏丹的游戏」Mod 合并管理器，解决多个 Mod 修改同一 JSON 文件时无法同时启用的问题。以游戏本体文件为基准，按用户指定的优先级逐层执行 JSON 内容级深度合并，最终生成合成 Mod 部署到 Workshop 目录。

## 端到端数据流

```
Mod 扫描（scanner）
    |
    v
JsonStore 批量加载（parser 清洗 + classify 分类 + store 缓存）
    |
    v
Schema 规则加载（loader 匹配 + generator 生成）
    |
    v
ModDelta 差异计算（delta 递归 diff + array_match 数组匹配 + rules 删除过滤）
    |
    v
合并引擎（merger 应用 delta + cache 缓存结果 + formatter 格式化）
    |
    v
部署（deployer 生成合成 Mod → Workshop 目录）
```

合并操作通过 GUI 的 `MergeWorker`（QThread）异步执行，避免阻塞主线程。

## 模块依赖图

```
01 数据类型（零依赖）
  |
  +---> 02 JSON 处理管线（依赖 01）
  |       |
  |       +---> 03 Schema 规则体系（依赖 01, 02）
  |               |
  |               +---> 04 差异计算引擎（依赖 01, 02, 03）
  |                       |
  |                       +---> 05 合并引擎（依赖 01, 02, 03, 04）
  |
  +---> 07 诊断与性能（零依赖，横切关注点）
  |
  +---> 08 平台集成（依赖 01）

06 Mod 管理（依赖 01, 02, 04, 05, 08）

09 GUI 界面（依赖所有 core 模块 + config）
```

## 模块索引

| # | 模块 | 涉及源文件 | 说明 |
|---|------|-----------|------|
| [01](01-data-types.md) | 数据类型 | `infra/types.py` | MergeMode、ChangeKind、DiffDict 等核心类型定义 |
| [02](02-json-pipeline.md) | JSON 处理管线 | `json/parser.py` `store.py` `classify.py` `accel/` | 非标准 JSON 清洗、解析、分类、全局缓存 |
| [03](03-schema-system.md) | Schema 规则体系 | `schema/loader.py` `generator.py` `dsl.py` | 合并规则的定义、查询、自动生成 |
| [04](04-diff-engine.md) | 差异计算引擎 | `merge/delta.py` `array_match.py` `rules.py` | 递归 diff、数组匹配、SMART 删除过滤 |
| [05](05-merge-engine.md) | 合并引擎 | `merge/merger.py` `cache.py` `formatter.py` | 核心合并算法、结果缓存、格式化输出 |
| [06](06-mod-management.md) | Mod 管理 | `mod/scanner.py` `conflict.py` `overlap.py` `deployer.py` `id_remap.py` `overrides.py` | Mod 扫描、冲突检测、部署、ID 重分配 |
| [07](07-diagnostics.md) | 诊断与性能 | `infra/diagnostics.py` `profiler.py` | 全局诊断收集、MergeContext、性能监测 |
| [08](08-platform.md) | 平台集成 | `platform/steam.py` `history.py` `updater.py` | Steam 数据读取、历史版本管理、更新检查 |
| [09](09-gui.md) | GUI 界面 | `gui/` 全部文件 | PySide6 主窗口、面板、对话框、工作线程 |

## 关键设计模式

### 单例模式

四个全局单例管理共享状态：

| 单例 | 模块 | 职责 |
|------|------|------|
| `JsonStore` | [02 JSON 处理](02-json-pipeline.md) | 全局 JSON 数据缓存 |
| `ModDelta` | [04 差异计算](04-diff-engine.md) | 全局 Delta 缓存 |
| `MergeCache` | [05 合并引擎](05-merge-engine.md) | 合并结果缓存 |
| `Diagnostics(diag)` | [07 诊断](07-diagnostics.md) | 诊断信息收集器 |

### 策略模式

合并行为由多层策略选择决定：

- **MergeMode 枚举**（[01 数据类型](01-data-types.md)）：NORMAL / SMART / REPLACE / ADAPTIVE
- **字段级合并策略**（[03 Schema](03-schema-system.md)）：merge / replace / coerce / append / smart_match
- **数组匹配策略**（[04 差异计算](04-diff-engine.md)）：by_keys / by_index / by_consume / by_heuristic
- **删除判定规则**（[04 差异计算](04-diff-engine.md)）：SMART 模式上下文感知删除控制

### 缓存体系

三级缓存加速重复访问：

1. **JsonStore 文件缓存**：`(路径, mtime)` 为 key，文件未修改时命中
2. **ModDelta 差异缓存**：`(mod_id, rel_path)` 为 key，启动时一次性预计算
3. **MergeCache 合并缓存**：按文件缓存最终合并结果，override 变更时按文件失效

### 数据流转类型

核心数据在模块间以强类型结构流转：

```
原始 JSON (dict)
    |  JsonStore.init / parse_file
    v
DiffDict（全状态注解树，from_dict 产出）
    |  compute_delta
    v
DiffDict（稀疏 delta，仅含变化字段）
    |  apply_delta
    v
DiffDict（累积全状态树，带 ChangeKind + version 标注）
    |  to_dict
    v
合并后 JSON (dict) → dump_json → 文件输出
```

## 核心业务规则

| 规则 | 说明 |
|------|------|
| Mod 优先级 | 列表中越靠下优先级越高，同一字段以最后一个 Mod 为准 |
| 冲突判定 | 多个 Mod 包含同名 JSON 文件即冲突，无法同时开启（除非合并） |
| 合成 Mod ID | 固定为 `0000000001`，部署到 Workshop 目录 |
| dictionary 顶层 key | 永不删除（即使 Mod 中不存在该 key） |
| WHOLE_FILE_REPLACE | `sfx_config.json` 跳过合并，直接用最后一个 Mod 版本 |
| tag.json 验证 | 覆盖时需验证 name 字段一致性 |
| 诊断信息 | 禁止 Python `logging`，统一通过 `diag` 单例收集 |

## 阅读指引

- **初次了解项目**：README → 01 → 02 → 03 → 04 → 05 → 06
- **参与合并逻辑开发**：04 → 05 → 03 → 01
- **参与 Mod 管理开发**：06 → 04 → 02
- **参与 GUI 开发**：09 → 05 → 06
