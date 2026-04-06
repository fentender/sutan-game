"""
Diff 对比窗口 - 逐级展示游戏本体经各 Mod 覆盖后的行级差异
"""
import copy
import difflib
import json
from html import escape
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QTextEdit, QLabel, QSplitter, QWidget, QPushButton,
    QLineEdit, QMessageBox
)
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtCore import Qt, Signal

from ..config import SCHEMA_DIR, MOD_OVERRIDES_DIR
from ..core.json_parser import load_json, strip_js_comments, strip_trailing_commas
from ..core.merger import deep_merge, classify_json, compute_mod_delta, _DELETED
from ..core.schema_loader import load_schemas, resolve_schema, get_schema_root_key
from .json_editor import CodeEditor, _format_with_comments

_MONO_FONT = QFont("Consolas", 10)

# diff 行的 HTML 颜色
_CLR_DEL_BG = "#501e1e"
_CLR_DEL_FG = "#dc9696"
_CLR_INS_BG = "#1e501e"
_CLR_INS_FG = "#96dc96"
_CLR_REP_OLD_BG = "#643214"
_CLR_REP_OLD_FG = "#e6aa82"
_CLR_REP_NEW_BG = "#1e5032"
_CLR_REP_NEW_FG = "#96e6aa"
_CLR_NORMAL_FG = "#dcdcdc"


class DiffDialog(QDialog):
    """文件 Diff 对比窗口"""

    def __init__(self, rel_path: str, game_config_path: Path,
                 mod_configs: list[tuple[str, str, Path]],
                 allow_deletions: bool = False, parent=None):
        super().__init__(parent)
        self._rel_path = rel_path
        self._game_config_path = game_config_path
        self._mod_configs = mod_configs
        self._allow_deletions = allow_deletions

        # 预计算各级合并状态的 JSON 文本（轻量）
        self._diff_pairs: list[tuple[str, str, str, str]] = []  # (mod_id, mod_name, prev_text, curr_text)
        self._precompute_merge_states()

        # 懒加载标记：已填充 diff 的 tab 索引
        self._loaded_tabs: set[int] = set()
        # 各 tab 的左右 QTextEdit 引用
        self._tab_edits: list[tuple[QTextEdit, QTextEdit]] = []
        # 导航相关：变化块位置、当前索引、按钮和计数标签
        self._tab_diff_positions: list[list[int]] = []
        self._tab_current_idx: list[int] = []
        self._tab_nav_widgets: list[tuple[QPushButton, QLabel, QPushButton]] = []

        self.setWindowTitle(f"Diff 对比 - {rel_path}")
        self.resize(1000, 650)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        self._build_ui()

    def _precompute_merge_states(self):
        """预计算逐级合并的 JSON 文本对，不涉及 UI 操作"""
        self._diff_pairs.clear()
        base_file = self._game_config_path / self._rel_path
        base_data = load_json(base_file) if base_file.exists() else {}

        # 加载 schema
        schemas = load_schemas(SCHEMA_DIR)
        schema = resolve_schema(self._rel_path, schemas)
        root_key = get_schema_root_key(schema) if schema else None

        file_type = classify_json(base_data) if base_data else "config"

        current: dict = copy.deepcopy(base_data)
        for mod_id, mod_name, config_path in self._mod_configs:
            mod_file = config_path / self._rel_path
            if not mod_file.exists():
                continue

            mod_data = load_json(mod_file)

            # 计算 delta：只保留 mod 相对于游戏本体实际修改的部分
            delta = compute_mod_delta(base_data, mod_data, file_type, self._allow_deletions)
            if not delta:
                continue

            prev_text = _format_json(current)

            field_path = [root_key] if root_key else None
            if file_type == "dictionary":
                next_state = copy.deepcopy(current)
                for key, value in delta.items():
                    if value is _DELETED:
                        next_state.pop(key, None)
                        continue
                    if key in next_state:
                        next_state[key] = deep_merge(next_state[key], value, schema, field_path)
                    else:
                        next_state[key] = copy.deepcopy(value)
                current = next_state
            else:
                current = deep_merge(current, delta, schema, field_path)  # type: ignore[assignment]

            curr_text = _format_json(current)

            # 检查是否存在用户 override
            override_file = MOD_OVERRIDES_DIR / mod_id / self._rel_path
            if override_file.exists():
                curr_text = override_file.read_text(encoding="utf-8")
                current = json.loads(curr_text)

            self._diff_pairs.append((mod_id, mod_name, prev_text, curr_text))

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 文件路径标题
        path_label = QLabel(self._rel_path)
        path_label.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px;")
        path_label.setFixedHeight(24)
        layout.addWidget(path_label)

        # 搜索栏（默认隐藏）
        self._search_bar = QLineEdit()
        self._search_bar.setPlaceholderText(
            "搜索... (Enter=下一个, Shift+Enter=上一个, Esc=关闭)")
        self._search_bar.setVisible(False)
        self._search_bar.returnPressed.connect(self._find_next)
        layout.addWidget(self._search_bar)

        QShortcut(QKeySequence("Ctrl+F"), self, self._toggle_search)
        QShortcut(QKeySequence("Shift+Return"), self._search_bar,
                  self._find_prev)
        QShortcut(QKeySequence("Escape"), self._search_bar,
                  self._close_search)

        if not self._diff_pairs:
            placeholder = QLabel("没有 Mod 修改此文件")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(placeholder)
            return

        self._tabs = QTabWidget()
        # 先创建所有 tab 的空壳
        for idx, (_, mod_name, _, _) in enumerate(self._diff_pairs):
            tab, left_edit, right_edit, btn_prev, count_lbl, btn_next = self._create_empty_tab(mod_name, idx)
            self._tabs.addTab(tab, f"↔ {mod_name}")
            self._tab_edits.append((left_edit, right_edit))
            self._tab_diff_positions.append([])
            self._tab_current_idx.append(-1)
            self._tab_nav_widgets.append((btn_prev, count_lbl, btn_next))

        # 切换 tab 时懒加载 diff
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs)

        # 立即加载第一个 tab
        self._load_tab(0)

    def _create_empty_tab(self, mod_name: str, tab_index: int) -> tuple[QWidget, QTextEdit, QTextEdit, QPushButton, QLabel, QPushButton]:
        widget = QWidget()
        vlayout = QVBoxLayout(widget)
        vlayout.setContentsMargins(0, 0, 0, 0)
        vlayout.setSpacing(2)

        # 标签行：固定高度，不撑开
        label_layout = QHBoxLayout()
        label_layout.setContentsMargins(8, 0, 8, 0)
        lbl_left = QLabel("合并前")
        lbl_left.setStyleSheet("font-weight: bold;")
        lbl_right = QLabel(mod_name)
        lbl_right.setStyleSheet("font-weight: bold; color: #8f8;")
        label_layout.addWidget(lbl_left)
        label_layout.addWidget(lbl_right)

        label_layout.addStretch()

        # 导航按钮和计数标签
        btn_prev = QPushButton("▲ 上一个变化")
        btn_prev.setFixedWidth(100)
        count_label = QLabel("0 / 0")
        count_label.setFixedWidth(50)
        count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        count_label.setStyleSheet("font-size: 11px; color: #aaa;")
        btn_next = QPushButton("▼ 下一个变化")
        btn_next.setFixedWidth(100)
        label_layout.addWidget(btn_prev)
        label_layout.addWidget(count_label)
        label_layout.addWidget(btn_next)

        btn_prev.clicked.connect(lambda: self._goto_diff(tab_index, -1))
        btn_next.clicked.connect(lambda: self._goto_diff(tab_index, 1))

        # 编辑和重置按钮
        btn_edit = QPushButton("编辑合并结果")
        btn_edit.setFixedWidth(100)
        btn_edit.clicked.connect(lambda: self._edit_override(tab_index))
        label_layout.addWidget(btn_edit)

        btn_reset = QPushButton("重置为默认")
        btn_reset.setFixedWidth(80)
        btn_reset.clicked.connect(lambda: self._reset_override(tab_index))
        label_layout.addWidget(btn_reset)

        vlayout.addLayout(label_layout)

        # 左右对比区域
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_edit = QTextEdit()
        left_edit.setReadOnly(True)
        left_edit.setFont(_MONO_FONT)
        left_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

        right_edit = QTextEdit()
        right_edit.setReadOnly(True)
        right_edit.setFont(_MONO_FONT)
        right_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

        splitter.addWidget(left_edit)
        splitter.addWidget(right_edit)
        splitter.setSizes([500, 500])
        vlayout.addWidget(splitter, 1)  # stretch=1 让 splitter 占满剩余空间

        # 滚动同步
        syncing = [False]
        left_bar = left_edit.verticalScrollBar()
        right_bar = right_edit.verticalScrollBar()

        def sync_lr(val):
            if not syncing[0]:
                syncing[0] = True
                right_bar.setValue(val)
                syncing[0] = False

        def sync_rl(val):
            if not syncing[0]:
                syncing[0] = True
                left_bar.setValue(val)
                syncing[0] = False

        left_bar.valueChanged.connect(sync_lr)
        right_bar.valueChanged.connect(sync_rl)

        return widget, left_edit, right_edit, btn_prev, count_label, btn_next

    def _on_tab_changed(self, index: int):
        self._load_tab(index)

    def _load_tab(self, index: int):
        """懒加载：首次切换到某 tab 时才计算并填充 diff"""
        if index in self._loaded_tabs or index >= len(self._diff_pairs):
            return
        self._loaded_tabs.add(index)

        _, _, prev_text, curr_text = self._diff_pairs[index]
        left_edit, right_edit = self._tab_edits[index]

        left_lines = prev_text.splitlines()
        right_lines = curr_text.splitlines()

        left_html, right_html, diff_positions = _build_diff_html(left_lines, right_lines)
        left_edit.setHtml(left_html)
        right_edit.setHtml(right_html)

        self._tab_diff_positions[index] = diff_positions
        _, count_label, _ = self._tab_nav_widgets[index]
        total = len(diff_positions)
        if total > 0:
            self._tab_current_idx[index] = 0
            count_label.setText(f"1 / {total}")
        else:
            count_label.setText("0 / 0")

    def _goto_diff(self, tab_index: int, direction: int):
        """跳转到上一个(-1)或下一个(+1)变化块"""
        positions = self._tab_diff_positions[tab_index]
        if not positions:
            return

        total = len(positions)
        current = self._tab_current_idx[tab_index]
        new_idx = current + direction

        # 循环导航
        if new_idx < 0:
            new_idx = total - 1
        elif new_idx >= total:
            new_idx = 0

        self._tab_current_idx[tab_index] = new_idx

        # 更新计数标签
        _, count_label, _ = self._tab_nav_widgets[tab_index]
        count_label.setText(f"{new_idx + 1} / {total}")

        # 滚动到目标位置
        target_block_idx = positions[new_idx]
        left_edit, _ = self._tab_edits[tab_index]
        doc = left_edit.document()
        block = doc.findBlockByNumber(target_block_idx)
        if block.isValid():
            layout = doc.documentLayout()
            y = layout.blockBoundingRect(block).y()
            left_edit.verticalScrollBar().setValue(int(y))

    def _toggle_search(self):
        visible = not self._search_bar.isVisible()
        self._search_bar.setVisible(visible)
        if visible:
            self._search_bar.setFocus()
            self._search_bar.selectAll()

    def _close_search(self):
        self._search_bar.setVisible(False)

    def _find_next(self):
        text = self._search_bar.text()
        if not text:
            return
        idx = self._tabs.currentIndex()
        left_edit, right_edit = self._tab_edits[idx]
        if not left_edit.find(text):
            cursor = left_edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            left_edit.setTextCursor(cursor)
            left_edit.find(text)
        if not right_edit.find(text):
            cursor = right_edit.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            right_edit.setTextCursor(cursor)
            right_edit.find(text)

    def _find_prev(self):
        text = self._search_bar.text()
        if not text:
            return
        from PySide6.QtGui import QTextDocument
        idx = self._tabs.currentIndex()
        left_edit, right_edit = self._tab_edits[idx]
        left_edit.find(text, QTextDocument.FindFlag.FindBackward)
        right_edit.find(text, QTextDocument.FindFlag.FindBackward)

    def _edit_override(self, tab_index: int):
        """弹出编辑器编辑当前 tab 的合并结果"""
        mod_id, mod_name, _, curr_text = self._diff_pairs[tab_index]
        dlg = OverrideEditorDialog(
            curr_text, f"编辑合并结果 - {mod_name} - {self._rel_path}",
            parent=self
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            edited_text = dlg.get_text()
            override_file = MOD_OVERRIDES_DIR / mod_id / self._rel_path
            override_file.parent.mkdir(parents=True, exist_ok=True)
            override_file.write_text(edited_text, encoding="utf-8")
            self._refresh_all()

    def _reset_override(self, tab_index: int):
        """删除 override 文件并刷新"""
        mod_id, mod_name, _, _ = self._diff_pairs[tab_index]
        override_file = MOD_OVERRIDES_DIR / mod_id / self._rel_path
        if not override_file.exists():
            QMessageBox.information(self, "提示", f"{mod_name} 没有自定义覆盖")
            return
        override_file.unlink()
        # 清理空目录
        if override_file.parent.exists() and not any(override_file.parent.iterdir()):
            override_file.parent.rmdir()
        self._refresh_all()

    def _refresh_all(self):
        """重新预计算并刷新所有 tab"""
        current_tab = self._tabs.currentIndex()
        self._precompute_merge_states()
        self._loaded_tabs.clear()
        # 重新填充 diff 内容（tab 壳已存在，只需刷新内容）
        # 如果 diff_pairs 数量变了需要重建 tab，但通常 override 不影响数量
        for i in range(len(self._diff_pairs)):
            if i < len(self._tab_edits):
                self._tab_diff_positions[i] = []
                self._tab_current_idx[i] = -1
        if current_tab < len(self._diff_pairs):
            self._load_tab(current_tab)


class OverrideEditorDialog(QDialog):
    """合并结果编辑器，支持 Ctrl+F 搜索和 JSON 格式化"""

    def __init__(self, text: str, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(800, 600)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        self._build_ui(text)

    def _build_ui(self, text: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 错误提示条
        self._error_bar = QLabel()
        self._error_bar.setStyleSheet(
            "background-color: #5a1a1a; color: #f99; padding: 4px 8px; font-weight: bold;"
        )
        self._error_bar.setFixedHeight(28)
        self._error_bar.setVisible(False)
        layout.addWidget(self._error_bar)

        # 搜索栏（默认隐藏）
        self._search_bar = QLineEdit()
        self._search_bar.setPlaceholderText("搜索... (Enter=下一个, Shift+Enter=上一个, Esc=关闭)")
        self._search_bar.setVisible(False)
        self._search_bar.returnPressed.connect(self._find_next)
        layout.addWidget(self._search_bar)

        # 编辑区
        self._editor = CodeEditor()
        self._editor.setPlainText(text)
        layout.addWidget(self._editor, 1)

        # 快捷键
        QShortcut(QKeySequence("Ctrl+F"), self, self._toggle_search)
        QShortcut(QKeySequence("Shift+Return"), self._search_bar, self._find_prev)
        QShortcut(QKeySequence("Escape"), self._search_bar, self._close_search)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_format = QPushButton("格式化")
        btn_format.setStyleSheet("padding: 4px 16px;")
        btn_format.clicked.connect(self._format)
        btn_layout.addWidget(btn_format)

        btn_save = QPushButton("保存")
        btn_save.setStyleSheet("font-weight: bold; padding: 4px 16px;")
        btn_save.clicked.connect(self._save)
        btn_layout.addWidget(btn_save)

        btn_cancel = QPushButton("取消")
        btn_cancel.setStyleSheet("padding: 4px 16px;")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        layout.addLayout(btn_layout)

    def get_text(self) -> str:
        return self._editor.toPlainText()

    def _toggle_search(self):
        visible = not self._search_bar.isVisible()
        self._search_bar.setVisible(visible)
        if visible:
            self._search_bar.setFocus()
            self._search_bar.selectAll()

    def _close_search(self):
        self._search_bar.setVisible(False)
        self._editor.setFocus()

    def _find_next(self):
        text = self._search_bar.text()
        if text:
            self._editor.find(text)

    def _find_prev(self):
        text = self._search_bar.text()
        if text:
            from PySide6.QtGui import QTextDocument
            self._editor.find(text, QTextDocument.FindFlag.FindBackward)

    def _detect_error(self) -> tuple[int | None, str]:
        """检测 JSON 语法错误，返回 (行号, 错误消息) 或 (None, "")"""
        text = self._editor.toPlainText()
        cleaned = strip_trailing_commas(strip_js_comments(text))
        try:
            json.loads(cleaned)
            return None, ""
        except json.JSONDecodeError as e:
            return e.lineno, e.msg

    def _update_error_bar(self):
        line, msg = self._detect_error()
        if line is not None:
            self._error_bar.setText(f"第 {line} 行: {msg}")
            self._error_bar.setVisible(True)
            self._editor.highlight_line(line, scroll_to=False)
        else:
            self._error_bar.setVisible(False)
            self._editor.clear_highlights()

    def _format(self):
        text = self._editor.toPlainText()
        formatted = _format_with_comments(text)
        scroll_val = self._editor.verticalScrollBar().value()
        self._editor.setPlainText(formatted)
        self._editor.verticalScrollBar().setValue(scroll_val)
        self._update_error_bar()

    def _save(self):
        """验证 JSON 后接受对话框"""
        line, msg = self._detect_error()
        if line is not None:
            self._error_bar.setText(f"第 {line} 行: {msg}")
            self._error_bar.setVisible(True)
            self._editor.highlight_line(line)
            QMessageBox.warning(self, "JSON 语法错误",
                                f"第 {line} 行: {msg}\n请修正后再保存。")
            return
        self.accept()


def _format_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=4, sort_keys=True)


def _build_diff_html(left_lines: list[str], right_lines: list[str]) -> tuple[str, str, list[int]]:
    """用 SequenceMatcher 生成左右两侧的 HTML，一次性 setHtml 比逐行 cursor 快得多。
    返回 (左侧HTML, 右侧HTML, 变化块起始行索引列表)。"""
    sm = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=False)

    left_parts: list[str] = []
    right_parts: list[str] = []
    diff_positions: list[int] = []  # 每个变化块在 div 序列中的起始索引
    left_no = 0
    right_no = 0
    line_idx = 0  # 当前 div 索引（左右同步）

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                left_no += 1
                right_no += 1
                left_parts.append(_html_line(left_no, left_lines[i1 + k], _CLR_NORMAL_FG, None))
                right_parts.append(_html_line(right_no, right_lines[j1 + k], _CLR_NORMAL_FG, None))
                line_idx += 1

        elif tag == "replace":
            diff_positions.append(line_idx)
            max_len = max(i2 - i1, j2 - j1)
            for k in range(max_len):
                if i1 + k < i2:
                    left_no += 1
                    left_parts.append(_html_line(left_no, left_lines[i1 + k], _CLR_REP_OLD_FG, _CLR_REP_OLD_BG))
                else:
                    left_parts.append(_html_line(None, "", _CLR_REP_OLD_FG, _CLR_REP_OLD_BG))
                if j1 + k < j2:
                    right_no += 1
                    right_parts.append(_html_line(right_no, right_lines[j1 + k], _CLR_REP_NEW_FG, _CLR_REP_NEW_BG))
                else:
                    right_parts.append(_html_line(None, "", _CLR_REP_NEW_FG, _CLR_REP_NEW_BG))
                line_idx += 1

        elif tag == "delete":
            diff_positions.append(line_idx)
            for k in range(i2 - i1):
                left_no += 1
                left_parts.append(_html_line(left_no, left_lines[i1 + k], _CLR_DEL_FG, _CLR_DEL_BG))
                right_parts.append(_html_line(None, "", _CLR_DEL_FG, _CLR_DEL_BG))
                line_idx += 1

        elif tag == "insert":
            diff_positions.append(line_idx)
            for k in range(j2 - j1):
                left_parts.append(_html_line(None, "", _CLR_INS_FG, _CLR_INS_BG))
                right_no += 1
                right_parts.append(_html_line(right_no, right_lines[j1 + k], _CLR_INS_FG, _CLR_INS_BG))
                line_idx += 1

    left_html = _wrap_html("\n".join(left_parts))
    right_html = _wrap_html("\n".join(right_parts))
    return left_html, right_html, diff_positions


def _html_line(line_no: int | None, text: str, fg: str, bg: str | None) -> str:
    num = f"{line_no:4d}" if line_no is not None else "    "
    escaped = escape(text)
    style = f"color:{fg};"
    if bg:
        style += f"background-color:{bg};"
    return f'<div style="{style}"><span style="color:#888;">{num} │ </span>{escaped}</div>'


def _wrap_html(body: str) -> str:
    return (
        '<html><body style="white-space:pre; font-family:Consolas,monospace; font-size:10pt; margin:0; padding:0;">'
        f'{body}'
        '</body></html>'
    )
