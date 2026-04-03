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
    QTextEdit, QLabel, QSplitter, QWidget
)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt

from ..core.json_parser import load_json
from ..core.merger import deep_merge, classify_json

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
                 mod_configs: list[tuple[str, str, Path]], parent=None):
        super().__init__(parent)
        self._rel_path = rel_path
        self._game_config_path = game_config_path
        self._mod_configs = mod_configs

        # 预计算各级合并状态的 JSON 文本（轻量）
        self._diff_pairs: list[tuple[str, str, str]] = []  # (mod_name, prev_text, curr_text)
        self._precompute_merge_states()

        # 懒加载标记：已填充 diff 的 tab 索引
        self._loaded_tabs: set[int] = set()
        # 各 tab 的左右 QTextEdit 引用
        self._tab_edits: list[tuple[QTextEdit, QTextEdit]] = []

        self.setWindowTitle(f"Diff 对比 - {rel_path}")
        self.resize(1000, 650)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        self._build_ui()

    def _precompute_merge_states(self):
        """预计算逐级合并的 JSON 文本对，不涉及 UI 操作"""
        base_file = self._game_config_path / self._rel_path
        base_data = load_json(base_file) if base_file.exists() else {}

        current: dict = copy.deepcopy(base_data)
        for _, mod_name, config_path in self._mod_configs:
            mod_file = config_path / self._rel_path
            if not mod_file.exists():
                continue

            mod_data = load_json(mod_file)
            prev_text = _format_json(current)

            file_type = classify_json(current)
            if file_type == "dictionary":
                next_state = copy.deepcopy(current)
                for key, value in mod_data.items():
                    if key in next_state:
                        next_state[key] = deep_merge(next_state[key], value)
                    else:
                        next_state[key] = copy.deepcopy(value)
                current = next_state
            else:
                current = deep_merge(current, mod_data)  # type: ignore[assignment]

            curr_text = _format_json(current)
            self._diff_pairs.append((mod_name, prev_text, curr_text))

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 文件路径标题
        path_label = QLabel(self._rel_path)
        path_label.setStyleSheet("font-weight: bold; font-size: 13px; padding: 2px;")
        path_label.setFixedHeight(24)
        layout.addWidget(path_label)

        if not self._diff_pairs:
            placeholder = QLabel("没有 Mod 修改此文件")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(placeholder)
            return

        self._tabs = QTabWidget()
        # 先创建所有 tab 的空壳
        for mod_name, _, _ in self._diff_pairs:
            tab, left_edit, right_edit = self._create_empty_tab(mod_name)
            self._tabs.addTab(tab, f"↔ {mod_name}")
            self._tab_edits.append((left_edit, right_edit))

        # 切换 tab 时懒加载 diff
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs)

        # 立即加载第一个 tab
        self._load_tab(0)

    def _create_empty_tab(self, mod_name: str) -> tuple[QWidget, QTextEdit, QTextEdit]:
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

        return widget, left_edit, right_edit

    def _on_tab_changed(self, index: int):
        self._load_tab(index)

    def _load_tab(self, index: int):
        """懒加载：首次切换到某 tab 时才计算并填充 diff"""
        if index in self._loaded_tabs or index >= len(self._diff_pairs):
            return
        self._loaded_tabs.add(index)

        _, prev_text, curr_text = self._diff_pairs[index]
        left_edit, right_edit = self._tab_edits[index]

        left_lines = prev_text.splitlines()
        right_lines = curr_text.splitlines()

        left_html, right_html = _build_diff_html(left_lines, right_lines)
        left_edit.setHtml(left_html)
        right_edit.setHtml(right_html)


def _format_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=4, sort_keys=True)


def _build_diff_html(left_lines: list[str], right_lines: list[str]) -> tuple[str, str]:
    """用 SequenceMatcher 生成左右两侧的 HTML，一次性 setHtml 比逐行 cursor 快得多"""
    sm = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=False)

    left_parts: list[str] = []
    right_parts: list[str] = []
    left_no = 0
    right_no = 0

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                left_no += 1
                right_no += 1
                left_parts.append(_html_line(left_no, left_lines[i1 + k], _CLR_NORMAL_FG, None))
                right_parts.append(_html_line(right_no, right_lines[j1 + k], _CLR_NORMAL_FG, None))

        elif tag == "replace":
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

        elif tag == "delete":
            for k in range(i2 - i1):
                left_no += 1
                left_parts.append(_html_line(left_no, left_lines[i1 + k], _CLR_DEL_FG, _CLR_DEL_BG))
                right_parts.append(_html_line(None, "", _CLR_DEL_FG, _CLR_DEL_BG))

        elif tag == "insert":
            for k in range(j2 - j1):
                left_parts.append(_html_line(None, "", _CLR_INS_FG, _CLR_INS_BG))
                right_no += 1
                right_parts.append(_html_line(right_no, right_lines[j1 + k], _CLR_INS_FG, _CLR_INS_BG))

    left_html = _wrap_html("\n".join(left_parts))
    right_html = _wrap_html("\n".join(right_parts))
    return left_html, right_html


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
