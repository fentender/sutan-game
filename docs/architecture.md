# 苏丹的游戏 Mod 合并管理器 - 架构文档

## 一、项目概述

解决多个 Mod 修改同一 JSON 文件时的冲突问题。以游戏本体文件为基础，按优先级逐层做 JSON 内容级深度合并，生成合成 Mod 部署到 Workshop 目录。

**技术栈**：Python 3.10+、PySide6（GUI）

**数据流**：Mod 扫描 → 冲突分析 → 深度合并 → 合成 Mod 部署

---

## 二、项目结构

```
SuDanGame/
├── src/
│   ├── main.py                   # 应用入口：路径检测、Schema 生成、启动 GUI
│   ├── config.py                 # 配置管理：路径常量、Steam 路径自动检测、用户偏好持久化
│   ├── core/                     # 核心逻辑层（无 GUI 依赖）
│   │   ├── merger.py            # 【核心】深度合并算法（~900 行）
│   │   ├── conflict.py          # 冲突分析（字段级覆盖链追踪）
│   │   ├── id_remapper.py       # ID 冲突检测与自动重映射（~700 行）
│   │   ├── json_parser.py       # JSON 解析（注释/BOM/尾逗号/缺逗号自动修复）
│   │   ├── mod_scanner.py       # Mod 扫描（遍历 workshop + 本地目录）
│   │   ├── deployer.py          # 部署（生成合成 Mod、资源复制、原子替换）
│   │   ├── schema_loader.py     # Schema 规则加载与字段定义查询
│   │   ├── schema_generator.py  # 从游戏本体自动生成 Schema
│   │   ├── array_match.py       # 数组元素匹配（精确 + 相似度配对）
│   │   ├── type_utils.py        # classify_json / get_type_str
│   │   ├── diagnostics.py       # 线程安全诊断收集器（diag.warn/error/snapshot）
│   │   ├── dsl_patterns.py      # DSL key 模式识别
│   │   ├── profiler.py          # 可选性能评估（@profile 装饰器，禁用时零开销）
│   │   ├── override_utils.py    # 用户 override 目录管理
│   │   └── updater.py           # GitHub/Gitee 版本检查
│   ├── gui/                      # GUI 层（PySide6）
│   │   ├── app.py               # MainWindow — 中央协调器
│   │   ├── workers.py           # QThread 工作线程（MergeWorker/AnalyzeWorker/SchemaWorker/UpdateCheckWorker）
│   │   ├── mod_list.py          # Mod 列表面板（拖拽排序、启用/禁用）
│   │   ├── mod_detail.py        # Mod 详情面板（preview + 描述）
│   │   ├── override_panel.py    # 覆盖详情（树形展示冲突）
│   │   ├── log_panel.py         # 日志面板（错误/警告，双击打开文件）
│   │   ├── diff_dialog.py       # Diff 对比（逐级合并可视化、行级高亮）
│   │   ├── json_editor.py       # JSON 编辑器（行号 + 错误高亮 + 格式化）
│   │   └── json_fix_dialog.py   # 多 Tab JSON 修复弹窗
│   └── lib/                      # 第三方库
├── schemas/                      # 合并规则文件（.schema.json，可自动生成）
├── tests/
│   ├── test_core.py              # 功能测试
│   └── test_perf.py              # 性能测试
├── docs/                         # 项目文档
├── run.bat                       # Windows 启动脚本
├── requirements.txt              # 依赖清单
├── user_config.json              # 用户配置（运行时生成，gitignore）
└── merged_output/                # 合并输出目录（运行时生成，gitignore）
```

---

## 三、核心模块

### 3.1 config.py — 配置管理

管理项目路径和用户偏好的持久化。

**路径常量**：

| 常量 | 说明 |
|------|------|
| `DEFAULT_GAME_PATH` | 游戏本体默认路径 |
| `DEFAULT_WORKSHOP_PATH` | Steam Workshop 默认路径 |
| `DEFAULT_CONFIG_SUBPATH` | 游戏配置子目录 `Sultan's Game_Data/StreamingAssets/config` |
| `SYNTHETIC_MOD_ID` | 合成 Mod 固定 ID `"0000000001"` |
| `SCHEMA_DIR` | Schema 规则文件目录 |

**UserConfig 数据类**：

| 字段 | 说明 |
|------|------|
| `game_path` | 游戏安装路径 |
| `workshop_path` | Steam Workshop 路径 |
| `local_mod_path` | 本地 Mod 路径 |
| `mod_order` | Mod 排序列表（越靠后优先级越高）|
| `enabled_mods` | 启用的 Mod ID 集合 |
| `allow_deletions` | 是否允许删减模式 |
| `enable_profiler` | 性能评估开关 |

- `detect_game_path()` / `detect_workshop_path()` 通过 Steam 注册表和硬编码路径自动检测
- `save()` 使用临时文件 + `os.replace` 原子写入
- `load()` 捕获损坏文件异常，回退默认值

---

### 3.2 json_parser.py — JSON 处理

游戏 JSON 非标准格式，包含 `//` 注释和尾随逗号。

| 函数 | 功能 |
|------|------|
| `strip_js_comments()` | 逐行剥离 `//` 注释，使用字符串状态机避免误删字符串内的 `//` |
| `strip_trailing_commas()` | 正则去除 `}` 或 `]` 前的尾逗号 |
| `fix_missing_commas()` | 字符级解析器，修复对象内相邻 key:value 之间缺失的逗号 |
| `strip_duplicate_commas()` | 压缩连续逗号 `,,` → `,` |
| `clean_json_text()` | 统一清洗流程：注释 → 尾逗号 → 缺逗号 → 连续逗号 |
| `load_json()` | 加载并自动修正 JSON，检测并移除 BOM，带 `(path, mtime)` 缓存 |
| `dump_json()` | 输出标准 JSON（缩进 4）|

---

### 3.3 mod_scanner.py — Mod 扫描

遍历 Workshop 和本地目录，读取 Mod 元数据。

**ModInfo 数据类**：

| 字段 | 说明 |
|------|------|
| `mod_id` | Mod 目录名（通常是数字）|
| `name` | Info.json 中的名称 |
| `path` | Mod 目录绝对路径 |
| `config_files` | `config/` 下的 JSON 文件相对路径列表 |
| `resource_files` | 非 JSON 资源文件列表 |
| `preview_path` | Preview 图片路径 |

| 函数 | 功能 |
|------|------|
| `scan_config_files()` | 扫描 mod 的 `config/` 目录，分离 JSON 和资源文件 |
| `scan_single_mod()` | 扫描单个 mod 目录，读取 `Info.json` |
| `scan_all_mods()` | 扫描整个 workshop 目录（支持排除列表）|

---

### 3.4 conflict.py — 冲突分析

追踪字段级覆盖链，识别冲突。

**FileOverrideInfo** — 单文件覆盖信息：
- `mod_chain`: 参与覆盖的 mod 名称列表
- `field_overrides`: 字段级覆盖详情（`FieldOverride` 列表）
- `has_conflict`: 是否存在冲突（任一字段被多个 mod 修改）

**FieldOverride** — 单字段覆盖信息：
- `field_path`: 字段路径（如 `condition.s1.is`）
- `base_value`: 游戏本体值
- `mod_values`: `[(mod_name, value), ...]`
- `is_conflict`: `len(mod_values) > 1`

| 函数 | 功能 |
|------|------|
| `_collect_field_diffs()` | 递归比较 mod 与本体的字段差异 |
| `analyze_file_overrides()` | 分析单文件的覆盖情况 |
| `analyze_all_overrides()` | 分析所有文件，返回 `FileOverrideInfo` 列表 |

---

### 3.5 schema_loader.py — Schema 系统

加载和查询合并规则。Schema 文件位于 `schemas/` 目录。

**Schema 结构**：

```json
{
  "_meta": {
    "file_type": "dictionary|entity|config",
    "source": "cards.json 或 rite/"
  },
  "_entry|_fields": {
    "fieldName": {
      "type": "int|string|array|object|...",
      "merge": "replace|merge|append|smart_match|coerce",
      "dynamic_keys": true,
      "fields": { ... },
      "element": { ... }
    }
  }
}
```

| 函数 | 功能 |
|------|------|
| `load_schemas()` | 加载所有 `.schema.json` 到全局缓存 |
| `resolve_schema()` | 根据文件路径匹配 schema（精确 + 目录前缀）|
| `get_field_def()` | 在 schema 树中导航，查找字段定义 |
| `get_schema_root_key()` | 根据 `file_type` 返回根级 key（`_entry` 或 `_fields`）|

**Schema 对合并的影响**：

| 影响点 | 机制 |
|--------|------|
| 数组合并策略 | `"merge": "smart_match\|append\|coerce\|replace"` |
| 动态 key | `"dynamic_keys": true` 标记可变 key 层 |
| 按类型不同策略 | `"merge_by_type": {"object": "merge"}` |
| 字段白名单 | 未知字段记录警告 |

---

### 3.6 merger.py — 【核心】深度合并算法

项目最核心的组件（~720 行）。包含文件分类、Delta 计算、深度合并、数组智能匹配四大子系统。

#### 3.6.1 文件分类

```
classify_json(data) → "dictionary" | "entity" | "config"
```

| 类型 | 判断条件 | 合并方式 | 示例 |
|------|--------|--------|------|
| **entity** | 顶层含 `id` 字段 | 整体深度合并 + 智能数组匹配 | `rite/5000001.json` |
| **dictionary** | 所有值都是 dict + 至少一个含 `id` | 按顶层 key 深度合并 | `cards.json`, `tag.json` |
| **config** | 其他 | 整体深度合并 | `ui.json`, `variable.json` |

优先级：entity > dictionary > config

#### 3.6.2 Delta 计算

**目的**：只传递实际修改的部分，避免 mod 中未修改的内容覆盖游戏新增的内容。

```
compute_mod_delta(base_data, mod_data, file_type, allow_deletions)
  ├─ dictionary 文件：按顶层 key 递归 delta
  └─ entity/config：整体递归 delta

_recursive_delta(base, mod)
  ├─ 两边都是 dict → 逐字段递归，只保留变化
  ├─ 两边都是 list → 检测对象数组则元素级 delta，否则原子比较
  └─ 标量 → 值相等返回 None（无变化），否则返回 mod 值

_object_array_delta(base_arr, mod_arr)
  └─ 按 match_key（id/guid 等）建索引，逐元素计算差异
```

**allow_deletions 模式**：mod 中不存在的顶层 key 标记为 `_DELETED`。

#### 3.6.3 深度合并

```
deep_merge(base, override, schema, field_path, game_base)
  ├─ 查询 schema 确定当前字段定义
  ├─ 遍历 override 的每个 key
  │   ├─ _resolve_merge_strategy() → 确定策略
  │   └─ _apply_merge_strategy() → 执行策略
  │       ├─ "merge"        → 递归 deep_merge()
  │       ├─ "replace"      → 直接替换
  │       ├─ "append"       → _append_array()
  │       ├─ "smart_match"  → _merge_settlement_array()
  │       └─ "coerce"       → _coerce_and_merge_array()
  └─ 检查未知 key 并记录警告
```

#### 3.6.4 数组智能匹配

**四层数组处理体系**：

| 层次 | 函数 | 触发条件 | 处理方式 |
|------|------|---------|---------|
| Settlement 数组 | `_merge_settlement_array()` | schema 指定 `smart_match` 或自动识别 | Rite/Event 策略 |
| 对象数组 | `_append_array()` | schema 指定 `append` 或默认 | 按 key 匹配合并 |
| 标量↔数组混合 | `_coerce_and_merge_array()` | schema 指定 `coerce` | 包裹标量为列表后合并 |
| 标量替换 | — | 默认 | 直接替换 |

**Rite 风格四级匹配**（`_find_matching_rite_item`）：

| 级别 | 匹配依据 | 说明 |
|------|---------|------|
| 1 | `guid` | 精确匹配 |
| 2 | `condition` 中的槽位引用（`s*.is`）| 按槽位条件匹配 |
| 3 | `condition` 完整序列化 | JSON 字符串全文比较 |
| 4 | `result_title` + `result_text` | 结果文本组合匹配 |

**Event 风格两级匹配**（`_find_matching_event_item`）：

| 级别 | 匹配依据 | 说明 |
|------|---------|------|
| 1 | `action` 中关键指令（`rite`/`event_on`/`prompt.id` 等）| 提取后匹配 |
| 2 | `action` 完整序列化 | JSON 字符串全文比较 |

**自动识别策略**：根据数组元素是否含 `action` 字段区分 Event/Rite 风格。

#### 3.6.5 特殊文件处理

| 文件 | 处理方式 | 原因 |
|------|--------|------|
| `sfx_config.json` | 整文件替换（`WHOLE_FILE_REPLACE`）| 内部结构不支持合并 |
| `tag.json` | 覆盖时验证 `name` 一致性 | tag 的 name 是游戏关键标识 |

#### 3.6.6 全局状态

| 变量 | 用途 |
|------|------|
| `merge_warnings` | 合并过程中的警告列表 |
| `MergeResult` | 单文件合并结果（merged_data, overrides, new_entries）|
| `OverrideRecord` | 单字段覆盖记录 |

---

### 3.7 id_remapper.py — ID 冲突检测与自动重映射

多个 Mod 可能定义相同 ID 的卡牌、仪式、幕后等实体，导致运行时冲突。此模块检测这些冲突并自动分配新 ID。

**支持的 ID 类型**：

| 类型 | 分类 | ID 分配起始值 | 示例 |
|------|------|-------------|------|
| cards | dictionary（字典表 key） | 2900000 | cards.json 中的条目 ID |
| tag_id | Tag 数字 ID | 3900000 | tag.json 中的 id 字段 |
| tag_code | Tag 代码 | 加后缀生成 | tag.json 中的 code |
| rite | file-based（文件名） | 5090000 | rite/5000001.json |
| event | file-based | 5390000 | event/5300001.json |
| over | file-based | 900 | over/800.json |
| loot | file-based | 6900000 | loot/6800001.json |
| rite_template | file-based | 8090000 | rite_template/*.json |
| rite_template_mappings | file-based | 8091000 | rite_template_mappings/*.json |

**核心数据结构**：

- **RemapTable** — 单个 Mod 的 ID 替换映射（cards/tag_codes/tag_ids/rite/event/over/loot 各一个 dict）
- **ModIdInfo** — 单个 Mod 的 ID 信息收集

**关键函数**：

| 函数 | 功能 |
|------|------|
| `collect_base_ids()` | 收集游戏本体的所有 ID |
| `collect_mod_ids()` | 收集单个 Mod 的 ID 定义 |
| `detect_conflicts()` | 检测所有类型的 ID 冲突（dictionary 重复 ID、Tag code/id 冲突） |
| `remap_mod_configs()` | 入口函数：检测冲突 → 分配新 ID → 生成临时重映射配置 → 返回 RemapTable |

**处理流程**：检测到冲突后，为冲突 ID 分配新值，生成临时目录存放重映射后的 Mod 配置，同时返回 `remap_tables` 供合并和资源复制时使用。

---

### 3.8 array_match.py — 数组元素匹配

从 merger.py 中抽离的数组匹配逻辑，处理多对多元素配对。

| 函数 | 功能 |
|------|------|
| `find_matching_item()` | 按 match_key 精确匹配 base 数组中的元素 |
| `resolve_duplicates()` | 多对多相似度匹配（贪心策略，每次选最相似一对） |
| `item_similarity()` | JSON 字符串相似度（SequenceMatcher，0.0~1.0） |
| `get_key_vals()` | 提取 match_key 值元组 |

**优化策略**：1×1 直接配对、1×N 找最相似候选、N×M 预计算完整相似度矩阵。

---

### 3.9 diagnostics.py — 线程安全诊断收集器

替代原有的模块级列表（parse_warnings / scan_errors / merge_warnings），提供统一的线程安全诊断信息收集。

```python
from src.core.diagnostics import diag, INFO, WARNING, ERROR

# core 层代码中收集诊断
diag.warn("scan", f"Mod {mod_id}: 找不到 manifest.json")
diag.error("merge", f"JSON 语法错误: {error}")

# GUI 层快照并清空
messages = diag.snapshot("scan", "parse")  # [(level, msg), ...]
```

**分类**：`"parse"` / `"scan"` / `"merge"` / `"schema"`

---

### 3.10 其他工具模块

| 模块 | 功能 |
|------|------|
| `schema_generator.py` | 从游戏本体 JSON 自动生成 Schema 规则文件 |
| `type_utils.py` | `classify_json(data)` 判定文件类型、`get_type_str(value)` 获取类型字符串 |
| `dsl_patterns.py` | DSL key 模式定义（条件/命令的参数化表达式），`classify_dsl_key()` 匹配模式组 |
| `profiler.py` | 可选性能评估，`@profile` 装饰器 + `profile_block()` 上下文管理器，禁用时零开销 |
| `override_utils.py` | `invalidate_stale_overrides()` 清理失效的用户 override 目录 |
| `updater.py` | `check_for_update()` 依次尝试 GitHub/Gitee API 检查新版本 |

---

### 3.11 deployer.py — 部署

| 函数 | 功能 |
|------|------|
| `generate_info_json()` | 生成合成 Mod 的 `Info.json`（包含 mod 名称列表和时间戳版本）|
| `copy_resources()` | 按优先级复制非 JSON 资源文件（图片等）|
| `deploy_to_workshop()` | 将合并结果部署为合成 Mod（临时目录 + 原子重命名）|
| `clean_synthetic_mod()` | 清理合成 Mod 目录 |

---

## 四、GUI 架构

### 4.1 app.py — 主窗口（中央协调器）

MainWindow 串联所有 GUI 面板和核心逻辑，管理异步工作线程。

**关键属性**：

| 属性 | 类型 | 说明 |
|------|------|------|
| `config` | `UserConfig` | 用户配置 |
| `mod_list_panel` | `ModListPanel` | 左侧 Mod 列表 |
| `mod_detail_panel` | `ModDetailPanel` | 右侧 Mod 详情 |
| `override_panel` | `OverridePanel` | 覆盖详情面板 |
| `log_panel` | `LogPanel` | 底部日志面板 |
| `_worker` | `MergeWorker` | 当前合并工作线程 |
| `_analyze_worker` | `AnalyzeWorker` | 冲突分析工作线程 |
| `_analyze_timer` | `QTimer` | 防抖定时器（300ms） |
| `_remapped_configs` | `list[tuple]` | ID 重映射后的 Mod 配置（缓存） |
| `_remap_tables` | `dict` | ID 映射表 |

**关键方法**：

| 方法 | 功能 |
|------|------|
| `_load_mods()` | 扫描 workshop + 本地目录，展示 mod 列表 |
| `_schedule_analyze()` | 启动 300ms 防抖定时器，避免频繁触发分析 |
| `_analyze_conflicts()` | [同步] ID 重映射 + [异步] AnalyzeWorker 冲突分析 |
| `_execute_merge()` | 检查分析完成后启动 MergeWorker |
| `_cancel_merge()` | 协作式取消合并 |
| `_on_merge_finished()` | 合并完成：生成 Info.json，展示警告 |
| `_on_merge_error()` | 合并失败：清理半成品目录，显示错误 |
| `_on_analyze_parse_errors()` | 弹 JsonFixDialog，用户修复后继续分析 |
| `_open_diff()` | 等待分析完成，打开 DiffDialog |
| `_open_json_editor()` | 打开 JsonEditorDialog |
| `_auto_check_update()` | 启动 3 秒后自动检查更新 |
| `closeEvent()` | 协作式等待所有工作线程结束（超时 5 秒），清理临时目录 |

**信号连接**：

```
mod_list_panel.mod_selected    → mod_detail_panel.show_mod
mod_list_panel.order_changed   → _save_config → _schedule_analyze（防抖）
override_panel.diff_requested  → _open_diff
log_panel.file_open_requested  → _open_json_editor
```

### 4.2 workers.py — 异步工作线程

所有 Worker 继承自 `CancellableWorker(QThread)`，通过 `threading.Event` 实现协作式取消。

| Worker | 职责 | 关键信号 |
|--------|------|---------|
| `MergeWorker` | 后台执行 JSON 合并 | `finished(dict, list)` / `progress(str)` / `parse_errors(list)` |
| `AnalyzeWorker` | 后台执行冲突分析 | `finished(list, list)` / `parse_errors(list)` |
| `SchemaWorker` | 首次运行时生成 Schema | `progress(int, int, str)` / `finished()` |
| `UpdateCheckWorker` | 后台检查版本更新 | `finished(dict \| None)` |

**可恢复错误处理模式**：

```
Worker 遇到 JSON 解析失败
  │
  ├─ parse_errors.emit(parse_failures)     # 信号通知主线程
  ├─ _resume_event.clear()
  └─ _resume_event.wait()                  # Worker 阻塞等待
      ↓
  主线程收到信号
      ↓
  JsonFixDialog.exec()                     # 模态对话框，用户修复或忽视
      ↓
  worker.set_error_resolution(resolutions) # 回传处理结果
      ↓
  _resume_event.set()                      # 唤醒 Worker
      ↓
  Worker 调用 _apply_error_resolutions()   # 继续执行
```

### 4.3 mod_list.py — Mod 列表面板

**DraggableModList**：支持内部拖拽的 QListWidget。
- 自定义 `startDrag()` 绘制半透明圆角预览
- `_row_at_pos()` 根据鼠标 Y 坐标计算插入行号
- `paintEvent()` 绘制蓝色插入指示线

**ModListPanel**：管理 mod 排序、启用/禁用、全选/全不选。

### 4.4 mod_detail.py — Mod 详情面板

显示选中 Mod 的 preview 图片、名称、版本、标签、描述。

### 4.5 override_panel.py — 覆盖详情面板

树形展示文件级覆盖链和字段级冲突：
```
[文件节点]
├── 覆盖链: [本体] ← ModA ← ModB
├── 字段: condition.s1.is  (冲突标记)
└── 新增: 新增 key: xxx  (绿色标记)
```

支持筛选模式（全部/常规/仅冲突）和文本搜索。双击文件节点打开 Diff 对比。

### 4.6 log_panel.py — 日志面板

按级别着色展示诊断信息（ERROR=红、WARNING=黄、INFO=灰）。从消息中正则提取 .json 路径和字段路径，双击可打开对应文件。

### 4.7 diff_dialog.py — Diff 对比窗口

逐级展示合并过程中各 mod 的行级差异。

**设计**：
- 预计算：创建对话框时计算所有 diff 对的 JSON 文本和高亮数据
- 懒加载：切换 tab 时才生成编辑器内容
- 行哈希加速：使用 `rapidfuzz.distance.Indel.opcodes()` C++ 后端（31000 行 ~1ms）
- 滚动同步：左右编辑框垂直 + 水平滚动联动
- 用户可保存 override 编辑到 `MOD_OVERRIDES_DIR/{mod_id}/{rel_path}`

**高亮颜色体系**：红（删除/修改）、绿（新增/修改后）、橙（冲突）、深灰（填充行）、亮蓝（搜索匹配）

### 4.8 json_editor.py — JSON 编辑器

内置 JSON 编辑器，带行号栏、语法错误高亮、自动格式化（保留注释）。

### 4.9 json_fix_dialog.py — JSON 修复弹窗

当 Worker 遇到 JSON 解析失败时弹出的多 Tab 修复对话框。每个失败文件一个 Tab，用户可逐个修复或选择忽视。

- `_save_current()` — 验证当前 Tab 的 JSON，成功则 Tab 变绿，全部成功自动关闭
- `_ignore_remaining()` — 忽视所有未修复的 Tab，关闭弹窗
- `resolutions` — `{file_path_str: {'action': 'fixed' | 'ignored'}}`，回传给 Worker

---

## 五、关键调用链

### 5.1 启动 → 显示 Mod 列表

```
main() → QApplication → MainWindow.__init__()
  → UserConfig.load()
  → _load_mods()
    → scan_all_mods(workshop_dir)
      → scan_single_mod() → load_json(Info.json)
    → scan_all_mods(local_dir)
    → mod_list_panel.set_mods()
```

### 5.2 分析冲突

```
用户调整 Mod 排序/启用
  → _save_config()
  → _schedule_analyze()              # 300ms 防抖定时器
  → _analyze_conflicts()
    → [同步] id_remapper.remap_mod_configs()
      → collect_base_ids() + collect_mod_ids()
      → detect_conflicts()           # 检测 ID 冲突
      → 分配新 ID，生成临时重映射配置
      → 返回 (remapped_configs, remap_tables)
    → [异步] AnalyzeWorker.start()
      → analyze_all_overrides(game_config_path, remapped_configs)
        → 遍历所有 mod JSON 文件
        → load_json(base) + load_json(mod)
        → compute_mod_delta()
        → _collect_field_diffs() → FieldOverride
        → (若解析失败) parse_errors.emit → JsonFixDialog → 用户修复 → 继续
      → finished.emit(overrides, parse_msgs)
  → _on_analyze_finished()
    → override_panel.set_data()
```

### 5.3 执行合并

```
_execute_merge()
  → 检查分析是否完成（若进行中则排队等待）
  → MergeWorker.start(remapped_configs, remap_tables) → run()
    → merge_all_files()
      → load_schemas()
      → 遍历所有 mod JSON 文件
      → compute_mod_delta()          # 只保留差异
      → merge_file()
        → classify_json()
        → deep_merge() 递归
          → _resolve_merge_strategy()
          → _apply_merge_strategy()
            → _merge_settlement_array() / _append_array() / ...
        → 检查并应用用户 override 编辑
      → dump_json(out_file)
      → (若解析失败) parse_errors.emit → JsonFixDialog → 用户修复 → 继续
    → copy_resources(remap_tables)   # 复制非 JSON 资源，支持 ID 重映射
    → finished.emit(results, warnings)
  → _on_merge_finished()
    → generate_info_json()           # 生成合成 Mod Info.json
    → deploy_to_workshop()           # 原子部署
```

### 5.4 Diff 对比

```
OverridePanel 双击文件节点
  → DiffDialog(rel_path, game_config_path, mod_configs)
    → _precompute_merge_states()
      → 逐 mod 调用 compute_mod_delta() + deep_merge()
      → 存储 (mod_name, prev_json, curr_json)
    → _load_tab()（懒加载）
      → _build_diff_html() → difflib.SequenceMatcher
```

---

## 六、关键业务规则

1. **Mod 优先级**：列表中越靠下优先级越高，同一字段以最后一个 Mod 为准
2. **合成 Mod ID**：固定为 `0000000001`，部署到 Workshop 目录
3. **Delta 机制**：只合并实际修改的部分，避免 mod 中未修改的内容覆盖游戏更新
4. **tag.json 验证**：覆盖时验证 `name` 字段一致性，不一致发出警告
5. **整文件替换**：`sfx_config.json` 等特殊文件跳过合并，用最后一个 Mod 的版本

---

## 七、诊断与警告收集

使用线程安全的全局诊断收集器 `diagnostics.diag`，按分类管理信息：

| 分类 | 来源 | 说明 |
|------|------|------|
| `"parse"` | `json_parser` | JSON 解析警告（BOM、格式异常） |
| `"scan"` | `mod_scanner` | Mod 扫描错误（Info.json 缺失/损坏） |
| `"merge"` | `merger` | 合并警告（类型不匹配、未知字段） |
| `"schema"` | `schema_loader` | Schema 加载警告 |

GUI 通过 `diag.snapshot(*categories)` 读取并清空消息，传递给 `log_panel` 展示。
Worker 线程内可安全调用 `diag.warn()` / `diag.error()`，无竞态问题。

---

## 八、异步与性能设计

### 防抖机制

Mod 排序/启用变化 → 300ms `QTimer.singleShot` → 只触发一次 `_analyze_conflicts()`。
快速连续操作不会导致重复分析。

### 协作式取消

`CancellableWorker` 基类使用 `threading.Event` 作为取消标志。Worker 在关键循环点调用 `_check_cancel()`，检测到取消后抛出 `_MergeCancelled` 异常，干净退出。

### 性能优化

| 优化手段 | 应用场景 | 效果 |
|---------|---------|------|
| 行哈希 + rapidfuzz C++ 后端 | diff_dialog 行级 diff | 31000 行 ~1ms |
| 懒加载 Tab | diff_dialog 多 Mod 对比 | 仅切换时加载 |
| JSON 解析缓存 `(path, mtime)` | json_parser.load_json | 避免重复解析同一文件 |
| Schema 字段定义缓存 | schema_loader.get_field_def | 避免重复导航 Schema 树 |
| 预计算高亮数据 | diff_dialog | 创建时一次计算，Tab 切换零延迟 |
| @profile 装饰器 | merger 等核心函数 | 禁用时零开销 |

---

## 九、安全与原子性

| 环节 | 安全机制 |
|------|--------|
| 配置保存 | 临时文件 + `os.replace` 原子重命名 |
| Mod 部署 | 临时目录 + 复制完成后原子 move，失败不破坏旧 Mod |
| 合并执行 | QThread 后台执行，合并期间禁用按钮 |
| 合并失败 | 自动清理半成品输出目录 |
| 窗口关闭 | `closeEvent` 等待工作线程结束 |

---

## 十、扩展指南

### 添加新的合并策略

1. 在 `merger.py` 的 `_apply_merge_strategy()` 中添加分支
2. 在 schema 文件中引用：`"merge": "new_strategy"`

### 添加新的特殊文件

```python
# merger.py
WHOLE_FILE_REPLACE = {'sfx_config.json', 'new_file.json'}
```

### 添加新的数组匹配策略

1. 实现 `_find_matching_xxx_item()` 函数
2. 在 `_merge_settlement_array()` 中添加检测逻辑
3. 在 schema 中指定 `"match_strategy": "xxx"`

### 添加新的 Schema 规则

在 `schemas/` 目录创建 `filename.schema.json`，程序启动时自动加载。
