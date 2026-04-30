# core 模块概览

`src/core/` 包含合并管理器的全部核心业务逻辑，按职责划分为六个子模块。

## 子模块一览

| 子模块     | 职责                         | 文件                                                               |
| ---------- | ---------------------------- | ------------------------------------------------------------------ |
| `infra`    | 基础设施（类型、诊断、性能） | `types.py`, `diagnostics.py`, `profiler.py`                        |
| `json`     | JSON 解析、缓存、分类        | `parser.py`, `store.py`, `classify.py`                             |
| `schema`   | Schema 规则加载与生成        | `loader.py`, `generator.py`, `dsl.py`                              |
| `merge`    | 合并引擎                     | `delta.py`, `merger.py`, `array_match.py`, `cache.py`, `formatter.py`, `rules.py` |
| `mod`      | Mod 管理                     | `scanner.py`, `conflict.py`, `overlap.py`, `deployer.py`, `id_remap.py`, `overrides.py` |
| `platform` | 平台集成                     | `steam.py`, `history.py`, `updater.py`                             |

## 依赖层级

```
infra（零依赖）
  |
  +---> json（依赖 infra）
  |       |
  |       +---> schema（依赖 infra, json）
  |               |
  |               +---> merge（依赖 infra, json, schema）
  |
  +---> platform（仅依赖 infra，独立辅助层）

mod（依赖 infra, json, merge, schema, platform）
```

- **infra** 是零依赖的最底层，提供类型定义和横切关注点
- **json** 和 **schema** 逐层向上构建数据读取与规则体系
- **merge** 是核心计算层，汇聚前三层的能力
- **mod** 位于最上层，编排完整的 Mod 管理流程
- **platform** 独立于主链，为 mod 层提供 Steam 数据和版本历史

## 关键设计模式

### 单例模式

四个全局单例管理共享状态，避免模块间传递大量参数：

| 单例                | 模块            | 职责                                    |
| ------------------- | --------------- | --------------------------------------- |
| `JsonStore`         | `json/store.py` | 全局 JSON 数据缓存，统一读取入口        |
| `ModDelta`          | `merge/delta.py`| 全局 Delta 缓存，预计算 Mod 差异        |
| `MergeCache`        | `merge/cache.py`| 合并结果缓存，避免重复计算              |
| `Diagnostics(diag)` | `infra/diagnostics.py` | 线程安全的诊断信息收集器         |

### 策略模式

合并行为由多层策略选择决定：

- **MergeMode 枚举**（`types.py`）：NORMAL / SMART / REPLACE / ADAPTIVE 四种全局合并模式
- **字段级合并策略**（schema `__merge__` 字段）：merge / replace / coerce / append / smart_match
- **数组匹配策略**（`array_match.py`）：by_keys / by_index / by_consume / by_heuristic
- **删除判定规则**（`rules.py`）：SMART 模式下的上下文感知删除控制

### 缓存体系

三级缓存加速重复访问：

1. **JsonStore 文件缓存**：`(路径, mtime)` 为 key，文件未修改时直接命中
2. **ModDelta 差异缓存**：`(mod_id, rel_path)` 为 key，启动时一次性预计算
3. **MergeCache 合并缓存**：按文件缓存最终合并结果和中间步骤，override 变更时按文件失效

### 数据流转类型

核心数据在模块间以强类型结构流转：

```
原始 JSON (dict)
    |  JsonStore.parse_file / init
    v
DiffDict / ArrayFieldDiff / FieldDiff（全状态注解树）
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

## 诊断信息机制

禁止使用 Python `logging` 模块。所有诊断信息（警告、错误）统一通过 `diag` 单例收集，按四个类别分组：

| 类别     | 来源模块                  | 内容                               |
| -------- | ------------------------- | ---------------------------------- |
| `parse`  | `json/store.py`           | BOM 修正、编码异常                  |
| `scan`   | `mod/scanner.py`          | Mod 扫描时的 Info.json 解析失败     |
| `merge`  | `merge/merger.py` 等      | 类型不匹配、未知字段、整文件替换警告 |
| `schema` | `schema/loader.py` 等     | schema 加载、生成过程中的信息与警告  |

GUI 通过 `diag.snapshot()` 批量读取并清空消息，展示在日志面板中。
