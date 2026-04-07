"""
Diff 对比窗口 - 逐级展示游戏本体经各 Mod 覆盖后的行级差异
左侧只读 CodeEditor 显示合并前状态，右侧可编辑 CodeEditor 显示合并结果
"""
import copy
import difflib
import json
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QTextEdit, QLabel, QSplitter, QWidget, QPushButton,
    QLineEdit, QMessageBox
)
from PySide6.QtGui import (
    QKeySequence, QShortcut, QColor, QTextCursor, QTextFormat
)
from PySide6.QtCore import Qt

from ..config import SCHEMA_DIR, MOD_OVERRIDES_DIR
from ..core.json_parser import load_json, strip_js_comments, strip_trailing_commas
from ..core.merger import deep_merge, classify_json, compute_mod_delta, _DELETED
from ..core.schema_loader import load_schemas, resolve_schema, get_schema_root_key
from ..core.profiler import profile
from .json_editor import CodeEditor, _format_with_comments

# diff 行高亮背景色
_CLR_LEFT_CHANGE = QColor(80, 30, 30)     # 红底（被修改/删除的行）
_CLR_RIGHT_CHANGE = QColor(30, 80, 30)    # 绿底（新增/修改后的行）
_CLR_RIGHT_CONFLICT = QColor(120, 80, 20) # 橙底（冲突：此行被多个 mod 修改）


def _apply_extra_selections(editor: CodeEditor,
                            highlights: list[tuple[int, QColor]]):
    """对 CodeEditor 应用行级背景高亮"""
    selections = []
    for block_no, color in highlights:
        block = editor.document().findBlockByNumber(block_no)
        if not block.isValid():
            continue
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(color)
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        sel.cursor = QTextCursor(block)
        sel.cursor.clearSelection()
        selections.append(sel)
    editor.setExtraSelections(selections)


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

        # 预计算各级合并状态的 JSON 文本
        self._base_text: str = ""  # 游戏本体原始文本（所有 mod 之前）
        self._diff_pairs: list[tuple[str, str, str, str]] = []  # (mod_id, mod_name, prev_text, curr_text)
        self._precompute_merge_states()

        # 懒加载标记
        self._loaded_tabs: set[int] = set()
        # 各 tab 的左右 CodeEditor 引用
        self._tab_edits: list[tuple[CodeEditor, CodeEditor]] = []
        # 各 tab 的错误提示条
        self._tab_error_bars: list[QLabel] = []
        # 导航相关
        self._tab_diff_positions: list[list[int]] = []
        self._tab_current_idx: list[int] = []
        self._tab_nav_widgets: list[tuple[QPushButton, QLabel, QPushButton]] = []

        self.setWindowTitle(f"Diff 对比 - {rel_path}")
        self.resize(1000, 650)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        self._build_ui()

    @profile
    def _precompute_merge_states(self):
        """预计算逐级合并的 JSON 文本对，不涉及 UI 操作"""
        self._diff_pairs.clear()
        base_file = self._game_config_path / self._rel_path
        base_data = load_json(base_file) if base_file.exists() else {}

        self._base_text = _format_json(base_data)

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
        for idx, (_, mod_name, _, _) in enumerate(self._diff_pairs):
            tab, left_edit, right_edit, error_bar, btn_prev, count_lbl, btn_next = (
                self._create_empty_tab(mod_name, idx))
            self._tabs.addTab(tab, f"↔ {mod_name}")
            self._tab_edits.append((left_edit, right_edit))
            self._tab_error_bars.append(error_bar)
            self._tab_diff_positions.append([])
            self._tab_current_idx.append(-1)
            self._tab_nav_widgets.append((btn_prev, count_lbl, btn_next))

        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs)

        # 立即加载第一个 tab
        self._load_tab(0)

    def _create_empty_tab(self, mod_name: str, tab_index: int):
        widget = QWidget()
        vlayout = QVBoxLayout(widget)
        vlayout.setContentsMargins(0, 0, 0, 0)
        vlayout.setSpacing(2)

        # 错误提示条
        error_bar = QLabel()
        error_bar.setStyleSheet(
            "background-color: #5a1a1a; color: #f99; padding: 4px 8px; font-weight: bold;"
        )
        error_bar.setFixedHeight(28)
        error_bar.setVisible(False)
        vlayout.addWidget(error_bar)

        # 标签行
        label_layout = QHBoxLayout()
        label_layout.setContentsMargins(8, 0, 8, 0)
        lbl_left = QLabel("合并前（只读）")
        lbl_left.setStyleSheet("font-weight: bold;")
        lbl_right = QLabel(f"{mod_name}（可编辑）")
        lbl_right.setStyleSheet("font-weight: bold; color: #8f8;")
        label_layout.addWidget(lbl_left)
        label_layout.addWidget(lbl_right)
        label_layout.addStretch()

        # 导航按钮
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

        # 格式化、保存、重置按钮
        btn_format = QPushButton("格式化")
        btn_format.setFixedWidth(60)
        btn_format.clicked.connect(lambda: self._format_override(tab_index))
        label_layout.addWidget(btn_format)

        btn_save = QPushButton("保存")
        btn_save.setFixedWidth(50)
        btn_save.setStyleSheet("font-weight: bold;")
        btn_save.clicked.connect(lambda: self._save_override(tab_index))
        label_layout.addWidget(btn_save)

        btn_reset = QPushButton("重置为默认")
        btn_reset.setFixedWidth(80)
        btn_reset.clicked.connect(lambda: self._reset_override(tab_index))
        label_layout.addWidget(btn_reset)

        vlayout.addLayout(label_layout)

        # 左右对比区域
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_edit = CodeEditor()
        left_edit.setReadOnly(True)

        right_edit = CodeEditor()
        # 可编辑（默认）

        splitter.addWidget(left_edit)
        splitter.addWidget(right_edit)
        splitter.setSizes([500, 500])
        vlayout.addWidget(splitter, 1)

        # 滚动同步（垂直 + 水平）
        syncing = [False]

        def sync_vertical_lr(val):
            if syncing[0]:
                return
            syncing[0] = True
            src_max = left_edit.verticalScrollBar().maximum()
            dst_max = right_edit.verticalScrollBar().maximum()
            if src_max > 0 and dst_max > 0:
                right_edit.verticalScrollBar().setValue(int(val / src_max * dst_max))
            syncing[0] = False

        def sync_vertical_rl(val):
            if syncing[0]:
                return
            syncing[0] = True
            src_max = right_edit.verticalScrollBar().maximum()
            dst_max = left_edit.verticalScrollBar().maximum()
            if src_max > 0 and dst_max > 0:
                left_edit.verticalScrollBar().setValue(int(val / src_max * dst_max))
            syncing[0] = False

        def sync_horizontal_lr(val):
            if not syncing[0]:
                syncing[0] = True
                right_edit.horizontalScrollBar().setValue(val)
                syncing[0] = False

        def sync_horizontal_rl(val):
            if not syncing[0]:
                syncing[0] = True
                left_edit.horizontalScrollBar().setValue(val)
                syncing[0] = False

        left_edit.verticalScrollBar().valueChanged.connect(sync_vertical_lr)
        right_edit.verticalScrollBar().valueChanged.connect(sync_vertical_rl)
        left_edit.horizontalScrollBar().valueChanged.connect(sync_horizontal_lr)
        right_edit.horizontalScrollBar().valueChanged.connect(sync_horizontal_rl)

        return widget, left_edit, right_edit, error_bar, btn_prev, count_label, btn_next

    def _on_tab_changed(self, index: int):
        self._load_tab(index)

    def _load_tab(self, index: int):
        """懒加载：首次切换到某 tab 时才填充文本并计算高亮"""
        if index in self._loaded_tabs or index >= len(self._diff_pairs):
            return
        self._loaded_tabs.add(index)

        _, _, prev_text, curr_text = self._diff_pairs[index]
        left_edit, right_edit = self._tab_edits[index]
        left_edit.setPlainText(prev_text)
        right_edit.setPlainText(curr_text)

        self._compute_and_apply_highlights(index)

    def _compute_and_apply_highlights(self, tab_index: int):
        """对比左右文本，用 ExtraSelections 标记差异行。
        如果某行被之前的 mod 也修改过，使用冲突色（橙底）。"""
        left_edit, right_edit = self._tab_edits[tab_index]
        left_lines = left_edit.toPlainText().splitlines()
        right_lines = right_edit.toPlainText().splitlines()

        # 计算 prev vs curr 的 opcodes
        sm = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=False)
        opcodes = sm.get_opcodes()

        # 计算哪些 prev 行已被之前的 mod 修改过（相对于本体）
        prev_changed_lines: set[int] = set()
        base_lines = self._base_text.splitlines()
        if left_lines != base_lines:
            base_sm = difflib.SequenceMatcher(None, base_lines, left_lines, autojunk=False)
            for tag, _, _, j1, j2 in base_sm.get_opcodes():
                if tag in ("replace", "insert"):
                    for j in range(j1, j2):
                        prev_changed_lines.add(j)

        left_highlights: list[tuple[int, QColor]] = []
        right_highlights: list[tuple[int, QColor]] = []
        diff_positions_left: list[int] = []

        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                continue
            diff_positions_left.append(i1)

            if tag in ("replace", "delete"):
                for i in range(i1, i2):
                    left_highlights.append((i, _CLR_LEFT_CHANGE))

            if tag in ("replace", "insert"):
                # 判断是否冲突：replace 且左侧行已被之前的 mod 修改过
                is_conflict = (
                    tag == "replace"
                    and prev_changed_lines
                    and any(i in prev_changed_lines for i in range(i1, i2))
                )
                color = _CLR_RIGHT_CONFLICT if is_conflict else _CLR_RIGHT_CHANGE
                for j in range(j1, j2):
                    right_highlights.append((j, color))

        _apply_extra_selections(left_edit, left_highlights)
        _apply_extra_selections(right_edit, right_highlights)

        self._tab_diff_positions[tab_index] = diff_positions_left
        _, count_label, _ = self._tab_nav_widgets[tab_index]
        total = len(diff_positions_left)
        if total > 0:
            self._tab_current_idx[tab_index] = 0
            count_label.setText(f"1 / {total}")
        else:
            self._tab_current_idx[tab_index] = -1
            count_label.setText("0 / 0")

    def _goto_diff(self, tab_index: int, direction: int):
        """跳转到上一个(-1)或下一个(+1)变化块"""
        positions = self._tab_diff_positions[tab_index]
        if not positions:
            return

        total = len(positions)
        current = self._tab_current_idx[tab_index]
        new_idx = current + direction
        if new_idx < 0:
            new_idx = total - 1
        elif new_idx >= total:
            new_idx = 0

        self._tab_current_idx[tab_index] = new_idx

        _, count_label, _ = self._tab_nav_widgets[tab_index]
        count_label.setText(f"{new_idx + 1} / {total}")

        target_block = positions[new_idx]
        left_edit, _ = self._tab_edits[tab_index]
        block = left_edit.document().findBlockByNumber(target_block)
        if block.isValid():
            cursor = QTextCursor(block)
            left_edit.setTextCursor(cursor)
            left_edit.centerCursor()

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

    def _save_override(self, tab_index: int):
        """验证 JSON 后保存为 override 文件"""
        right_edit = self._tab_edits[tab_index][1]
        text = right_edit.toPlainText()
        error_bar = self._tab_error_bars[tab_index]

        # JSON 验证
        cleaned = strip_trailing_commas(strip_js_comments(text))
        try:
            json.loads(cleaned)
        except json.JSONDecodeError as e:
            error_bar.setText(f"⚠ 第 {e.lineno} 行: {e.msg}")
            error_bar.setVisible(True)
            right_edit.highlight_line(e.lineno)
            QMessageBox.warning(self, "JSON 语法错误",
                                f"第 {e.lineno} 行: {e.msg}\n请修正后再保存。")
            return

        error_bar.setVisible(False)
        right_edit.clear_highlights()

        mod_id = self._diff_pairs[tab_index][0]
        override_file = MOD_OVERRIDES_DIR / mod_id / self._rel_path
        override_file.parent.mkdir(parents=True, exist_ok=True)
        override_file.write_text(text, encoding="utf-8")

        self._refresh_all()

    def _format_override(self, tab_index: int):
        """格式化右侧文本并更新高亮"""
        right_edit = self._tab_edits[tab_index][1]
        text = right_edit.toPlainText()
        formatted = _format_with_comments(text)
        scroll_val = right_edit.verticalScrollBar().value()
        right_edit.setPlainText(formatted)
        right_edit.verticalScrollBar().setValue(scroll_val)
        self._compute_and_apply_highlights(tab_index)

    def _reset_override(self, tab_index: int):
        """删除 override 文件并刷新"""
        mod_id, mod_name, _, _ = self._diff_pairs[tab_index]
        override_file = MOD_OVERRIDES_DIR / mod_id / self._rel_path
        if not override_file.exists():
            QMessageBox.information(self, "提示", f"{mod_name} 没有自定义覆盖")
            return
        override_file.unlink()
        if override_file.parent.exists() and not any(override_file.parent.iterdir()):
            override_file.parent.rmdir()
        self._refresh_all()

    def _refresh_all(self):
        """重新预计算并刷新所有 tab"""
        current_tab = self._tabs.currentIndex()
        self._precompute_merge_states()
        self._loaded_tabs.clear()
        for i in range(len(self._diff_pairs)):
            if i < len(self._tab_edits):
                self._tab_diff_positions[i] = []
                self._tab_current_idx[i] = -1
                self._tab_error_bars[i].setVisible(False)
        if current_tab < len(self._diff_pairs):
            self._load_tab(current_tab)


def _format_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=4, sort_keys=True)
