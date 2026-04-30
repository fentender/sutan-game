# GUI 模块概览

## 技术栈

GUI 基于 PySide6 构建，采用 `QMainWindow` 作为主窗口容器，所有耗时操作通过 `QThread` 子类在后台执行，
保证界面不阻塞。模块间通过 Qt Signal/Slot 机制实现松耦合通信。

## 目录结构

```
src/gui/
├── app.py              # 主窗口（MainWindow）
├── workers.py          # 后台工作线程
├── widgets/            # 可复用基础组件
│   └── code_editor.py  # 带行号栏的代码编辑器 + JSON 编辑弹窗
├── panels/             # 主窗口内嵌面板
│   ├── mod_list.py     # Mod 拖拽排序列表
│   ├── mod_detail.py   # Mod 详情展示
│   ├── override.py     # 覆盖链树形视图
│   └── log.py          # 诊断日志面板
└── dialogs/            # 弹出对话框
    ├── diff.py         # 行级 Diff 对比
    ├── json_fix.py     # JSON 解析错误修复
    ├── deletion.py     # 删减报告
    ├── setup_wizard.py # 首次运行配置引导
    └── manual.py       # Markdown 使用手册
```

## 信号驱动通信

面板间不直接调用彼此方法，而是通过 Qt Signal 松耦合连接：

| 信号发送方 | 信号 | 接收方 | 作用 |
|---|---|---|---|
| ModListPanel | `mod_selected(ModInfo)` | ModDetailPanel.show_mod | 选中 Mod 时展示详情 |
| ModListPanel | `order_changed()` | MainWindow._save_config | 排序/启用变化后保存配置并重新分析 |
| ModListPanel | `merge_mode_changed(str, str)` | MainWindow._on_mod_merge_mode_changed | 单 Mod 合并模式变更 |
| OverridePanel | `diff_requested(str)` | MainWindow._open_diff | 双击文件节点打开 Diff 窗口 |
| LogPanel | `file_open_requested(str, str)` | MainWindow._open_json_editor | 双击日志条目打开文件编辑器 |

## 后台工作线程

所有工作线程定义在 `workers.py`，通过信号向主线程报告进度和结果：

| 线程类 | 职责 | 关键信号 |
|---|---|---|
| `StoreInitWorker` | 后台初始化 JsonStore（加载所有 Mod 的 config JSON） | `finished`、`error` |
| `DeltaInitWorker` | 预计算所有 Mod 与本体的差异（delta） | `progress(int, int)`、`finished`、`error` |
| `MergeWorker` | 执行 JSON 合并 + 资源文件复制 | `progress(int, int)`、`stage(str)`、`done(dict, list)`、`error` |
| `AnalyzeWorker` | 分析文件覆盖链和字段级冲突 | `done(list, list)`、`error` |
| `SchemaWorker` | 从游戏本体 JSON 生成 Schema 文件 | `progress(int, int, str)`、`error` |
| `UpdateCheckWorker` | 检查是否有新版本可用 | `done(object)` |

其中 `MergeWorker` 和 `AnalyzeWorker` 继承自 `CancellableWorker`，支持通过 `threading.Event`
协作式取消正在运行的任务。

## app.py 职责

`MainWindow` 是整个应用的中枢，主要职责包括：

### 面板串联

- 初始化并布局四大面板：ModListPanel、ModDetailPanel、OverridePanel、LogPanel
- 连接面板间的信号，串联完整的用户交互流程
- 提供菜单栏（文件路径设置、检查更新、使用教程）和状态栏（进度条、状态文本）

### Mod 管理流程

启动时的初始化链条：

1. `_load_mods()` — 扫描 Workshop + 本地目录的所有 Mod
2. `_start_store_init()` — 后台加载所有 JSON 到 JsonStore
3. `_on_store_ready()` — 处理解析失败文件（弹出 JsonFixDialog）、加载 override
4. `_start_delta_init()` — 后台预计算差异数据
5. `_on_delta_ready()` → `_schedule_analyze()` — 触发冲突分析

### 防抖机制

用户快速连续操作（拖拽排序、切换启用状态）时，`_analyze_timer`（QTimer，300ms 单触发）
会合并多次触发为一次 `_analyze_conflicts()` 调用，避免重复计算。

### 合并执行

- 合并期间按钮切换为"取消合并"，显示模态 QProgressDialog
- 合并完成后自动生成合成 Mod 的 Info.json
- 合并失败时清理残留的半成品输出目录

### ID 重分配

- `_remap_tables` 缓存 ID 冲突检测结果
- remap 在 `_analyze_conflicts` 中同步执行（通常很快），结果写入 JsonStore 内存
- 供 AnalyzeWorker、DiffDialog、MergeWorker 共用
- 窗口关闭或重新分析时通过 `_cleanup_remap()` 撤销 store 中的修改
