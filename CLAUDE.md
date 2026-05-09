# CLAUDE.md

## 项目概述

「苏丹的游戏」Mod合并器。多Mod改同一JSON无法共存——本体为基础，按优先级逐层JSON深度合并，生成合成Mod部署Workshop。

## 运行

```bash
pip install PySide6        
python -m src.main         # 或双击 run.bat
```

无测试框架/lint/构建。

## 架构

数据流：`mod/scanner`扫描→`merge/delta`差异→`merge/merger`深度合并→`mod/deployer`部署。

GUI(PySide6)`src/gui/app.py``MainWindow`串联逻辑，`MergeWorker`(QThread)异步合并。

### 目录

```text
src/
├── main.py                      # 入口
├── config.py                    # 路径/配置
├── accel/                       # C扩展加速JSON
├── core/
│   ├── infra/                   # 基础设施(类型/诊断/性能)
│   │   ├── types.py             # MergeMode,ChangeKind,DiffDict,FieldDiff,DupList
│   │   ├── diagnostics.py       # Diagnostics单例,MergeContext
│   │   └── profiler.py          # @profile,profile_block
│   ├── json/                    # JSON处理
│   │   ├── parser.py            # 清洗/格式化/序列化
│   │   ├── store.py             # JsonStore单例(加载/缓存/override)
│   │   └── classify.py          # get_type_str()(classify_json已下沉C++)
│   ├── schema/                  # Schema规则
│   │   ├── loader.py            # load_schemas,resolve_schema,get_field_def
│   │   ├── generator.py         # 从本体config自动生成schema
│   │   └── dsl.py               # 游戏DSL key模式识别
│   ├── merge/                   # 合并引擎
│   │   ├── delta.py             # ModDelta缓存,compute_delta递归diff
│   │   ├── merger.py            # apply_delta,deep_merge,merge_all_files
│   │   ├── array_match.py       # 数组匹配(四种策略)
│   │   ├── cache.py             # MergeCache逐mod合并可视化
│   │   ├── formatter.py         # format_delta_json行级差异
│   │   └── rules.py             # SMART模式删除判定
│   ├── mod/                     # Mod管理
│   │   ├── scanner.py           # ModInfo,scan_all_mods
│   │   ├── conflict.py          # 冲突检测,FileOverrideInfo
│   │   ├── overlap.py           # 本体重叠检测
│   │   ├── deployer.py          # deploy_to_workshop
│   │   ├── id_remap.py          # ID冲突检测与重分配
│   │   └── overrides.py         # override目录管理
│   └── platform/                # 平台集成
│       ├── steam.py             # Steam ACF/VDF解析
│       ├── history.py           # 历史版本目录解析
│       └── updater.py           # 更新检查
└── gui/
    ├── app.py                   # MainWindow
    ├── workers.py               # 后台线程
    ├── widgets/
    │   └── code_editor.py       # CodeEditor+JsonEditorDialog
    ├── panels/
    │   ├── mod_list.py          # 拖拽排序Mod列表
    │   ├── mod_detail.py        # Mod详情
    │   ├── override.py          # 覆盖链
    │   └── log.py               # 诊断日志
    └── dialogs/
        ├── diff.py              # 行级差异对比
        ├── json_fix.py          # JSON修复
        ├── deletion.py          # 删减报告
        ├── setup_wizard.py      # 首次配置
        └── manual.py            # 使用教程
```

### 依赖

```text
infra(零依赖)→json→schema→merge
                                ↑
                       mod────┘
platform(仅依赖infra)
```

### 合并策略(merge/merger.py)

`classify_json`三分类决定合并方式：

- **dictionary**(顶层key为ID字典,如cards.json)：按key深度合并
- **entity**(含id字段单实体,如rite/*.json)：整体深度合并，`settlement`/`settlement_prior`/`settlement_extre`用智能匹配
- **config**：整体深度合并

数组智能匹配两策略(自动识别)：

- **Rite风格**：guid→槽位引用(s*.is)→condition全文→result_title+result_text，四级
- **Event风格**：action关键指令(rite/event_on/prompt.id等)→action全文序列化，两级

`WHOLE_FILE_REPLACE`中文件(目前仅sfx_config.json)跳过合并，用最后Mod版本。

### json/parser.py

游戏JSON非标准：含`//`注释和尾随逗号。`load_json`自动清理后解析。BOM等异常记录`parse_warnings`供GUI展示。

### 诊断

禁Python`logging`。诊断统一`src/core/infra/diagnostics.py``diag`单例收集，GUI读取。

## 业务规则

1. Mod与本体同名JSON，Mod覆盖本体
2. 不同Mod含同名JSON互相冲突——本工具解决
3. 错误/警告用已有error_log面板(`_log_error`/`_show_errors`)，不另设机制
4. Mod优先级：列表越靠下越高，同字段最后Mod为准
5. 合成Mod ID固定`0000000001`，部署Workshop
6. `tag.json`覆盖需验证name一致性，不一致发警告
7. 错误暴露。不正确调用/不符预期用报错代替兜底
8. 禁Python`logging`。诊断统一`diag`单例

## 路径

- 本体配置：`{game_path}/Sultan's Game_Data/StreamingAssets/config/`
- Mod配置：各Mod`config/`子目录，结构与本体对应
- 合并输出：`merged_output/`
- 用户配置：`user_config.json`(运行时生成,gitignore)

## 编码规范

【类型】全函数完整注解。Python 3.10 typing(list[str],dict[str,int])。禁Any。

【质量】通过mypy。通过ruff。遵循PEP 8。

【LSP】代码导航优先LSP不用Grep。`character`对准符号列不能用1或行首。先看行内容数出符号起始列(1-based)。

【行为】代码不符规范自动修复后输出。不解释，只输出最终正确代码。

【行为】遇到错误，严格按照这个步骤进行：（1）.不要分析， 先写测例复现错误（2）.复现错误之后再进行分析修复。(3).基于测例验证。