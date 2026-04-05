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
│   ├── main.py                   # 应用入口
│   ├── config.py                 # 配置管理：路径常量、用户偏好持久化
│   ├── core/                     # 核心逻辑层
│   │   ├── json_parser.py       # JSON 解析（注释/BOM/尾逗号处理）
│   │   ├── mod_scanner.py       # Mod 扫描（遍历 workshop + 本地目录）
│   │   ├── conflict.py          # 冲突分析（字段级覆盖链追踪）
│   │   ├── merger.py            # 【核心】深度合并算法
│   │   ├── deployer.py          # 部署（生成合成 Mod、原子替换）
│   │   └── schema_loader.py     # Schema 规则加载与查询
│   └── gui/                      # GUI 层（PySide6）
│       ├── app.py               # 主窗口、MergeWorker 后台线程
│       ├── mod_list.py          # Mod 列表面板（拖拽排序、启用/禁用）
│       ├── mod_detail.py        # Mod 详情面板（preview + 描述）
│       ├── override_panel.py    # 覆盖详情（树形展示冲突）
│       ├── diff_dialog.py       # Diff 对比（逐级合并可视化）
│       └── json_editor.py       # JSON 编辑器（行号 + 错误高亮）
├── schemas/                      # 合并规则文件（.schema.json）
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

- `save()` 使用临时文件 + `os.replace` 原子写入
- `load()` 捕获损坏文件异常，回退默认值

---

### 3.2 json_parser.py — JSON 处理

游戏 JSON 非标准格式，包含 `//` 注释和尾随逗号。

| 函数 | 功能 |
|------|------|
| `strip_js_comments()` | 逐行剥离 `//` 注释，使用字符串状态机避免误删字符串内的 `//` |
| `strip_trailing_commas()` | 正则去除 `}` 或 `]` 前的尾逗号 |
| `load_json()` | 加载并自动修正 JSON，检测并移除 BOM |
| `dump_json()` | 输出标准 JSON |
| `parse_warnings` | 模块级列表，收集解析警告供 GUI 展示 |

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

### 3.7 deployer.py — 部署

| 函数 | 功能 |
|------|------|
| `generate_info_json()` | 生成合成 Mod 的 `Info.json`（包含 mod 名称列表和时间戳版本）|
| `copy_resources()` | 按优先级复制非 JSON 资源文件（图片等）|
| `deploy_to_workshop()` | 将合并结果部署为合成 Mod（临时目录 + 原子重命名）|
| `clean_synthetic_mod()` | 清理合成 Mod 目录 |

---

## 四、GUI 架构

### 4.1 app.py — 主窗口

**MergeWorker**（QThread）：在后台执行合并操作，通过信号传递结果和警告到主线程。

**MainWindow 关键方法**：

| 方法 | 功能 |
|------|------|
| `_load_mods()` | 扫描 workshop + 本地目录，展示 mod 列表 |
| `_analyze_conflicts()` | 调用冲突分析，在 override_panel 中展示结果 |
| `_execute_merge()` | 启动 MergeWorker，合并期间禁用按钮 |
| `_on_merge_finished()` | 合并完成：生成 Info.json，展示警告 |
| `_on_merge_error()` | 合并失败：清理半成品目录，显示错误 |
| `_show_errors()` / `_log_error()` | 错误日志面板管理 |
| `closeEvent()` | 关闭窗口时等待工作线程结束 |

**信号连接**：
- `mod_list_panel.mod_selected` → `mod_detail_panel.show_mod`
- `mod_list_panel.order_changed` → `_save_config`

### 4.2 mod_list.py — Mod 列表面板

**DraggableModList**：支持内部拖拽的 QListWidget。
- 自定义 `startDrag()` 绘制半透明圆角预览
- `_row_at_pos()` 根据鼠标 Y 坐标计算插入行号
- `paintEvent()` 绘制蓝色插入指示线

**ModListPanel**：管理 mod 排序、启用/禁用、全选/全不选。

### 4.3 mod_detail.py — Mod 详情面板

显示选中 Mod 的 preview 图片、名称、版本、标签、描述。

### 4.4 override_panel.py — 覆盖详情面板

树形展示文件级覆盖链和字段级冲突：
```
[文件节点]
├── 覆盖链: [本体] ← ModA ← ModB
├── 字段: condition.s1.is  (冲突标记)
└── 新增: 新增 key: xxx  (绿色标记)
```

支持筛选模式（全部/常规/仅冲突）和文本搜索。双击文件节点打开 Diff 对比。

### 4.5 diff_dialog.py — Diff 对比窗口

逐级展示合并过程中各 mod 的行级差异。

**设计**：
- 预计算：创建对话框时计算所有 diff 对的 JSON 文本
- 懒加载：切换 tab 时才生成 HTML diff
- 批量 HTML：一次字符串拼接 + 单次 `setHtml()`
- 滚动同步：左右编辑框垂直滚动联动

### 4.6 json_editor.py — JSON 编辑器

内置 JSON 编辑器，带行号栏、语法错误高亮、自动格式化（保留注释）。

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
_analyze_conflicts()
  → analyze_all_overrides(game_config_path, mod_configs)
    → 遍历所有 mod JSON 文件
    → load_json(base) + load_json(mod)
    → _collect_field_diffs() → FieldOverride
  → override_panel.set_data()
```

### 5.3 执行合并

```
_execute_merge()
  → MergeWorker.start() → run()
    → merge_all_files()
      → load_schemas()
      → 遍历所有 mod JSON 文件
      → compute_mod_delta()  → 只保留差异
      → merge_file()
        → classify_json()
        → deep_merge()  递归
          → _resolve_merge_strategy()
          → _apply_merge_strategy()
            → _merge_settlement_array() / _append_array() / ...
      → dump_json(out_file)
    → copy_resources()
    → finished.emit(results, warnings)
  → _on_merge_finished()
    → generate_info_json()
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

## 七、全局状态与警告收集

各模块使用模块级列表收集警告/错误，GUI 读取后展示：

| 模块 | 变量 | 说明 |
|------|------|------|
| `json_parser` | `parse_warnings` | JSON 解析警告（BOM、格式异常）|
| `mod_scanner` | `scan_errors` | Mod 扫描错误（Info.json 缺失/损坏）|
| `merger` | `merge_warnings` | 合并警告（类型不匹配、未知字段）|

MergeWorker 在工作线程内快照 `merge_warnings`，通过信号传递给主线程，避免竞态。

---

## 八、安全与原子性

| 环节 | 安全机制 |
|------|--------|
| 配置保存 | 临时文件 + `os.replace` 原子重命名 |
| Mod 部署 | 临时目录 + 复制完成后原子 move，失败不破坏旧 Mod |
| 合并执行 | QThread 后台执行，合并期间禁用按钮 |
| 合并失败 | 自动清理半成品输出目录 |
| 窗口关闭 | `closeEvent` 等待工作线程结束 |

---

## 九、扩展指南

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
