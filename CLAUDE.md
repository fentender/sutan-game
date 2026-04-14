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

数据流：`mod_scanner` 扫描 → `conflict` 分析覆盖链 → `merger` 深度合并 → `deployer` 部署合成 Mod。

GUI（PySide6）在 `src/gui/app.py` 的 `MainWindow` 中串联所有逻辑，合并操作通过 `MergeWorker`(QThread) 异步执行。

### 核心合并策略（merger.py）

三种文件分类（`classify_json`）决定合并方式：
- **dictionary**（顶层 key 是 ID 的字典，如 cards.json）：按 key 深度合并
- **entity**（含 id 字段的单实体，如 rite/*.json）：整体深度合并，数组字段 `settlement`/`settlement_prior`/`settlement_extre` 使用智能匹配
- **config**：整体深度合并

数组智能匹配分两套策略（自动识别）：
- **Rite 风格**：guid → 槽位引用(s*.is) → condition 全文 → result_title+result_text，四级匹配
- **Event 风格**：action 关键指令(rite/event_on/prompt.id 等) → action 全文序列化，两级匹配

`WHOLE_FILE_REPLACE` 集合中的文件（目前仅 sfx_config.json）跳过合并，直接用最后一个 Mod 的版本。

### json_parser.py

游戏 JSON 不是标准 JSON：含 `//` 注释和尾随逗号。`load_json` 自动清理这些格式再解析。BOM 头等异常格式会记录到 `parse_warnings` 供 GUI 展示。

### 全局状态收集器

各模块使用模块级列表收集警告/错误，GUI 读取后展示：
- `json_parser.parse_warnings`
- `mod_scanner.scan_errors`
- `merger.merge_warnings`

## 关键业务规则

1. Mod 和游戏本体同名的 JSON 文件，Mod 会覆盖本体
2. 不同 Mod 如果包含同名 JSON 文件，两者互相冲突无法同时开启（这正是本工具要解决的问题）
3. 遇到需要向用户报告的错误或警告时，使用已有的 error_log 面板（通过 `_log_error` 或 `_show_errors` 方法），不要另外设计新的报告机制
4. Mod 优先级：列表中越靠下优先级越高，同一字段以最后一个 Mod 为准
5. 合成 Mod 的 ID 固定为 `0000000001`，部署到 Workshop 目录下
6. `tag.json` 覆盖时需验证 name 字段一致性，不一致发出警告
7. 设计代码要把错误暴露出来。像是不正确的调用、不符预期的结果应该用报错代替兜底。
8. 禁止使用 Python `logging` 模块。所有诊断信息（警告、错误、调试信息）统一通过 `src/core/diagnostics.py` 的 `diag` 单例收集，GUI 统一读取展示。

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
- 必须通过 mypy 检查（无 error）
- 必须通过 ruff 检查（无 error）
- 遵循 PEP 8

【行为约束】
- 如果代码不符合规范，你必须自动修复后再输出
- 不要解释，只输出最终正确代码