# 可复用组件 — widgets/

本目录包含跨模块共享的基础 UI 组件，目前仅有 `code_editor.py`。

## code_editor.py

提供带行号栏的代码编辑器及 JSON 文件编辑弹窗，是多个对话框的底层基础组件。

### CodeEditor（QPlainTextEdit）

核心编辑器组件，被 DiffDialog、JsonFixDialog、DeletionReportDialog 等多处复用。

#### 行号栏

- 内部类 `_LineNumberArea(QWidget)` 作为左侧行号侧栏
- 行号宽度根据文档总行数动态计算
- 支持两种模式：
  - **普通模式**：行号从 1 开始顺序递增
  - **Diff 模式**：通过 `_diff_line_map` 列表映射，填充行不显示行号，
    真实行显示原始行号

#### JSON 语法检验

CodeEditor 本身不做语法检验，而是由宿主对话框调用 `highlight_line()` 标记错误行。
检验逻辑在 `JsonEditorDialog._detect_error()` 中：先用 `clean_json_text()` 清理注释和尾逗号，
再调用 `json.loads()` 捕获 `JSONDecodeError`。

#### 行级背景高亮

- `highlight_line(line_no, scroll_to, color, append)` — 对指定行设置背景色
  - 默认红色（`QColor(100, 30, 30)`），可自定义
  - `append=True` 追加到已有高亮（不覆盖）
  - `scroll_to=True` 同时滚动到该行
- `clear_highlights()` — 清除所有 ExtraSelection 高亮
- `setExtraSelections()` — DiffDialog 用于批量设置多行差异高亮

#### Diff 模式

当 `_diff_line_map` 被设置（非 None）时进入 Diff 模式：

- **行号映射**：`list[int | None]`，`None` 表示填充行（不显示行号），
  `int` 表示对应的原始行号（0-based）
- **填充行背景**：`paintEvent` 重写后，对填充行绘制深灰色背景
  （`_padding_color = QColor(30, 30, 30)`），覆盖选区高亮
- **智能复制**：`createMimeDataFromSelection()` 重写，复制时自动跳过填充行，
  仅保留真实行文本

### JsonEditorDialog（QDialog）

独立的 JSON 文件编辑弹窗，由日志面板双击触发（`MainWindow._open_json_editor`）。

#### 构造参数

| 参数 | 类型 | 说明 |
|---|---|---|
| `file_path` | `Path` | 要编辑的 JSON 文件路径 |
| `search_key` | `str` | 可选，打开后自动定位到包含此 key 的行（黄色高亮） |

#### 功能

- **加载 + 错误检测**：读取文件内容后立即调用 `_detect_error()` 检测语法错误
- **错误提示条**：顶部红色横幅显示错误行号和描述，无错误时隐藏
- **错误行标红**：通过 CodeEditor.highlight_line 标记
- **字段定位**：如果提供了 `search_key`，在文本中搜索 `"key"` 并用黄色高亮
- **格式化**：调用 `_format_with_comments()`，保持滚动位置，重新检测错误
- **保存**：写回文件后重新验证，弹窗提示保存结果

### _format_with_comments(text) -> str

保留注释的 JSON 格式化函数，基于括号深度计算缩进：

1. 逐行调用 `_split_code_comment()` 拆分代码和注释
2. 用 `_count_brackets()` 统计开/闭括号数（排除字符串内的括号）
3. 行首闭括号（`}`、`]`）先减少缩进再输出
4. 缩进单位为 4 空格

该函数被以下模块复用：
- `diff.py`（DiffDialog 格式化覆盖）
- `json_fix.py`（JsonFixDialog 格式化修复内容）

### 复用关系

```
code_editor.py
├── diff.py         — DiffDialog 左右两侧均为 CodeEditor（左只读、右可编辑）
├── json_fix.py     — JsonFixDialog 每个 Tab 一个 CodeEditor
├── deletion.py     — DeletionReportDialog 预览窗口使用只读 CodeEditor
└── app.py          — MainWindow 通过 JsonEditorDialog 编辑日志中引用的文件
```
