# 主窗口面板 — panels/

主窗口由四个面板组成，从上到下依次为：Mod 列表 + 详情（水平分割）、覆盖详情、操作按钮栏、日志面板。

## mod_list.py — Mod 列表面板

### DraggableModList（QListWidget）

支持拖拽排序的列表控件：

- 拖拽时绘制半透明圆角预览（Mod 名称 + 深灰背景）
- 拖拽过程中在目标位置绘制蓝色水平指示线（`QPen(QColor(51, 153, 255), 2)`）
- 根据鼠标在目标项的上/下半区决定插入位置
- 发出 `item_moved(from_row, to_row)` 信号通知数据模型同步

### ModListItem（QWidget）

单个 Mod 的列表项控件，水平布局：

| 组件 | 说明 |
|---|---|
| 过时警告图标 | 根据 Mod 更新时间 vs 游戏更新时间显示不同级别图标 |
| QCheckBox | 启用/禁用，发出 `toggled(mod_id, enabled)` |
| QLabel | Mod 名称，tooltip 显示 ID、版本、文件数 |
| QComboBox | Per-Mod 合并模式（跟随全局/智能/自适应/正常/替换） |
| 上移/下移按钮 | 发出 `move_up(mod_id)` / `move_down(mod_id)` |

过时警告分三级：
- 红色感叹号：严重过时（早于大版本更新 `MAJOR_UPDATE_TS`）
- 黄色三角：轻微过时（在大版本之后更新过，但早于游戏最新更新）
- 蓝色 i：过时但纯增量（未修改本体文件），风险较低

### ModListPanel（QWidget）

容器面板，管理 Mod 列表的数据和交互：

- **数据管理**：`_mods`（有序列表）、`_enabled`（启用状态）、
  `_merge_modes`（per-mod 合并模式）、`_overlap`（是否与本体文件重叠）
- **排序规则**：按用户保存的顺序排列，未勾选的排到勾选的后面
- **全选/全不选**按钮

#### 信号

| 信号 | 参数 | 触发时机 |
|---|---|---|
| `mod_selected` | `ModInfo` | 点击选中某个 Mod |
| `order_changed` | 无 | 拖拽排序、上下移动、启用/禁用 |
| `merge_mode_changed` | `mod_id, mode_value` | 切换单个 Mod 的合并模式 |

---

## mod_detail.py — Mod 详情面板

### ModDetailPanel（QWidget）

位于主窗口右侧，展示选中 Mod 的详细信息：

- **预览图**：140x140 固定尺寸，等比缩放，深灰背景，无图时显示"无预览图"
- **名称**：16px 加粗
- **版本**：灰色文本
- **标签**：逗号分隔
- **描述**：可滚动区域（QScrollArea），短文本禁用滚动条

通过 `show_mod(ModInfo)` 槽函数接收 ModListPanel 的 `mod_selected` 信号。

---

## override.py — 覆盖详情面板

### OverridePanel（QWidget）

树形视图展示所有被 Mod 修改的文件及其字段级覆盖情况。

#### 数据结构

接收 `list[FileOverrideInfo]`，每个 `FileOverrideInfo` 包含：
- `rel_path`：相对路径
- `mod_chain`：覆盖链（Mod 名称列表，按优先级排列）
- `field_overrides`：字段级覆盖详情
- `new_entries`：新增条目
- `has_conflict` / `has_warning`：是否有冲突/数组合并

#### 树形结构

- **文件节点**（顶层）：显示文件路径 + 覆盖链（`[本体] <- Mod1 <- Mod2`）
  - 冲突文件：橙色标记
  - 数组合并文件：黄色标记
- **字段节点**（子级）：显示字段路径（用 ` -> ` 分隔）、覆盖链、最终值
  - 新增条目：绿色"新增"标记

#### 筛选和搜索

- **筛选按钮组**：所有 / 普通 / 数组合并 / 严重过时Mod / 冲突
- **Mod 下拉框**：按单个 Mod 筛选
- **搜索框**：模糊匹配文件名或 Mod 名
- 排序规则：冲突在前、数组合并次之、普通在后

#### 交互

- 双击文件节点发出 `diff_requested(rel_path)` 信号，打开 DiffDialog
- 可折叠/展开整个面板

---

## log.py — 日志面板

### LogPanel（QWidget）

底部诊断消息展示面板，初始隐藏，有消息时自动显示。

#### 消息来源

通过 `diagnostics.diag` 单例收集，各阶段快照后传入：
- 扫描阶段（scan）：Mod 扫描错误
- 解析阶段（parse）：JSON 解析警告
- JSON 加载阶段（json）：JsonStore 加载问题
- 合并阶段（merge）：合并过程中的警告
- remap 阶段：ID 重分配日志

#### 按级别着色

| 级别 | 颜色 |
|---|---|
| 错误（ERROR） | 浅红 `#ee8888` |
| 警告（WARNING） | 浅黄 `#eec864` |
| 信息（INFO） | 浅灰 `#b4b4b4` |

#### 筛选

头部提供按钮组：全部 / 信息 / 警告 / 错误，切换后即时过滤列表项。

#### 双击打开文件

日志条目中包含文件路径（通过正则提取 `.json` 路径）时，双击发出
`file_open_requested(file_path, field_path)` 信号，MainWindow 打开 JsonEditorDialog。

字段路径通过 `\.json\s*>\s*([a-zA-Z0-9_.]+):` 模式从消息文本中提取。

#### 辅助函数

`prefix_mod_title(msg, name_map)` — 从日志消息中提取 mod_id，
查找对应的 Mod 名称并添加 `【Mod名】` 前缀，提升可读性。支持两种格式：
- `Mod {mod_id}: ...`（scan_errors 格式）
- 路径中 `/{mod_id}/config/` 提取
