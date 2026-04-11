"""
多 Tab JSON 修复弹窗 - 展示解析失败的文件供用户修复
"""
import json

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QPushButton, QWidget, QMessageBox,
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt

from .json_editor import CodeEditor, _format_with_comments
from ..core.json_parser import clean_json_text


class JsonFixDialog(QDialog):
    """多 Tab JSON 修复弹窗。

    每个解析失败的文件一个 Tab，用户可编辑后保存修复。
    所有 Tab 修复成功后自动关闭；也可点击"忽视剩余"关闭。
    """

    def __init__(self, parse_failures: list, parent=None):
        super().__init__(parent)
        self._failures = parse_failures
        # {file_path_str: {'action': 'fixed'|'ignored'}}
        self.resolutions: dict[str, dict] = {}
        self._editors: list[CodeEditor] = []
        self._error_bars: list[QLabel] = []
        self._tab_fixed: list[bool] = [False] * len(parse_failures)

        self.setWindowTitle("JSON 解析错误")
        self.resize(900, 650)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        self._build_ui()
        self._init_tabs()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # 顶部横幅
        n = len(self._failures)
        banner = QLabel(f"  ⚠ {n} 个文件 JSON 解析失败，需要处理  ")
        banner.setStyleSheet(
            "background-color: #5a1a1a; color: #f99; padding: 6px 12px;"
            "font-weight: bold; font-size: 13px;"
        )
        banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(banner)

        # Tab 区域
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, 1)

        # 底部按钮
        btn_layout = QHBoxLayout()

        btn_format = QPushButton("格式化")
        btn_format.setStyleSheet("padding: 4px 16px;")
        btn_format.clicked.connect(self._format_current)
        btn_layout.addWidget(btn_format)

        btn_save = QPushButton("保存")
        btn_save.setStyleSheet("font-weight: bold; padding: 4px 16px;")
        btn_save.clicked.connect(self._save_current)
        btn_layout.addWidget(btn_save)

        btn_layout.addStretch()

        # 忽视按钮 + 提示
        ignore_container = QVBoxLayout()
        ignore_container.setSpacing(2)

        btn_ignore = QPushButton("忽视剩余，默认处理")
        btn_ignore.setStyleSheet("padding: 4px 16px; color: #ccc;")
        btn_ignore.clicked.connect(self._ignore_remaining)
        ignore_container.addWidget(btn_ignore, alignment=Qt.AlignmentFlag.AlignRight)

        ignore_hint = QLabel("不处理则该文件无法正常合并")
        ignore_hint.setStyleSheet("color: #e88; font-size: 11px;")
        ignore_container.addWidget(ignore_hint, alignment=Qt.AlignmentFlag.AlignRight)

        btn_layout.addLayout(ignore_container)
        layout.addLayout(btn_layout)

    def _init_tabs(self):
        """为每个失败文件创建一个 Tab"""
        for idx, failure in enumerate(self._failures):
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.setSpacing(2)

            # 错误提示条
            error_bar = QLabel()
            error_bar.setStyleSheet(
                "background-color: #5a1a1a; color: #f99; padding: 4px 8px; font-weight: bold;"
            )
            error_bar.setFixedHeight(28)
            error_bar.setText(f"⚠ 第 {failure.error_line} 行: {failure.error_msg}")
            tab_layout.addWidget(error_bar)
            self._error_bars.append(error_bar)

            # 编辑器
            editor = CodeEditor()
            editor.setReadOnly(False)
            raw = failure.file_path.read_text(encoding="utf-8")
            editor.setPlainText(raw)
            if failure.error_line > 0:
                editor.highlight_line(failure.error_line)
            tab_layout.addWidget(editor, 1)
            self._editors.append(editor)

            # Tab 标题
            if failure.mod_name:
                title = f"{failure.mod_name} / {failure.rel_path}"
            else:
                title = f"本体 / {failure.rel_path}"
            self._tabs.addTab(tab, title)
            # 未修复 Tab 标红
            self._tabs.tabBar().setTabTextColor(idx, QColor(238, 136, 136))

    def _format_current(self):
        """格式化当前 Tab 的编辑器内容"""
        idx = self._tabs.currentIndex()
        if idx < 0:
            return
        editor = self._editors[idx]
        text = editor.toPlainText()
        formatted = _format_with_comments(text)
        scroll_val = editor.verticalScrollBar().value()
        editor.setPlainText(formatted)
        editor.verticalScrollBar().setValue(scroll_val)
        # 重新检测错误
        self._detect_and_highlight(idx)

    def _save_current(self):
        """保存当前 Tab：写回原文件 + 验证 JSON"""
        idx = self._tabs.currentIndex()
        if idx < 0:
            return
        failure = self._failures[idx]
        editor = self._editors[idx]
        content = editor.toPlainText()

        # 写回原文件
        failure.file_path.write_text(content, encoding="utf-8")

        # 验证 JSON（不走自动修复，要验证用户是否真正修好了）
        cleaned = clean_json_text(content)
        try:
            json.loads(cleaned)
        except json.JSONDecodeError as e:
            # 仍有错误
            self._error_bars[idx].setText(f"⚠ 第 {e.lineno} 行: {e.msg}")
            self._error_bars[idx].setStyleSheet(
                "background-color: #5a1a1a; color: #f99; padding: 4px 8px; font-weight: bold;"
            )
            editor.highlight_line(e.lineno)
            QMessageBox.warning(self, "仍有错误",
                                f"文件已保存，但仍有语法错误:\n第 {e.lineno} 行: {e.msg}")
            return

        # 修复成功
        self._tab_fixed[idx] = True
        self.resolutions[str(failure.file_path)] = {'action': 'fixed'}
        self._error_bars[idx].setText("✓ JSON 格式正确")
        self._error_bars[idx].setStyleSheet(
            "background-color: #1a3a1a; color: #9f9; padding: 4px 8px; font-weight: bold;"
        )
        editor.clear_highlights()
        # Tab 标绿
        self._tabs.tabBar().setTabTextColor(idx, QColor(100, 220, 100))

        # 检查是否全部修复
        if all(self._tab_fixed):
            QMessageBox.information(self, "全部修复", "所有文件已修复，继续合并。")
            self.accept()

    def _detect_and_highlight(self, idx: int):
        """检测指定 Tab 的 JSON 错误并更新高亮"""
        editor = self._editors[idx]
        text = editor.toPlainText()
        cleaned = clean_json_text(text)
        try:
            json.loads(cleaned)
        except json.JSONDecodeError as e:
            self._error_bars[idx].setText(f"⚠ 第 {e.lineno} 行: {e.msg}")
            self._error_bars[idx].setStyleSheet(
                "background-color: #5a1a1a; color: #f99; padding: 4px 8px; font-weight: bold;"
            )
            editor.highlight_line(e.lineno, scroll_to=False)
            return
        # 无错误
        self._error_bars[idx].setText("✓ JSON 格式正确（点击保存确认）")
        self._error_bars[idx].setStyleSheet(
            "background-color: #2a3a1a; color: #af9; padding: 4px 8px; font-weight: bold;"
        )
        editor.clear_highlights()

    def _ignore_remaining(self):
        """忽视所有未修复的 Tab，关闭弹窗"""
        for idx, failure in enumerate(self._failures):
            key = str(failure.file_path)
            if key not in self.resolutions:
                self.resolutions[key] = {'action': 'ignored'}
        self.reject()

    def closeEvent(self, event):
        """关闭窗口等同忽视剩余"""
        for idx, failure in enumerate(self._failures):
            key = str(failure.file_path)
            if key not in self.resolutions:
                self.resolutions[key] = {'action': 'ignored'}
        super().closeEvent(event)
