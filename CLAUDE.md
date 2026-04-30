# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

「苏丹的游戏」Mod 合并管理器。解决多个 Mod 修改同一 JSON 文件时无法同时启用的问题——以游戏本体文件为基础，按优先级逐层做 JSON 内容级深度合并，生成合成 Mod 部署到 Workshop 目录。

## 运行

```bash
pip install PySide6        
python -m src.main         # 或双击 run.bat
```

无测试框架、无 lint 配置、无构建步骤。

## 架构

数据流：`mod/scanner` 扫描 → `merge/delta` 差异计算 → `merge/merger` 深度合并 → `mod/deployer` 部署合成 Mod。

GUI（PySide6）在 `src/gui/app.py` 的 `MainWindow` 中串联所有逻辑，合并操作通过 `MergeWorker`(QThread) 异步执行。

### 目录结构

```text
src/
├── main.py                      # 应用入口
├── config.py                    # 路径常量、用户配置
├── accel/                       # C 扩展加速 JSON 解析
├── core/
│   ├── infra/                   # 基础设施层（类型、诊断、性能监测）
│   │   ├── types.py             # MergeMode, ChangeKind, DiffDict, FieldDiff, DupList 等
│   │   ├── diagnostics.py       # Diagnostics 单例, MergeContext
│   │   └── profiler.py          # @profile, profile_block
│   ├── json/                    # JSON 处理层
│   │   ├── parser.py            # 清洗/格式化/序列化
│   │   ├── store.py             # JsonStore 单例（加载、缓存、override 管理）
│   │   └── classify.py          # classify_json()（dictionary/entity/config）
│   ├── schema/                  # Schema 规则层
│   │   ├── loader.py            # load_schemas, resolve_schema, get_field_def
│   │   ├── generator.py         # 从本体 config 自动生成 schema
│   │   └── dsl.py               # 游戏 DSL key 模式识别
│   ├── merge/                   # 合并引擎层
│   │   ├── delta.py             # ModDelta 缓存, compute_delta 递归 diff
│   │   ├── merger.py            # apply_delta, deep_merge, merge_all_files
│   │   ├── array_match.py       # 数组匹配策略（四种）
│   │   ├── cache.py             # MergeCache 逐 mod 合并可视化数据
│   │   ├── formatter.py         # format_delta_json 行级差异文本
│   │   └── rules.py             # SMART 模式删除判定
│   ├── mod/                     # Mod 管理层
│   │   ├── scanner.py           # ModInfo, scan_all_mods
│   │   ├── conflict.py          # 冲突检测, FileOverrideInfo
│   │   ├── overlap.py           # 本体重叠检测
│   │   ├── deployer.py          # deploy_to_workshop
│   │   ├── id_remap.py          # ID 冲突检测与重分配
│   │   └── overrides.py         # override 目录管理
│   └── platform/                # 平台集成层
│       ├── steam.py             # Steam ACF/VDF 解析
│       ├── history.py           # 历史版本目录解析
│       └── updater.py           # 更新检查
└── gui/
    ├── app.py                   # MainWindow 主窗口
    ├── workers.py               # 后台工作线程
    ├── widgets/                 # 可复用 UI 组件
    │   └── code_editor.py       # CodeEditor + JsonEditorDialog
    ├── panels/                  # 主窗口面板
    │   ├── mod_list.py          # 拖拽排序 Mod 列表
    │   ├── mod_detail.py        # Mod 详情
    │   ├── override.py          # 覆盖链展示
    │   └── log.py               # 诊断日志
    └── dialogs/                 # 弹出对话框
        ├── diff.py              # 行级差异对比
        ├── json_fix.py          # JSON 修复
        ├── deletion.py          # 删减报告
        ├── setup_wizard.py      # 首次配置
        └── manual.py            # 使用教程
```

### 层级依赖

```text
infra（零依赖）→ json → schema → merge
                                    ↑
                           mod ────┘
platform（仅依赖 infra）
```

### 核心合并策略（merge/merger.py）

三种文件分类（`classify_json`）决定合并方式：
- **dictionary**（顶层 key 是 ID 的字典，如 cards.json）：按 key 深度合并
- **entity**（含 id 字段的单实体，如 rite/*.json）：整体深度合并，数组字段 `settlement`/`settlement_prior`/`settlement_extre` 使用智能匹配
- **config**：整体深度合并

数组智能匹配分两套策略（自动识别）：
- **Rite 风格**：guid → 槽位引用(s*.is) → condition 全文 → result_title+result_text，四级匹配
- **Event 风格**：action 关键指令(rite/event_on/prompt.id 等) → action 全文序列化，两级匹配

`WHOLE_FILE_REPLACE` 集合中的文件（目前仅 sfx_config.json）跳过合并，直接用最后一个 Mod 的版本。

### json/parser.py

游戏 JSON 不是标准 JSON：含 `//` 注释和尾随逗号。`load_json` 自动清理这些格式再解析。BOM 头等异常格式会记录到 `parse_warnings` 供 GUI 展示。

### 诊断信息

禁止使用 Python `logging` 模块。所有诊断信息（警告、错误、调试信息）统一通过 `src/core/infra/diagnostics.py` 的 `diag` 单例收集，GUI 统一读取展示。

## 关键业务规则

1. Mod 和游戏本体同名的 JSON 文件，Mod 会覆盖本体
2. 不同 Mod 如果包含同名 JSON 文件，两者互相冲突无法同时开启（这正是本工具要解决的问题）
3. 遇到需要向用户报告的错误或警告时，使用已有的 error_log 面板（通过 `_log_error` 或 `_show_errors` 方法），不要另外设计新的报告机制
4. Mod 优先级：列表中越靠下优先级越高，同一字段以最后一个 Mod 为准
5. 合成 Mod 的 ID 固定为 `0000000001`，部署到 Workshop 目录下
6. `tag.json` 覆盖时需验证 name 字段一致性，不一致发出警告
7. 设计代码要把错误暴露出来。像是不正确的调用、不符预期的结果应该用报错代替兜底。
8. 禁止使用 Python `logging` 模块。所有诊断信息（警告、错误、调试信息）统一通过 `src/core/infra/diagnostics.py` 的 `diag` 单例收集，GUI 统一读取展示。

## 路径约定

- 游戏本体配置：`{game_path}/Sultan's Game_Data/StreamingAssets/config/`
- Mod 配置文件在各 Mod 的 `config/` 子目录下，目录结构与游戏本体 config 对应
- 合并输出：项目根目录下 `merged_output/`
- 用户配置：项目根目录下 `user_config.json`（运行时生成，已 gitignore）

## 编码规范

【类型系统】

- 所有函数必须有完整类型注解
- 使用 Python 3.10 typing（list[str], dict[str, int]）
- 禁止 Any

【代码质量】

- 必须通过 mypy 检查
- 必须通过 ruff 检查
- 遵循 PEP 8

【行为约束】

- 如果代码不符合规范，你必须自动修复后再输出
- 不要解释，只输出最终正确代码
