# 对话框 — dialogs/

所有弹出式对话框，通过 `QDialog.exec()` 模态打开。

## diff.py — Diff 对比窗口

### DiffDialog（QDialog）

逐级展示游戏本体经各 Mod 覆盖后的行级差异，是用户审查和编辑合并结果的核心界面。

#### 布局

- 顶部：文件路径标签 + 警告条（数组合并/Schema 验证警告）
- 主体：QTabWidget，每个启用 Mod 一个 Tab
- 每个 Tab：错误提示条、标签行（含导航/保存/重置按钮）、左右 QSplitter（两个 CodeEditor）

#### 多 Mod Tab 切换

- 每个 Tab 对应一个 Mod 的覆盖层级
- Tab 标签颜色标记：橙色 = 有冲突，蓝色 = 有数组合并
- 懒加载：首次切换到某 Tab 时才设置文本和高亮

#### 行级差异高亮

预计算阶段（`_precompute_merge_states`）通过 `MergeCache` 获取结构化合并状态，
从 `ChangeKind` 映射到颜色：

| 颜色 | 含义 |
|---|---|
| 红底 `(80,30,30)` | 删除的行 |
| 绿底 `(30,80,30)` | 新增的行 |
| 橙底 `(140,90,20)` | 冲突（多 Mod 修改同一字段） |
| 蓝底 `(50,80,120)` | 用户手动覆写（override） |
| 深灰 `(30,30,30)` | 填充行（对齐用） |
| 亮蓝 `(40,100,180)` | 搜索匹配高亮 |

同一行出现多种高亮时，按优先级保留最高的（override > 冲突 > 增删 > 填充）。

#### 搜索与导航

- `Ctrl+F` 切换搜索栏，每侧编辑器独立搜索，匹配处亮蓝高亮
- 上一个/下一个变化块按钮，计数标签（如 `3 / 15`），循环导航

#### 保存/重置 Override

- **保存**：验证 JSON 语法 -> 计算 delta -> 存入 JsonStore 的 override 层
- **重置**：删除该 Mod 对该文件的 override
- 操作后 `_refresh_all()` 重新预计算所有 Tab

#### 滚动同步

左右编辑器的垂直和水平滚动条双向同步，使用 `syncing` 标志防止递归触发。

---

## json_fix.py — JSON 修复对话框

### JsonFixDialog（QDialog）

在 JsonStore 初始化完成后，如果有 JSON 解析失败的文件，自动弹出此对话框。

#### 结构

- 顶部红色横幅：显示失败文件总数
- QTabWidget：每个失败文件一个 Tab（标题 `Mod名 / 相对路径`）
  - 错误提示条 + CodeEditor（可编辑，初始高亮错误行）
- 底部：格式化、保存、忽视剩余按钮

#### 修复流程

1. 用户编辑文件内容
2. "格式化"：`_format_with_comments()` 重新缩进 + 自动检测错误
3. "保存"：写回原文件 -> `json.loads()` 验证
   - 通过：Tab 标签变绿 + 绿色提示条，记录 `action: 'fixed'`
   - 失败：弹窗提示仍有错误，更新错误行高亮
4. 所有 Tab 修复完成后自动关闭
5. "忽视剩余"或关闭窗口：未修复文件标记为 `action: 'ignored'`

返回值 `resolutions: dict[str, dict[str, str]]`，MainWindow 据此决定是否重新加载。

---

## deletion.py — 删减报告对话框

### DeletionReportDialog（QDialog）

展示各 Mod 在智能合并模式下被阻止的字段删减操作。

#### Tab 结构

- **"所有"Tab**：按文件分组的树形视图（文件 -> 容器 -> 叶子字段），显示 Mod 名和被删除的值
- **每个 Mod 单独 Tab**：仅展示该 Mod 的删减
- **"统计"Tab**：按容器聚合统计（合计 + 每个 Mod 的容器级统计）

#### 双击预览

双击叶子节点打开预览子对话框：只读 CodeEditor 展示无删除版本的合并结果，
被删除行浅红背景，双击目标字段深红背景并滚动定位。

#### 辅助函数

- `_dedup_records()` — 按 field_path 去重，合并涉及的 Mod 名称
- `_get_container_name()` — 从字段路径提取最深容器名
- `_diff_deleted_lines()` — 对比有/无删除版本，返回被删行号集合

---

## setup_wizard.py — 首次运行配置引导

### SetupDialog（QDialog）

首次运行时弹出，引导用户配置游戏安装目录和创意工坊目录。

- **路径校验**：实时验证路径存在性，不通过时禁用"确定"按钮，底部红色文本提示问题
- **自动推导**：填写游戏路径后自动调用 `infer_workshop_path_from_game()`
  推导创意工坊路径（仅在创意工坊输入框为空时生效）
- 属性：`game_path: str`、`workshop_path: str`

---

## manual.py — Markdown 手册展示

### ManualDialog（QDialog）

在应用内展示用户使用手册。使用 `QTextBrowser.setMarkdown()` 渲染，
支持外部链接跳转浏览器。手册路径 `docs/用户使用手册.md`，
兼容 PyInstaller 打包环境（通过 `sys._MEIPASS`）。
