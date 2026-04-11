"""
Diff 对比窗口 - 逐级展示游戏本体经各 Mod 覆盖后的行级差异
左侧只读 CodeEditor 显示合并前状态，右侧可编辑 CodeEditor 显示合并结果
"""
import copy
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
from ..core.diagnostics import diag, merge_ctx
from ..core.json_parser import load_json, clean_json_text, format_json, _pairs_hook
from ..core.merger import deep_merge, classify_json, compute_mod_delta, _DELETED
from ..core.schema_loader import load_schemas, resolve_schema, get_schema_root_key
from ..core.profiler import profile
from .json_editor import CodeEditor, _format_with_comments, _DiffBlockData

# diff 行高亮背景色
_CLR_LEFT_CHANGE = QColor(80, 30, 30)     # 红底（被修改/删除的行）
_CLR_RIGHT_CHANGE = QColor(30, 80, 30)    # 绿底（新增/修改后的行）
_CLR_LEFT_CONFLICT = QColor(120, 80, 20)  # 橙底（冲突：左侧对应行）
_CLR_RIGHT_CONFLICT = QColor(120, 80, 20) # 橙底（冲突：此行被多个 mod 修改）
_CLR_PADDING = QColor(30, 30, 30)         # 填充行背景（略深于编辑器背景）
_CLR_SEARCH = QColor(40, 100, 180)        # 搜索匹配高亮（亮蓝）

# 同一行出现多种高亮时，按优先级保留最高的（QColor 不可哈希，用 rgb 整数做 key）
_COLOR_PRIORITY: dict[int, int] = {
    _CLR_PADDING.rgb(): 1,
    _CLR_LEFT_CHANGE.rgb(): 2,
    _CLR_RIGHT_CHANGE.rgb(): 2,
    _CLR_LEFT_CONFLICT.rgb(): 3,
    _CLR_RIGHT_CONFLICT.rgb(): 3,
}


def _normalize_for_diff(line: str) -> str:
    """去除行尾逗号，避免 JSON 数组元素追加/删除时的纯格式差异被识别为内容修改"""
    stripped = line.rstrip()
    if stripped.endswith(','):
        return stripped[:-1]
    return stripped


def _intern_lines(lines: list[str], table: dict[str, int]) -> list[int]:
    """将字符串行列表映射为整数 ID 列表，共享 table 跨多次调用复用。
    对行做尾逗号标准化，使 JSON 格式差异不影响 diff 结果。"""
    ids = []
    for line in lines:
        key = _normalize_for_diff(line)
        if key not in table:
            table[key] = len(table)
        ids.append(table[key])
    return ids


def _fast_opcodes(a_ids: list[int], b_ids: list[int]) -> list[tuple[str, int, int, int, int]]:
    """使用 rapidfuzz C++ 后端计算 diff opcodes（行哈希整数序列输入）。
    Indel 只产出 equal/delete/insert，此函数将相邻 delete+insert 合并为 replace
    以保持与 difflib 兼容的语义。"""
    from rapidfuzz.distance import Indel

    raw = Indel.opcodes(a_ids, b_ids)

    # 合并相邻 delete+insert 为 replace
    opcodes: list[tuple[str, int, int, int, int]] = []
    i = 0
    n = len(raw)
    while i < n:
        op = raw[i]
        tag = op.tag
        if tag == "delete" and i + 1 < n and raw[i + 1].tag == "insert":
            nxt = raw[i + 1]
            opcodes.append(("replace", op.src_start, op.src_end,
                            nxt.dest_start, nxt.dest_end))
            i += 2
        elif tag == "insert" and i + 1 < n and raw[i + 1].tag == "delete":
            nxt = raw[i + 1]
            opcodes.append(("replace", nxt.src_start, nxt.src_end,
                            op.dest_start, op.dest_end))
            i += 2
        else:
            opcodes.append((tag, op.src_start, op.src_end,
                            op.dest_start, op.dest_end))
            i += 1
    return opcodes


def _diff_opcodes(a_lines: list[str], b_lines: list[str]) -> list[tuple]:
    """行哈希 + rapidfuzz C++ 后端 diff — 31000 行文件仅需 ~1ms"""
    table: dict[str, int] = {}
    a_ids = _intern_lines(a_lines, table)
    b_ids = _intern_lines(b_lines, table)
    return _fast_opcodes(a_ids, b_ids)


def _build_padded_texts(
    left_lines: list[str],
    right_lines: list[str],
    opcodes: list[tuple[str, int, int, int, int]]
) -> tuple[
    list[str], list[str],
    list[int | None], list[int | None],
    dict[int, int], dict[int, int]
]:
    """根据 opcodes 在行数少的一侧插入空行，使两侧总行数一致。

    返回:
        padded_left, padded_right: 填充后的行列表
        left_map, right_map: padded_index → 原始行号(0-based)|None
        left_o2p, right_o2p: 原始行号 → padded_index
    """
    padded_left: list[str] = []
    padded_right: list[str] = []
    left_map: list[int | None] = []
    right_map: list[int | None] = []
    left_o2p: dict[int, int] = {}
    right_o2p: dict[int, int] = {}

    for tag, i1, i2, j1, j2 in opcodes:
        left_count = i2 - i1
        right_count = j2 - j1

        if tag == "equal":
            for k in range(left_count):
                idx = len(padded_left)
                left_o2p[i1 + k] = idx
                right_o2p[j1 + k] = idx
                padded_left.append(left_lines[i1 + k])
                padded_right.append(right_lines[j1 + k])
                left_map.append(i1 + k)
                right_map.append(j1 + k)

        elif tag == "insert":
            for k in range(right_count):
                idx = len(padded_left)
                right_o2p[j1 + k] = idx
                padded_left.append("")
                padded_right.append(right_lines[j1 + k])
                left_map.append(None)
                right_map.append(j1 + k)

        elif tag == "delete":
            for k in range(left_count):
                idx = len(padded_left)
                left_o2p[i1 + k] = idx
                padded_left.append(left_lines[i1 + k])
                padded_right.append("")
                left_map.append(i1 + k)
                right_map.append(None)

        elif tag == "replace":
            max_count = max(left_count, right_count)
            for k in range(max_count):
                idx = len(padded_left)
                if k < left_count:
                    left_o2p[i1 + k] = idx
                    padded_left.append(left_lines[i1 + k])
                    left_map.append(i1 + k)
                else:
                    padded_left.append("")
                    left_map.append(None)
                if k < right_count:
                    right_o2p[j1 + k] = idx
                    padded_right.append(right_lines[j1 + k])
                    right_map.append(j1 + k)
                else:
                    padded_right.append("")
                    right_map.append(None)

    assert len(padded_left) == len(padded_right)
    return padded_left, padded_right, left_map, right_map, left_o2p, right_o2p


def _apply_block_userdata(editor: CodeEditor, line_map: list[int | None]):
    """为编辑器的每个 block 设置 _DiffBlockData"""
    block = editor.document().begin()
    for real_line in line_map:
        if not block.isValid():
            break
        block.setUserData(_DiffBlockData(real_line))
        block = block.next()


def _get_real_text(editor: CodeEditor) -> str:
    """从编辑器中提取非填充行文本（剥离填充行）"""
    lines = []
    block = editor.document().begin()
    while block.isValid():
        data = block.userData()
        if not isinstance(data, _DiffBlockData) or data.real_line is not None:
            lines.append(block.text())
        block = block.next()
    return '\n'.join(lines)


@profile
def _apply_extra_selections(editor: CodeEditor,
                            highlights: list[tuple[int, QColor]]):
    """对 CodeEditor 应用行级背景高亮。同一行多种颜色时按优先级保留最高的。"""
    # 按行去重，冲突色优先
    best: dict[int, tuple[int, QColor]] = {}
    for block_no, color in highlights:
        prio = _COLOR_PRIORITY.get(color.rgb(), 0)
        prev = best.get(block_no)
        if prev is None or prio > prev[0]:
            best[block_no] = (prio, color)

    selections = []
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

    def __init__(self, rel_path: str, game_config_path: Path,
                 mod_configs: list[tuple[str, str, Path]],
                 allow_deletions: bool = False,
                 parent=None):
        super().__init__(parent)
        self._rel_path = rel_path
        self._game_config_path = game_config_path
        self._mod_configs = mod_configs
        self._allow_deletions = allow_deletions

        # 预计算各级合并状态的 JSON 文本
        self._base_text: str = ""  # 游戏本体原始文本（所有 mod 之前）
        self._diff_pairs: list[tuple[str, str, str, str]] = []  # (mod_id, mod_name, prev_text, curr_text)
        # 预计算的高亮数据：[(left_highlights, right_highlights, diff_positions), ...]
        self._precomputed_highlights: list[tuple[
            list[tuple[int, QColor]], list[tuple[int, QColor]], list[int]
        ]] = []
        # 预计算的 opcodes，供 _load_tab() 构建填充文本时复用
        self._precomputed_opcodes: list[list[tuple]] = []
        # 各 tab 是否包含冲突行
        self._tab_has_conflict: list[bool] = []
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

        self.setWindowTitle(f"Diff 对比 - {rel_path}")
        self.resize(1000, 650)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        self._build_ui()

    @profile
    def _precompute_merge_states(self):
        """预计算逐级合并的 JSON 文本对 + 行级 diff 高亮，不涉及 UI 操作"""
        self._diff_pairs.clear()
        self._precomputed_highlights.clear()
        self._precomputed_opcodes.clear()
        diag.snapshot("merge")  # 清空残留的 merge 警告
        base_file = self._game_config_path / self._rel_path
        base_data = load_json(base_file, readonly=True) if base_file.exists() else {}

        self._base_text = _format_json(base_data)

        schemas = load_schemas(SCHEMA_DIR)
        schema = resolve_schema(self._rel_path, schemas)
        root_key = get_schema_root_key(schema) if schema else None

        file_type = classify_json(base_data) if base_data else "config"

        current: dict = copy.deepcopy(base_data)
        # 缓存上一轮 curr_text，避免重复格式化
        last_curr_text: str | None = None
        for mod_id, mod_name, config_path in self._mod_configs:
            mod_file = config_path / self._rel_path
            if not mod_file.exists():
                continue

            # 设置合并上下文，供 deep_merge 内部的警告使用
            merge_ctx.mod_name = mod_name
            merge_ctx.mod_id = mod_id
            merge_ctx.rel_path = self._rel_path
            merge_ctx.source_file = str(mod_file)

            mod_data = load_json(mod_file, readonly=True)
            delta = compute_mod_delta(base_data, mod_data, file_type, self._allow_deletions,
                                      schema=schema, root_key=root_key)
            if not delta:
                continue

            # 复用上轮 curr_text 作为本轮 prev_text
            prev_text = last_curr_text if last_curr_text is not None else _format_json(current)

            field_path = [root_key] if root_key else None
            if file_type == "dictionary":
                next_state = copy.deepcopy(current)
                for key, value in delta.items():
                    if value is _DELETED:
                        next_state.pop(key, None)
                        continue
                    if key in next_state:
                        next_state[key] = deep_merge(next_state[key], value, schema, field_path,
                                                     _in_place=True)
                    else:
                        next_state[key] = copy.deepcopy(value)
                current = next_state
            else:
                current = deep_merge(current, delta, schema, field_path)

            curr_text = _format_json(current)

            # 检查是否存在用户 override
            override_file = MOD_OVERRIDES_DIR / mod_id / self._rel_path
            if override_file.exists():
                curr_text = override_file.read_text(encoding="utf-8")
                current = json.loads(curr_text, object_pairs_hook=_pairs_hook)
                # override 改变了 current，下轮需要重新格式化
                last_curr_text = None
            else:
                last_curr_text = curr_text

            self._diff_pairs.append((mod_id, mod_name, prev_text, curr_text))

        # 收集合并过程中产生的 schema 验证警告
        self._merge_warnings = [msg for _, msg in diag.snapshot("merge")]

        # 预计算所有 tab 的行级 diff 高亮（行哈希 + rapidfuzz C++ 后端）
        base_lines = self._base_text.splitlines()
        intern_table: dict[str, int] = {}
        base_ids = _intern_lines(base_lines, intern_table)

        for _, _, prev_text, curr_text in self._diff_pairs:
            left_lines = prev_text.splitlines()
            right_lines = curr_text.splitlines()

            left_ids = _intern_lines(left_lines, intern_table)
            right_ids = _intern_lines(right_lines, intern_table)

            # prev vs curr 的 opcodes
            opcodes = _fast_opcodes(left_ids, right_ids)
            self._precomputed_opcodes.append(opcodes)

            # base vs left — 判断哪些 prev 行已被之前的 mod 修改过
            prev_changed_lines: set[int] = set()
            if left_ids != base_ids:
                base_opcodes = _fast_opcodes(base_ids, left_ids)
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
                diff_positions.append(i1)

                is_conflict = (
                    tag == "replace"
                    and prev_changed_lines
                    and any(i in prev_changed_lines for i in range(i1, i2))
                )

                if tag in ("replace", "delete"):
                    color = _CLR_LEFT_CONFLICT if is_conflict else _CLR_LEFT_CHANGE
                    for i in range(i1, i2):
                        left_highlights.append((i, color))

                if tag in ("replace", "insert"):
                    color = _CLR_RIGHT_CONFLICT if is_conflict else _CLR_RIGHT_CHANGE
                    for j in range(j1, j2):
                        right_highlights.append((j, color))

            self._precomputed_highlights.append(
                (left_highlights, right_highlights, diff_positions)
            )
            self._tab_has_conflict.append(
                any(color == _CLR_RIGHT_CONFLICT for _, color in right_highlights)
            )

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 文件路径标题
        path_label = QLabel(self._rel_path)
        path_label.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px;")
        path_label.setFixedHeight(24)
        layout.addWidget(path_label)

        # Schema 验证警告条
        self._warn_bar = QLabel()
        self._warn_bar.setWordWrap(True)
        self._warn_bar.setStyleSheet(
            "background-color: #3a3010; color: #eec864; padding: 4px 8px; font-size: 12px;"
        )
        self._warn_bar.setVisible(False)
        layout.addWidget(self._warn_bar)
        self._update_warn_bar()

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
            self._tab_edits.append((left_edit, right_edit))
            self._tab_search_bars.append((left_search_w, right_search_w))
            self._tab_search_inputs.append((left_search_in, right_search_in))
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

        def sync_vertical_lr(val):
            if syncing[0]:
                return
            syncing[0] = True
            right_edit.verticalScrollBar().setValue(val)
            syncing[0] = False

        def sync_vertical_rl(val):
            if syncing[0]:
                return
            syncing[0] = True
            left_edit.verticalScrollBar().setValue(val)
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

        return (widget, left_edit, right_edit, error_bar, btn_prev, count_label, btn_next,
                left_search_widget, left_search_input, right_search_widget, right_search_input)

    def _on_tab_changed(self, index: int):
        self._load_tab(index)

    @profile
    def _load_tab(self, index: int):
        """懒加载：首次切换到某 tab 时构建填充文本并应用高亮"""
        if index in self._loaded_tabs or index >= len(self._diff_pairs):
            return
        self._loaded_tabs.add(index)

        _, _, prev_text, curr_text = self._diff_pairs[index]
        left_edit, right_edit = self._tab_edits[index]

        left_lines = prev_text.splitlines()
        right_lines = curr_text.splitlines()
        opcodes = self._precomputed_opcodes[index]

        (padded_left, padded_right,
         left_map, right_map,
         left_o2p, right_o2p) = _build_padded_texts(left_lines, right_lines, opcodes)

        # 禁用更新，避免 setPlainText/userdata/highlight 期间多次重绘
        left_edit.setUpdatesEnabled(False)
        right_edit.setUpdatesEnabled(False)
        try:
            left_edit.setPlainText('\n'.join(padded_left))
            right_edit.setPlainText('\n'.join(padded_right))

            _apply_block_userdata(left_edit, left_map)
            _apply_block_userdata(right_edit, right_map)

            self._apply_precomputed_highlights(index, left_o2p, right_o2p, left_map, right_map)
        finally:
            left_edit.setUpdatesEnabled(True)
            right_edit.setUpdatesEnabled(True)

    def _apply_precomputed_highlights(self, tab_index: int,
                                       left_o2p: dict[int, int],
                                       right_o2p: dict[int, int],
                                       left_map: list[int | None],
                                       right_map: list[int | None]):
        """应用预计算的高亮数据到编辑器，将原始行号翻译为填充后 block 号"""
        left_edit, right_edit = self._tab_edits[tab_index]
        left_hl, right_hl, diff_positions = self._precomputed_highlights[tab_index]

        # 翻译高亮行号到填充后 block 号
        translated_left_hl = [(left_o2p[ln], color) for ln, color in left_hl if ln in left_o2p]
        translated_right_hl = [(right_o2p[ln], color) for ln, color in right_hl if ln in right_o2p]

        # 为填充行添加背景高亮
        for idx, real_line in enumerate(left_map):
            if real_line is None:
                translated_left_hl.append((idx, _CLR_PADDING))
        for idx, real_line in enumerate(right_map):
            if real_line is None:
                translated_right_hl.append((idx, _CLR_PADDING))

        _apply_extra_selections(left_edit, translated_left_hl)
        _apply_extra_selections(right_edit, translated_right_hl)

        # 翻译 diff 导航位置
        translated_positions = [left_o2p[p] for p in diff_positions if p in left_o2p]
        self._tab_diff_positions[tab_index] = translated_positions
        _, count_label, _ = self._tab_nav_widgets[tab_index]
        total = len(translated_positions)
        if total > 0:
            self._tab_current_idx[tab_index] = 0
            count_label.setText(f"1 / {total}")
        else:
            self._tab_current_idx[tab_index] = -1
            count_label.setText("0 / 0")

    @profile
    def _compute_and_apply_highlights(self, tab_index: int,
                                       left_map: list[int | None],
                                       right_map: list[int | None]):
        """对比左右真实文本，计算差异高亮并翻译到填充后 block 号。
        仅用于格式化等需要实时重算的场景。"""
        left_edit, right_edit = self._tab_edits[tab_index]

        # 提取真实行（跳过填充行）
        left_real = [left_edit.document().findBlockByNumber(i).text()
                     for i, r in enumerate(left_map) if r is not None]
        right_real = [right_edit.document().findBlockByNumber(i).text()
                      for i, r in enumerate(right_map) if r is not None]

        # 构建原始行号 → 填充后 block 号映射
        left_o2p: dict[int, int] = {}
        right_o2p: dict[int, int] = {}
        for padded_idx, real_line in enumerate(left_map):
            if real_line is not None:
                left_o2p[real_line] = padded_idx
        for padded_idx, real_line in enumerate(right_map):
            if real_line is not None:
                right_o2p[real_line] = padded_idx

        # 行哈希加速 diff
        opcodes = _diff_opcodes(left_real, right_real)

        # base vs left — 判断哪些 prev 行已被之前的 mod 修改过
        prev_changed_lines: set[int] = set()
        base_lines = self._base_text.splitlines()
        if left_real != base_lines:
            base_opcodes = _diff_opcodes(base_lines, left_real)
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

        # 为填充行添加背景高亮
        for idx, real_line in enumerate(left_map):
            if real_line is None:
                left_highlights.append((idx, _CLR_PADDING))
        for idx, real_line in enumerate(right_map):
            if real_line is None:
                right_highlights.append((idx, _CLR_PADDING))

        _apply_extra_selections(left_edit, left_highlights)
        _apply_extra_selections(right_edit, right_highlights)

        # 同步更新 tab 冲突标记颜色
        has_conflict = any(color == _CLR_RIGHT_CONFLICT for _, color in right_highlights)
        self._tab_has_conflict[tab_index] = has_conflict
        self._tabs.tabBar().setTabTextColor(
            tab_index,
            QColor(255, 180, 50) if has_conflict else QColor(0, 0, 0, 0)
        )

        self._tab_diff_positions[tab_index] = diff_positions
        _, count_label, _ = self._tab_nav_widgets[tab_index]
        total = len(diff_positions)
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
        """Ctrl+F：显示当前焦点编辑器对应的搜索框"""
        idx = self._tabs.currentIndex()
        if idx < 0 or idx >= len(self._tab_search_bars):
            return
        left_bar, right_bar = self._tab_search_bars[idx]
        left_edit, right_edit = self._tab_edits[idx]
        left_input, right_input = self._tab_search_inputs[idx]
        # 判断焦点在哪侧，默认右侧
        if left_edit.hasFocus() or left_input.hasFocus():
            bar, inp = left_bar, left_input
        else:
            bar, inp = right_bar, right_input
        visible = not bar.isVisible()
        bar.setVisible(visible)
        if visible:
            inp.setFocus()
            inp.selectAll()

    def _close_search_bar(self, search_widget: QWidget, editor: CodeEditor):
        """关闭搜索栏并清除搜索高亮"""
        search_widget.setVisible(False)
        selections = [s for s in editor.extraSelections()
                      if s.format.background().color() != _CLR_SEARCH]
        editor.setExtraSelections(selections)

    def _find_in_editor(self, editor: CodeEditor, text: str, backward: bool = False):
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
            # 记录匹配的光标（含选区），用于高亮
            match_cursor = editor.textCursor()
            # 取消选区但保持光标在匹配末尾，确保下次搜索从此处继续
            end_pos = match_cursor.selectionEnd()
            deselected = QTextCursor(editor.document())
            deselected.setPosition(end_pos)
            editor.setTextCursor(deselected)
            # 将搜索高亮追加到已有的 diff 高亮之后
            selections = [s for s in editor.extraSelections()
                          if s.format.background().color() != _CLR_SEARCH]
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(_CLR_SEARCH)
            sel.cursor = match_cursor
            selections.append(sel)
            editor.setExtraSelections(selections)

    def _save_override(self, tab_index: int):
        """验证 JSON 后保存为 override 文件"""
        right_edit = self._tab_edits[tab_index][1]
        text = _get_real_text(right_edit)
        error_bar = self._tab_error_bars[tab_index]

        # JSON 验证
        cleaned = clean_json_text(text)
        try:
            json.loads(cleaned, object_pairs_hook=_pairs_hook)
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
        """格式化右侧文本，重建填充对齐并更新高亮"""
        left_edit, right_edit = self._tab_edits[tab_index]

        # 提取真实文本并格式化
        text = _get_real_text(right_edit)
        formatted = _format_with_comments(text)

        # 重新计算 diff + 填充
        _, _, prev_text, _ = self._diff_pairs[tab_index]
        left_lines = prev_text.splitlines()
        right_lines = formatted.splitlines()
        opcodes = _diff_opcodes(left_lines, right_lines)

        (padded_left, padded_right,
         left_map, right_map,
         _, _) = _build_padded_texts(left_lines, right_lines, opcodes)

        scroll_val = right_edit.verticalScrollBar().value()
        left_edit.setUpdatesEnabled(False)
        right_edit.setUpdatesEnabled(False)
        try:
            left_edit.setPlainText('\n'.join(padded_left))
            right_edit.setPlainText('\n'.join(padded_right))
            right_edit.verticalScrollBar().setValue(scroll_val)

            _apply_block_userdata(left_edit, left_map)
            _apply_block_userdata(right_edit, right_map)

            self._compute_and_apply_highlights(tab_index, left_map, right_map)
        finally:
            left_edit.setUpdatesEnabled(True)
            right_edit.setUpdatesEnabled(True)

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

    def _update_warn_bar(self):
        """根据 _merge_warnings 更新警告条"""
        warnings = getattr(self, "_merge_warnings", [])
        if warnings:
            MAX_SHOW = 10
            lines = [f"  - {w}" for w in warnings[:MAX_SHOW]]
            if len(warnings) > MAX_SHOW:
                lines.append(f"  ... 还有 {len(warnings) - MAX_SHOW} 条")
            self._warn_bar.setText(
                f"Schema 验证警告 ({len(warnings)}):\n" + "\n".join(lines)
            )
            self._warn_bar.setVisible(True)
        else:
            self._warn_bar.setVisible(False)

    def _refresh_all(self):
        """重新预计算并刷新所有 tab"""
        current_tab = self._tabs.currentIndex()
        self._precompute_merge_states()
        self._update_warn_bar()
        self._loaded_tabs.clear()
        for i in range(len(self._diff_pairs)):
            if i < len(self._tab_edits):
                self._tab_diff_positions[i] = []
                self._tab_current_idx[i] = -1
                self._tab_error_bars[i].setVisible(False)
        if current_tab < len(self._diff_pairs):
            self._load_tab(current_tab)


def _format_json(data: object) -> str:
    return format_json(data)
