# 09 — GUI 界面

基于 PySide6 构建的图形界面层，串联所有 core 模块的逻辑，提供用户交互入口。采用 `QMainWindow` 作为主窗口容器，所有耗时操作通过 `QThread` 后台执行，模块间通过 Qt Signal/Slot 松耦合通信。

## 涉及文件

| 文件 | 职责 |
|------|------|
| `src/gui/app.py` | 主窗口（MainWindow），串联所有逻辑 |
| `src/gui/workers.py` | 后台工作线程（6 个 Worker） |
| `src/gui/widgets/code_editor.py` | 带行号栏的代码编辑器 + JSON 编辑弹窗 |
| `src/gui/panels/mod_list.py` | Mod 拖拽排序列表面板 |
| `src/gui/panels/mod_detail.py` | Mod 详情展示面板 |
| `src/gui/panels/override.py` | 覆盖链树形视图面板 |
| `src/gui/panels/log.py` | 诊断日志面板 |
| `src/gui/dialogs/diff.py` | 行级 Diff 对比对话框 |
| `src/gui/dialogs/json_fix.py` | JSON 解析错误修复对话框 |
| `src/gui/dialogs/deletion.py` | 删减报告对话框 |
| `src/gui/dialogs/setup_wizard.py` | 首次运行配置引导 |
| `src/gui/dialogs/manual.py` | Markdown 使用手册展示 |

## 模块依赖

依赖所有 core 模块 + config.py。

---

## 信号驱动通信

面板间不直接调用彼此方法，而是通过 Qt Signal 松耦合连接：

| 信号发送方 | 信号 | 接收方 | 作用 |
|-----------|------|--------|------|
| ModListPanel | `mod_selected(ModInfo)` | ModDetailPanel.show_mod | 选中 Mod 时展示详情 |
| ModListPanel | `order_changed()` | MainWindow._save_config | 排序/启用变化后保存配置并重新分析 |
| ModListPanel | `merge_mode_changed(str, str)` | MainWindow._on_mod_merge_mode_changed | 单 Mod 合并模式变更 |
| OverridePanel | `diff_requested(str)` | MainWindow._open_diff | 双击文件节点打开 Diff 窗口 |
| LogPanel | `file_open_requested(str, str)` | MainWindow._open_json_editor | 双击日志条目打开文件编辑器 |

---

## 后台工作线程（workers.py）

| 线程类 | 职责 | 关键信号 |
|--------|------|---------|
| `StoreInitWorker` | 后台初始化 [JsonStore](02-json-pipeline.md) | `finished`、`error` |
| `DeltaInitWorker` | 预计算所有 Mod 与本体的 [差异](04-diff-engine.md) | `progress(int, int)`、`finished`、`error` |
| `MergeWorker` | 执行 [JSON 合并](05-merge-engine.md) + 资源文件复制 | `progress(int, int)`、`stage(str)`、`done(dict, list)`、`error` |
| `AnalyzeWorker` | 分析文件覆盖链和字段级 [冲突](06-mod-management.md) | `done(list, list)`、`error` |
| `SchemaWorker` | 从游戏本体 JSON 生成 [Schema](03-schema-system.md) | `progress(int, int, str)`、`error` |
| `UpdateCheckWorker` | 检查 [新版本](08-platform.md) | `done(object)` |

其中 `MergeWorker` 和 `AnalyzeWorker` 继承自 `CancellableWorker`，支持通过 `threading.Event` 协作式取消。

---

## MainWindow 主窗口（app.py）

### 面板布局

- 初始化并布局四大面板：ModListPanel、ModDetailPanel、OverridePanel、LogPanel
- 连接面板间信号，串联完整用户交互流程
- 菜单栏（文件路径设置、检查更新、使用教程）和状态栏（进度条、状态文本）

### 启动初始化链

1. `_load_mods()` — 扫描 Workshop + 本地目录的所有 Mod
2. `_start_store_init()` — 后台加载所有 JSON 到 JsonStore
3. `_on_store_ready()` — 处理解析失败文件（弹出 JsonFixDialog）、加载 override
4. `_start_delta_init()` — 后台预计算差异数据
5. `_on_delta_ready()` → `_schedule_analyze()` — 触发冲突分析

### 防抖机制

用户快速连续操作（拖拽排序、切换启用状态）时，`_analyze_timer`（QTimer，300ms 单触发）合并多次触发为一次 `_analyze_conflicts()` 调用，避免重复计算。

### 合并执行

- 合并期间按钮切换为"取消合并"，显示模态 QProgressDialog
- 合并完成后自动生成合成 Mod 的 Info.json
- 合并失败时清理残留的半成品输出目录

### ID 重分配

- `_remap_tables` 缓存 [ID 冲突检测](06-mod-management.md) 结果
- remap 在 `_analyze_conflicts` 中同步执行，结果写入 JsonStore 内存
- 供 AnalyzeWorker、DiffDialog、MergeWorker 共用
- 窗口关闭或重新分析时通过 `_cleanup_remap()` 撤销 store 中的修改

---

## 面板

### ModListPanel — Mod 拖拽排序列表（mod_list.py）

#### DraggableModList（QListWidget）

- 拖拽时绘制半透明圆角预览（Mod 名称 + 深灰背景）
- 拖拽过程中在目标位置绘制蓝色水平指示线
- 根据鼠标在目标项的上/下半区决定插入位置
- 发出 `item_moved(from_row, to_row)` 信号

#### ModListItem（QWidget）

单个 Mod 的列表项，水平布局：过时警告图标 + QCheckBox + 名称 + 合并模式 ComboBox + 上移/下移按钮

过时警告分三级：
- 红色感叹号：严重过时（早于大版本更新 `MAJOR_UPDATE_TS`）
- 黄色三角：轻微过时（在大版本之后更新过，但早于游戏最新更新）
- 蓝色 i：过时但纯增量（未修改本体文件），风险较低

#### ModListPanel 信号

| 信号 | 参数 | 触发时机 |
|------|------|---------|
| `mod_selected` | `ModInfo` | 点击选中某个 Mod |
| `order_changed` | 无 | 拖拽排序、上下移动、启用/禁用 |
| `merge_mode_changed` | `mod_id, mode_value` | 切换单个 Mod 的合并模式 |

### ModDetailPanel — Mod 详情（mod_detail.py）

位于主窗口右侧，展示选中 Mod 的详细信息：预览图（140x140）、名称（16px 加粗）、版本、标签、描述（可滚动）。

通过 `show_mod(ModInfo)` 槽函数接收信号。

### OverridePanel — 覆盖链树形视图（override.py）

树形视图展示所有被 Mod 修改的文件及其字段级覆盖情况。

- **文件节点**（顶层）：显示文件路径 + 覆盖链（`[本体] <- Mod1 <- Mod2`），冲突橙色/数组合并黄色标记
- **字段节点**（子级）：显示字段路径、覆盖链、最终值，新增条目绿色标记
- **筛选**：所有 / 普通 / 数组合并 / 严重过时 Mod / 冲突 + Mod 下拉框 + 搜索框
- 双击文件节点发出 `diff_requested(rel_path)` 信号

### LogPanel — 诊断日志（log.py）

底部诊断消息展示面板，初始隐藏，有消息时自动显示。

- 通过 [Diagnostics](07-diagnostics.md) `diag.snapshot()` 读取消息
- 按级别着色：错误浅红 `#ee8888`、警告浅黄 `#eec864`、信息浅灰 `#b4b4b4`
- 筛选按钮组：全部 / 信息 / 警告 / 错误
- 双击日志条目可打开引用的 JSON 文件（通过正则提取 `.json` 路径）

---

## 对话框

### DiffDialog — Diff 对比（diff.py）

逐级展示游戏本体经各 Mod 覆盖后的行级差异，是用户审查和编辑合并结果的核心界面。

- **多 Tab**：每个启用 Mod 一个 Tab，标签颜色标记冲突（橙色）或数组合并（蓝色），懒加载
- **行级高亮**：红底（删除）、绿底（新增）、橙底（冲突/MULTI_MOD）、蓝底（用户覆写/OVERRIDE）、深灰（填充行）
- **搜索导航**：`Ctrl+F` 搜索栏 + 上一个/下一个变化块按钮
- **保存/重置 Override**：验证 JSON 语法 → 计算 delta → 存入 JsonStore 的 override 层
- **滚动同步**：左右编辑器双向同步

### JsonFixDialog — JSON 修复（json_fix.py）

JsonStore 初始化完成后，如果有 JSON 解析失败的文件，自动弹出。

- 每个失败文件一个 Tab（标题 `Mod名 / 相对路径`）
- 修复流程：编辑 → 格式化 → 保存（写回文件 + 验证） → Tab 标签变绿
- "忽视剩余"：未修复文件标记为 `ignored`，合并时整文件复制

### DeletionReportDialog — 删减报告（deletion.py）

展示各 Mod 在智能合并模式下被阻止的字段删减操作。

- "所有" Tab：按文件分组的树形视图
- 每个 Mod 单独 Tab
- "统计" Tab：按容器聚合统计
- 双击叶子节点打开预览（被删除行浅红/深红背景）

### SetupDialog — 首次配置（setup_wizard.py）

首次运行时引导用户配置游戏安装目录和创意工坊目录。

- 路径实时校验，不通过时禁用"确定"按钮
- 自动推导：填写游戏路径后自动推导创意工坊路径

### ManualDialog — 使用手册（manual.py）

在应用内展示 `docs/用户使用手册.md`，使用 `QTextBrowser.setMarkdown()` 渲染，支持外部链接跳转浏览器。

---

## CodeEditor 组件（widgets/code_editor.py）

底层编辑器组件，被 DiffDialog、JsonFixDialog、DeletionReportDialog 等多处复用。

### CodeEditor（QPlainTextEdit）

- **行号栏**：内部类 `_LineNumberArea`，宽度随行数动态计算
- **普通模式**：行号从 1 顺序递增
- **Diff 模式**：通过 `_diff_line_map` 映射，填充行不显示行号
- **行级背景高亮**：`highlight_line()` 设置指定行背景色
- **智能复制**：Diff 模式下复制时自动跳过填充行

### JsonEditorDialog（QDialog）

独立的 JSON 文件编辑弹窗，由 LogPanel 双击触发。

- 加载文件后立即检测语法错误
- 错误提示条 + 错误行标红
- 格式化保留注释（`_format_with_comments`）
- 可选字段定位（`search_key`）

### _format_with_comments(text) -> str

保留注释的 JSON 格式化函数，基于括号深度计算缩进（4 空格），被 DiffDialog 和 JsonFixDialog 复用。
