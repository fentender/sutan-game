"""
Diff 对比窗口 - 逐级展示游戏本体经各 Mod 覆盖后的行级差异
左侧只读 CodeEditor 显示合并前状态，右侧可编辑 CodeEditor 显示合并结果
"""
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import SCHEMA_DIR
from ..core.diff_formatter import (
    build_padded_texts,
    diff_opcodes,
)
from ..core.json_parser import _pairs_hook, clean_json_text
from ..core.json_store import JsonStore
from ..core.merge_cache import MergeCache
from ..core.profiler import profile
from ..core.types import ChangeKind
from .json_editor import CodeEditor, _format_with_comments

# diff 行高亮背景色
_CLR_LEFT_CHANGE = QColor(80, 30, 30)     # 红底（删除的行）
_CLR_RIGHT_CHANGE = QColor(30, 80, 30)    # 绿底（新增的行）
_CLR_CHANGED = QColor(80, 75, 20)         # 浅黄底（修改的行，无冲突）
_CLR_CONFLICT = QColor(140, 90, 20)       # 橙底/深黄底（冲突：多 mod 修改同一字段）
_CLR_LEFT_CONFLICT = QColor(120, 80, 20)  # 橙底（旧：文本匹配 fallback 用）
_CLR_RIGHT_CONFLICT = QColor(120, 80, 20) # 橙底（旧：文本匹配 fallback 用）
_CLR_PADDING = QColor(30, 30, 30)         # 填充行背景（略深于编辑器背景）
_CLR_SEARCH = QColor(40, 100, 180)        # 搜索匹配高亮（亮蓝）
_CLR_OVERRIDE = QColor(50, 80, 120)       # 蓝底（用户手动覆写）
_CLR_LEFT_OVERRIDE = QColor(50, 70, 110)  # 蓝底（覆写前）

# 同一行出现多种高亮时，按优先级保留最高的（QColor 不可哈希，用 rgb 整数做 key）
_COLOR_PRIORITY: dict[int, int] = {
    _CLR_PADDING.rgb(): 1,
    _CLR_LEFT_CHANGE.rgb(): 2,
    _CLR_RIGHT_CHANGE.rgb(): 2,
    _CLR_CHANGED.rgb(): 2,
    _CLR_LEFT_CONFLICT.rgb(): 3,
    _CLR_RIGHT_CONFLICT.rgb(): 3,
    _CLR_CONFLICT.rgb(): 3,
    _CLR_LEFT_OVERRIDE.rgb(): 4,
    _CLR_OVERRIDE.rgb(): 4,
}


def _get_real_text(editor: CodeEditor, line_map: list[int | None]) -> str:
    """从编辑器中提取非填充行文本（根据 line_map 跳过填充行）"""
    lines: list[str] = []
    block = editor.document().begin()
    idx = 0
    while block.isValid():
        if idx >= len(line_map) or line_map[idx] is not None:
            lines.append(block.text())
        block = block.next()
        idx += 1
    return '\n'.join(lines)


@profile
def _apply_extra_selections(editor: CodeEditor,
                            highlights: list[tuple[int, QColor]]) -> None:
    """对 CodeEditor 应用行级背景高亮。同一行多种颜色时按优先级保留最高的。"""
    best: dict[int, tuple[int, QColor]] = {}
    for block_no, color in highlights:
        prio = _COLOR_PRIORITY.get(color.rgb(), 0)
        prev = best.get(block_no)
        if prev is None or prio > prev[0]:
            best[block_no] = (prio, color)

    selections: list[QTextEdit.ExtraSelection] = []
    for block_no in sorted(best):
        block = editor.document().findBlockByNumber(block_no)
        if not block.isValid():
            continue
        _, color = best[block_no]
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(color)
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        sel.cursor = QTextCursor(block)
        sel.cursor.clearSelection()
        selections.append(sel)
    editor.setExtraSelections(selections)


class DiffDialog(QDialog):
    """文件 Diff 对比窗口"""

    def __init__(self, rel_path: str,
                 mod_configs: list[tuple[str, str, Path]],
                 array_warnings: list[str] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rel_path = rel_path
        self._mod_configs = mod_configs
        self._array_warnings = array_warnings or []

        # 预计算各级合并状态的 JSON 文本
        self._base_text: str = ""
        self._diff_pairs: list[tuple[str, str, str, str]] = []
        self._precomputed_highlights: list[tuple[
            list[tuple[int, QColor]], list[tuple[int, QColor]], list[int]
        ]] = []
        # 各 tab 的行级 ChangeKind 对（结构化高亮）
        self._line_kinds_pairs: list[tuple[
            list[ChangeKind | None], list[ChangeKind | None]
        ]] = []
        # 各 tab 是否包含冲突行
        self._tab_has_conflict: list[bool] = []
        # 各 tab 的原始右侧文本（无填充行），用于 override delta 计算
        self._tab_original_texts: list[str] = []
        # 文件级是否有数组合并警告
        self._has_array_warning = len(self._array_warnings) > 0
        self._precompute_merge_states()

        # 懒加载标记
        self._loaded_tabs: set[int] = set()
        # 各 tab 的左右 CodeEditor 引用
        self._tab_edits: list[tuple[CodeEditor, CodeEditor]] = []
        # 各 tab 的搜索栏容器和输入框
        self._tab_search_bars: list[tuple[QWidget, QWidget]] = []
        self._tab_search_inputs: list[tuple[QLineEdit, QLineEdit]] = []
        # 各 tab 的错误提示条
        self._tab_error_bars: list[QLabel] = []
        # 导航相关
        self._tab_diff_positions: list[list[int]] = []
        self._tab_current_idx: list[int] = []
        self._tab_nav_widgets: list[tuple[QPushButton, QLabel, QPushButton]] = []
        # 各 tab 的行映射表（替代 block.setUserData）
        self._tab_line_maps: list[tuple[list[int | None], list[int | None]]] = []

        self.setWindowTitle(f"Diff 对比 - {rel_path}")
        self.resize(1000, 650)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        self._build_ui()

    @profile
    def _precompute_merge_states(self) -> None:
        """预计算逐级合并的 JSON 文本对 + 行级 diff 高亮，不涉及 UI 操作"""
        self._diff_pairs.clear()
        self._precomputed_highlights.clear()
        self._line_kinds_pairs.clear()
        self._tab_original_texts.clear()

        cache = MergeCache.instance()
        state = cache.get(self._rel_path, self._mod_configs, SCHEMA_DIR)

        store = JsonStore.instance()
        base_data = store.get_base(self._rel_path)
        from ..core.json_parser import format_json
        self._base_text = format_json(base_data)

        for step in state.steps:
            prev_text = '\n'.join(step.left_lines)
            curr_text = '\n'.join(step.right_lines)

            self._diff_pairs.append((step.mod_id, step.mod_name, prev_text, curr_text))
            self._line_kinds_pairs.append((step.left_kinds, step.right_kinds))

            # 缓存原始右侧文本（无填充行），供 override delta 计算
            original_right = '\n'.join(
                line for line, rk in zip(step.right_lines, step.right_kinds)
                if rk is not None
            )
            self._tab_original_texts.append(original_right)

            # 从 line_kinds 计算高亮
            left_highlights: list[tuple[int, QColor]] = []
            right_highlights: list[tuple[int, QColor]] = []
            diff_positions: list[int] = []
            has_conflict = False
            prev_is_change = False

            for i, (lk, rk) in enumerate(zip(step.left_kinds, step.right_kinds, strict=True)):
                is_change = False

                if lk is not None:
                    if lk.is_deleted or lk.is_changed:
                        if lk.is_override:
                            color = _CLR_LEFT_OVERRIDE
                        elif lk.is_multi_mod:
                            color = _CLR_CONFLICT
                        else:
                            color = _CLR_LEFT_CHANGE
                        left_highlights.append((i, color))
                        is_change = True
                        if lk.is_multi_mod:
                            has_conflict = True
                elif rk is not None and not rk.is_origin:
                    left_highlights.append((i, _CLR_LEFT_CHANGE))

                if rk is not None:
                    if rk.is_added or rk.is_changed:
                        if rk.is_override:
                            color = _CLR_OVERRIDE
                        elif rk.is_multi_mod:
                            color = _CLR_CONFLICT
                        else:
                            color = _CLR_RIGHT_CHANGE
                        right_highlights.append((i, color))
                        is_change = True
                        if rk.is_multi_mod:
                            has_conflict = True
                elif lk is not None and not lk.is_origin:
                    right_highlights.append((i, _CLR_RIGHT_CHANGE))

                # 只记录每个连续变化块的第一行
                if is_change and not prev_is_change:
                    diff_positions.append(i)
                prev_is_change = is_change

            self._precomputed_highlights.append(
                (left_highlights, right_highlights, diff_positions)
            )
            self._tab_has_conflict.append(has_conflict)

        self._merge_warnings = state.warnings

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        path_label = QLabel(self._rel_path)
        path_label.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px;")
        path_label.setFixedHeight(24)
        layout.addWidget(path_label)

        self._warn_bar = QLabel()
        self._warn_bar.setWordWrap(True)
        self._warn_bar.setStyleSheet(
            "background-color: #3a3010; color: #eec864; padding: 4px 8px; font-size: 12px;"
        )
        self._warn_bar.setVisible(False)
        layout.addWidget(self._warn_bar)
        # 暂时关闭顶部提示条
        # self._update_warn_bar()

        QShortcut(QKeySequence("Ctrl+F"), self, self._toggle_search)

        if not self._diff_pairs:
            placeholder = QLabel("没有 Mod 修改此文件")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(placeholder)
            return

        self._tabs = QTabWidget()
        for idx, (_, mod_name, _, _) in enumerate(self._diff_pairs):
            (tab, left_edit, right_edit, error_bar, btn_prev, count_lbl, btn_next,
             left_search_w, left_search_in, right_search_w, right_search_in) = (
                self._create_empty_tab(mod_name, idx))
            self._tabs.addTab(tab, f"↔ {mod_name}")
            if self._tab_has_conflict[idx]:
                self._tabs.tabBar().setTabTextColor(idx, QColor(255, 180, 50))
            elif self._has_array_warning:
                self._tabs.tabBar().setTabTextColor(idx, QColor(100, 180, 255))
            self._tab_edits.append((left_edit, right_edit))
            self._tab_search_bars.append((left_search_w, right_search_w))
            self._tab_search_inputs.append((left_search_in, right_search_in))
            self._tab_error_bars.append(error_bar)
            self._tab_diff_positions.append([])
            self._tab_current_idx.append(-1)
            self._tab_nav_widgets.append((btn_prev, count_lbl, btn_next))
            self._tab_line_maps.append(([], []))

        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs)

        self._load_tab(0)

    def _create_empty_tab(self, mod_name: str, tab_index: int) -> tuple[
        QWidget, CodeEditor, CodeEditor, QLabel, QPushButton, QLabel, QPushButton,
        QWidget, QLineEdit, QWidget, QLineEdit,
    ]:
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
        btn_prev.setAutoDefault(False)
        count_label = QLabel("0 / 0")
        count_label.setFixedWidth(50)
        count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        count_label.setStyleSheet("font-size: 11px; color: #aaa;")
        btn_next = QPushButton("▼ 下一个变化")
        btn_next.setFixedWidth(100)
        btn_next.setAutoDefault(False)
        label_layout.addWidget(btn_prev)
        label_layout.addWidget(count_label)
        label_layout.addWidget(btn_next)

        btn_prev.clicked.connect(lambda: self._goto_diff(tab_index, -1))
        btn_next.clicked.connect(lambda: self._goto_diff(tab_index, 1))

        # 格式化、保存、重置按钮
        btn_format = QPushButton("格式化")
        btn_format.setFixedWidth(60)
        btn_format.setAutoDefault(False)
        btn_format.clicked.connect(lambda: self._format_override(tab_index))
        label_layout.addWidget(btn_format)

        btn_save = QPushButton("保存")
        btn_save.setFixedWidth(50)
        btn_save.setAutoDefault(False)
        btn_save.setStyleSheet("font-weight: bold;")
        btn_save.clicked.connect(lambda: self._save_override(tab_index))
        label_layout.addWidget(btn_save)

        btn_reset = QPushButton("重置为默认")
        btn_reset.setFixedWidth(80)
        btn_reset.setAutoDefault(False)
        btn_reset.clicked.connect(lambda: self._reset_override(tab_index))
        label_layout.addWidget(btn_reset)

        vlayout.addLayout(label_layout)

        # 左右对比区域
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧容器：搜索栏 + 编辑器
        left_container = QWidget()
        left_vlayout = QVBoxLayout(left_container)
        left_vlayout.setContentsMargins(0, 0, 0, 0)
        left_vlayout.setSpacing(0)

        left_search_widget = QWidget()
        left_search_layout = QHBoxLayout(left_search_widget)
        left_search_layout.setContentsMargins(2, 2, 2, 2)
        left_search_layout.setSpacing(2)
        left_search_input = QLineEdit()
        left_search_input.setPlaceholderText("搜索...")
        left_search_prev = QPushButton("▲")
        left_search_prev.setFixedWidth(28)
        left_search_prev.setAutoDefault(False)
        left_search_next = QPushButton("▼")
        left_search_next.setFixedWidth(28)
        left_search_next.setAutoDefault(False)
        left_search_close = QPushButton("✕")
        left_search_close.setFixedWidth(28)
        left_search_close.setAutoDefault(False)
        left_search_layout.addWidget(left_search_input, 1)
        left_search_layout.addWidget(left_search_prev)
        left_search_layout.addWidget(left_search_next)
        left_search_layout.addWidget(left_search_close)
        left_search_widget.setVisible(False)
        left_vlayout.addWidget(left_search_widget)

        left_edit = CodeEditor()
        left_edit.setReadOnly(True)
        left_vlayout.addWidget(left_edit, 1)

        # 右侧容器：搜索栏 + 编辑器
        right_container = QWidget()
        right_vlayout = QVBoxLayout(right_container)
        right_vlayout.setContentsMargins(0, 0, 0, 0)
        right_vlayout.setSpacing(0)

        right_search_widget = QWidget()
        right_search_layout = QHBoxLayout(right_search_widget)
        right_search_layout.setContentsMargins(2, 2, 2, 2)
        right_search_layout.setSpacing(2)
        right_search_input = QLineEdit()
        right_search_input.setPlaceholderText("搜索...")
        right_search_prev = QPushButton("▲")
        right_search_prev.setFixedWidth(28)
        right_search_prev.setAutoDefault(False)
        right_search_next = QPushButton("▼")
        right_search_next.setFixedWidth(28)
        right_search_next.setAutoDefault(False)
        right_search_close = QPushButton("✕")
        right_search_close.setFixedWidth(28)
        right_search_close.setAutoDefault(False)
        right_search_layout.addWidget(right_search_input, 1)
        right_search_layout.addWidget(right_search_prev)
        right_search_layout.addWidget(right_search_next)
        right_search_layout.addWidget(right_search_close)
        right_search_widget.setVisible(False)
        right_vlayout.addWidget(right_search_widget)

        right_edit = CodeEditor()
        right_vlayout.addWidget(right_edit, 1)

        # 搜索信号连接
        left_search_input.returnPressed.connect(
            lambda: self._find_in_editor(left_edit, left_search_input.text()))
        left_search_next.clicked.connect(
            lambda: self._find_in_editor(left_edit, left_search_input.text()))
        left_search_prev.clicked.connect(
            lambda: self._find_in_editor(left_edit, left_search_input.text(), backward=True))
        left_search_close.clicked.connect(
            lambda: self._close_search_bar(left_search_widget, left_edit))
        QShortcut(QKeySequence("Shift+Return"), left_search_input,
                  lambda: self._find_in_editor(left_edit, left_search_input.text(), backward=True))
        QShortcut(QKeySequence("Escape"), left_search_input,
                  lambda: self._close_search_bar(left_search_widget, left_edit))

        right_search_input.returnPressed.connect(
            lambda: self._find_in_editor(right_edit, right_search_input.text()))
        right_search_next.clicked.connect(
            lambda: self._find_in_editor(right_edit, right_search_input.text()))
        right_search_prev.clicked.connect(
            lambda: self._find_in_editor(right_edit, right_search_input.text(), backward=True))
        right_search_close.clicked.connect(
            lambda: self._close_search_bar(right_search_widget, right_edit))
        QShortcut(QKeySequence("Shift+Return"), right_search_input,
                  lambda: self._find_in_editor(right_edit, right_search_input.text(), backward=True))
        QShortcut(QKeySequence("Escape"), right_search_input,
                  lambda: self._close_search_bar(right_search_widget, right_edit))

        splitter.addWidget(left_container)
        splitter.addWidget(right_container)
        splitter.setSizes([500, 500])
        vlayout.addWidget(splitter, 1)

        # 滚动同步（垂直 + 水平）
        syncing = [False]

        def sync_vertical_lr(val: int) -> None:
            if syncing[0]:
                return
            syncing[0] = True
            right_edit.verticalScrollBar().setValue(val)
            syncing[0] = False

        def sync_vertical_rl(val: int) -> None:
            if syncing[0]:
                return
            syncing[0] = True
            left_edit.verticalScrollBar().setValue(val)
            syncing[0] = False

        def sync_horizontal_lr(val: int) -> None:
            if not syncing[0]:
                syncing[0] = True
                right_edit.horizontalScrollBar().setValue(val)
                syncing[0] = False

        def sync_horizontal_rl(val: int) -> None:
            if not syncing[0]:
                syncing[0] = True
                left_edit.horizontalScrollBar().setValue(val)
                syncing[0] = False

        left_edit.verticalScrollBar().valueChanged.connect(sync_vertical_lr)
        right_edit.verticalScrollBar().valueChanged.connect(sync_vertical_rl)
        left_edit.horizontalScrollBar().valueChanged.connect(sync_horizontal_lr)
        right_edit.horizontalScrollBar().valueChanged.connect(sync_horizontal_rl)

        return (widget, left_edit, right_edit, error_bar, btn_prev, count_label, btn_next,
                left_search_widget, left_search_input, right_search_widget, right_search_input)

    def _on_tab_changed(self, index: int) -> None:
        self._load_tab(index)

    @profile
    def _load_tab(self, index: int) -> None:
        """懒加载：首次切换到某 tab 时设置文本并应用高亮。

        format_delta_json 已产出预对齐文本（含填充行），无需 build_padded_texts。
        """
        if index in self._loaded_tabs or index >= len(self._diff_pairs):
            return
        self._loaded_tabs.add(index)

        _, _, prev_text, curr_text = self._diff_pairs[index]
        left_edit, right_edit = self._tab_edits[index]

        left_kinds, right_kinds = self._line_kinds_pairs[index]
        left_map: list[int | None] = []
        left_real = 0
        for k in left_kinds:
            if k is not None:
                left_map.append(left_real)
                left_real += 1
            else:
                left_map.append(None)
        right_map: list[int | None] = []
        right_real = 0
        for k in right_kinds:
            if k is not None:
                right_map.append(right_real)
                right_real += 1
            else:
                right_map.append(None)

        # 存储行映射并设置到编辑器（用 Python list 查表代替 setUserData）
        self._tab_line_maps[index] = (left_map, right_map)
        left_edit._diff_line_map = left_map
        right_edit._diff_line_map = right_map

        left_edit.setUpdatesEnabled(False)
        right_edit.setUpdatesEnabled(False)
        left_edit.blockSignals(True)
        left_edit.document().blockSignals(True)
        right_edit.blockSignals(True)
        right_edit.document().blockSignals(True)
        try:
            left_edit.setPlainText(prev_text)
            right_edit.setPlainText(curr_text)

            self._apply_precomputed_highlights(index)
        finally:
            left_edit.document().blockSignals(False)
            left_edit.blockSignals(False)
            right_edit.document().blockSignals(False)
            right_edit.blockSignals(False)
            left_edit.setUpdatesEnabled(True)
            right_edit.setUpdatesEnabled(True)

    def _apply_precomputed_highlights(self, tab_index: int) -> None:
        """应用预计算的高亮数据（结构化对齐模式，行号已是绝对值无需翻译）"""
        left_edit, right_edit = self._tab_edits[tab_index]
        left_hl, right_hl, diff_positions = self._precomputed_highlights[tab_index]

        _apply_extra_selections(left_edit, left_hl)
        _apply_extra_selections(right_edit, right_hl)

        self._tab_diff_positions[tab_index] = diff_positions
        _, count_label, _ = self._tab_nav_widgets[tab_index]
        total = len(diff_positions)
        if total > 0:
            self._tab_current_idx[tab_index] = 0
            count_label.setText(f"1 / {total}")
        else:
            self._tab_current_idx[tab_index] = -1
            count_label.setText("0 / 0")

    @profile
    def _compute_and_apply_highlights(self, tab_index: int,
                                       left_map: list[int | None],
                                       right_map: list[int | None]) -> None:
        """对比左右真实文本，计算差异高亮并翻译到填充后 block 号。
        仅用于格式化等需要实时重算的场景。"""
        left_edit, right_edit = self._tab_edits[tab_index]

        left_real = [left_edit.document().findBlockByNumber(i).text()
                     for i, r in enumerate(left_map) if r is not None]
        right_real = [right_edit.document().findBlockByNumber(i).text()
                      for i, r in enumerate(right_map) if r is not None]

        left_o2p: dict[int, int] = {}
        right_o2p: dict[int, int] = {}
        for padded_idx, real_line in enumerate(left_map):
            if real_line is not None:
                left_o2p[real_line] = padded_idx
        for padded_idx, real_line in enumerate(right_map):
            if real_line is not None:
                right_o2p[real_line] = padded_idx

        opcodes = diff_opcodes(left_real, right_real)

        # base vs left — 判断哪些 prev 行已被之前的 mod 修改过
        prev_changed_lines: set[int] = set()
        base_lines = self._base_text.splitlines()
        if left_real != base_lines:
            base_opcodes = diff_opcodes(base_lines, left_real)
            for tag, _, _, j1, j2 in base_opcodes:
                if tag in ("replace", "insert"):
                    for j in range(j1, j2):
                        prev_changed_lines.add(j)

        left_highlights: list[tuple[int, QColor]] = []
        right_highlights: list[tuple[int, QColor]] = []
        diff_positions: list[int] = []

        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "equal":
                continue
            if i1 in left_o2p:
                diff_positions.append(left_o2p[i1])

            is_conflict = (
                tag == "replace"
                and prev_changed_lines
                and any(i in prev_changed_lines for i in range(i1, i2))
            )

            if tag in ("replace", "delete"):
                color = _CLR_LEFT_CONFLICT if is_conflict else _CLR_LEFT_CHANGE
                for i in range(i1, i2):
                    if i in left_o2p:
                        left_highlights.append((left_o2p[i], color))

            if tag in ("replace", "insert"):
                color = _CLR_RIGHT_CONFLICT if is_conflict else _CLR_RIGHT_CHANGE
                for j in range(j1, j2):
                    if j in right_o2p:
                        right_highlights.append((right_o2p[j], color))

        for idx, real_line in enumerate(left_map):
            if real_line is None:
                left_highlights.append((idx, _CLR_PADDING))
        for idx, real_line in enumerate(right_map):
            if real_line is None:
                right_highlights.append((idx, _CLR_PADDING))

        _apply_extra_selections(left_edit, left_highlights)
        _apply_extra_selections(right_edit, right_highlights)

        has_conflict = any(color == _CLR_RIGHT_CONFLICT for _, color in right_highlights)
        self._tab_has_conflict[tab_index] = has_conflict
        if has_conflict:
            tab_color = QColor(255, 180, 50)
        elif self._has_array_warning:
            tab_color = QColor(100, 180, 255)
        else:
            tab_color = QColor(0, 0, 0, 0)
        self._tabs.tabBar().setTabTextColor(tab_index, tab_color)

        self._tab_diff_positions[tab_index] = diff_positions
        _, count_label, _ = self._tab_nav_widgets[tab_index]
        total = len(diff_positions)
        if total > 0:
            self._tab_current_idx[tab_index] = 0
            count_label.setText(f"1 / {total}")
        else:
            self._tab_current_idx[tab_index] = -1
            count_label.setText("0 / 0")

    def _goto_diff(self, tab_index: int, direction: int) -> None:
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

    def _toggle_search(self) -> None:
        """Ctrl+F：显示当前焦点编辑器对应的搜索框"""
        idx = self._tabs.currentIndex()
        if idx < 0 or idx >= len(self._tab_search_bars):
            return
        left_bar, right_bar = self._tab_search_bars[idx]
        left_edit, right_edit = self._tab_edits[idx]
        left_input, right_input = self._tab_search_inputs[idx]
        if left_edit.hasFocus() or left_input.hasFocus():
            bar, inp = left_bar, left_input
        else:
            bar, inp = right_bar, right_input
        visible = not bar.isVisible()
        bar.setVisible(visible)
        if visible:
            inp.setFocus()
            inp.selectAll()

    def _close_search_bar(self, search_widget: QWidget, editor: CodeEditor) -> None:
        """关闭搜索栏并清除搜索高亮"""
        search_widget.setVisible(False)
        selections = [s for s in editor.extraSelections()
                      if s.format.background().color() != _CLR_SEARCH]
        editor.setExtraSelections(selections)

    def _find_in_editor(self, editor: CodeEditor, text: str, backward: bool = False) -> None:
        """在指定编辑器中搜索文本，匹配处用亮蓝背景高亮"""
        if not text:
            return
        from PySide6.QtGui import QTextDocument
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        found = editor.find(text, flags)
        if not found:
            cursor = editor.textCursor()
            cursor.movePosition(
                cursor.MoveOperation.End if backward else cursor.MoveOperation.Start)
            editor.setTextCursor(cursor)
            found = editor.find(text, flags)
        if found:
            match_cursor = editor.textCursor()
            end_pos = match_cursor.selectionEnd()
            deselected = QTextCursor(editor.document())
            deselected.setPosition(end_pos)
            editor.setTextCursor(deselected)
            selections = [s for s in editor.extraSelections()
                          if s.format.background().color() != _CLR_SEARCH]
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(_CLR_SEARCH)
            sel.cursor = match_cursor
            selections.append(sel)
            editor.setExtraSelections(selections)

    def _save_override(self, tab_index: int) -> None:
        """验证 JSON 后计算 delta 并保存为 override"""
        right_edit = self._tab_edits[tab_index][1]
        _, right_map = self._tab_line_maps[tab_index]
        text = _get_real_text(right_edit, right_map)
        error_bar = self._tab_error_bars[tab_index]

        cleaned = clean_json_text(text)
        try:
            new_json = json.loads(cleaned, object_pairs_hook=_pairs_hook)
        except json.JSONDecodeError as e:
            error_bar.setText(f"⚠ 第 {e.lineno} 行: {e.msg}")
            error_bar.setVisible(True)
            right_edit.highlight_line(e.lineno)
            QMessageBox.warning(self, "JSON 语法错误",
                                f"第 {e.lineno} 行: {e.msg}\n请修正后再保存。")
            return

        error_bar.setVisible(False)
        right_edit.clear_highlights()

        # 解析编辑前的原始文本
        original_text = self._tab_original_texts[tab_index]
        old_cleaned = clean_json_text(original_text)
        old_json = json.loads(old_cleaned, object_pairs_hook=_pairs_hook)

        # 计算 delta
        from ..core.delta_store import compute_delta
        from ..core.types import MergeMode
        delta = compute_delta(old_json, new_json, "config", merge_mode=MergeMode.NORMAL)

        mod_id = self._diff_pairs[tab_index][0]
        store = JsonStore.instance()
        if delta is None:
            # 无实际变化，移除 override
            store.remove_override(mod_id, self._rel_path)
        else:
            store.set_override(mod_id, self._rel_path, delta)

        self._refresh_all()

    def _format_override(self, tab_index: int) -> None:
        """格式化右侧文本，重建填充对齐并更新高亮"""
        left_edit, right_edit = self._tab_edits[tab_index]

        _, old_right_map = self._tab_line_maps[tab_index]
        text = _get_real_text(right_edit, old_right_map)
        formatted = _format_with_comments(text)

        _, _, prev_text, _ = self._diff_pairs[tab_index]
        left_lines = prev_text.splitlines()
        right_lines = formatted.splitlines()
        opcodes = diff_opcodes(left_lines, right_lines)

        (padded_left, padded_right,
         left_map, right_map,
         _, _) = build_padded_texts(left_lines, right_lines, opcodes)

        # 更新行映射
        self._tab_line_maps[tab_index] = (left_map, right_map)
        left_edit._diff_line_map = left_map
        right_edit._diff_line_map = right_map

        scroll_val = right_edit.verticalScrollBar().value()
        left_edit.setUpdatesEnabled(False)
        right_edit.setUpdatesEnabled(False)
        left_edit.blockSignals(True)
        left_edit.document().blockSignals(True)
        right_edit.blockSignals(True)
        right_edit.document().blockSignals(True)
        try:
            left_edit.setPlainText('\n'.join(padded_left))
            right_edit.setPlainText('\n'.join(padded_right))
            right_edit.verticalScrollBar().setValue(scroll_val)

            self._compute_and_apply_highlights(tab_index, left_map, right_map)
        finally:
            left_edit.document().blockSignals(False)
            left_edit.blockSignals(False)
            right_edit.document().blockSignals(False)
            right_edit.blockSignals(False)
            left_edit.setUpdatesEnabled(True)
            right_edit.setUpdatesEnabled(True)

    def _reset_override(self, tab_index: int) -> None:
        """删除 override 文件并刷新"""
        mod_id, mod_name, _, _ = self._diff_pairs[tab_index]
        store = JsonStore.instance()
        if not store.has_override(mod_id, self._rel_path):
            QMessageBox.information(self, "提示", f"{mod_name} 没有自定义覆盖")
            return
        store.remove_override(mod_id, self._rel_path)
        self._refresh_all()

    def _update_warn_bar(self) -> None:
        """根据 _merge_warnings 和 _array_warnings 更新警告条"""
        parts: list[str] = []
        warnings = getattr(self, "_merge_warnings", [])
        if warnings:
            MAX_SHOW = 10
            lines = [f"  - {w}" for w in warnings[:MAX_SHOW]]
            if len(warnings) > MAX_SHOW:
                lines.append(f"  ... 还有 {len(warnings) - MAX_SHOW} 条")
            parts.append(
                f"Schema 验证警告 ({len(warnings)}):\n" + "\n".join(lines)
            )
        if self._array_warnings:
            arr_lines = [f"  - {p.replace(chr(1), ' → ')}" for p in self._array_warnings]
            parts.append(
                f"数组合并注意 ({len(self._array_warnings)}):\n"
                "以下数组被多个 Mod 同时修改，合并结果可能需要人工确认:\n"
                + "\n".join(arr_lines)
            )
        if parts:
            self._warn_bar.setText("\n".join(parts))
            self._warn_bar.setVisible(True)
        else:
            self._warn_bar.setVisible(False)

    def _refresh_all(self) -> None:
        """重新预计算并刷新所有 tab"""
        current_tab = self._tabs.currentIndex()
        MergeCache.instance().invalidate(self._rel_path)
        self._precompute_merge_states()
        # 暂时关闭顶部提示条
        # self._update_warn_bar()
        self._loaded_tabs.clear()
        for i in range(len(self._diff_pairs)):
            if i < len(self._tab_edits):
                self._tab_diff_positions[i] = []
                self._tab_current_idx[i] = -1
                self._tab_error_bars[i].setVisible(False)
        if current_tab < len(self._diff_pairs):
            self._load_tab(current_tab)
