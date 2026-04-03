"""
内置 JSON 文本编辑器 - 行号栏 + 错误行高亮 + 保存
"""
import json
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QTextEdit,
    QLabel, QPushButton, QMessageBox, QWidget
)
from PySide6.QtGui import QFont, QColor, QTextCursor, QPainter, QTextFormat
from PySide6.QtCore import Qt, QRect, QSize

from ..core.json_parser import strip_js_comments, strip_trailing_commas

_FONT = QFont("Consolas", 10)


class _LineNumberArea(QWidget):
    """行号侧栏"""

    def __init__(self, editor: "CodeEditor"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self._editor.paint_line_numbers(event)


class CodeEditor(QPlainTextEdit):
    """带行号的代码编辑器"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(_FONT)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(32)

        self._line_number_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_width)
        self.updateRequest.connect(self._update_line_number_area)
        self._update_line_number_width()

    def line_number_area_width(self) -> int:
        digits = max(1, len(str(self.blockCount())))
        return 8 + self.fontMetrics().horizontalAdvance("9") * (digits + 1)

    def _update_line_number_width(self):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect, dy):
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(0, rect.y(), self._line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def paint_line_numbers(self, event):
        painter = QPainter(self._line_number_area)
        painter.fillRect(event.rect(), QColor(45, 45, 45))
        painter.setPen(QColor(140, 140, 140))
        painter.setFont(_FONT)

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0, top, self._line_number_area.width() - 4, self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight, str(block_number + 1)
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

        painter.end()

    def highlight_line(self, line_no: int, scroll_to: bool = True):
        """用红色背景高亮指定行（1-based）"""
        selections = []
        block = self.document().findBlockByLineNumber(line_no - 1)
        if block.isValid():
            selection = QTextEdit.ExtraSelection()
            selection.format.setBackground(QColor(100, 30, 30))
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selection.cursor = QTextCursor(block)
            selection.cursor.clearSelection()
            selections.append(selection)

            if scroll_to:
                cursor = QTextCursor(block)
                self.setTextCursor(cursor)
                self.ensureCursorVisible()

        self.setExtraSelections(selections)

    def clear_highlights(self):
        self.setExtraSelections([])


class JsonEditorDialog(QDialog):
    """JSON 文件编辑器弹窗，出错行标红"""

    def __init__(self, file_path: Path, parent=None):
        super().__init__(parent)
        self._file_path = file_path
        self._error_line: int | None = None
        self._error_msg: str = ""

        self.setWindowTitle(f"编辑 - {file_path.name}")
        self.resize(800, 600)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint)
        self._build_ui()
        self._load_and_highlight()

    def _build_ui(self):
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

        # 编辑区
        self._editor = CodeEditor()
        layout.addWidget(self._editor, 1)

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

        layout.addLayout(btn_layout)

    def _load_and_highlight(self):
        """读取文件内容并检测错误位置"""
        raw = self._file_path.read_text(encoding="utf-8")
        self._editor.setPlainText(raw)
        self._detect_error()
        self._update_highlights()

    def _detect_error(self):
        """基于编辑器当前文本检测 JSON 语法错误"""
        self._error_line = None
        self._error_msg = ""
        text = self._editor.toPlainText()
        cleaned = strip_trailing_commas(strip_js_comments(text))
        try:
            json.loads(cleaned)
        except json.JSONDecodeError as e:
            self._error_line = e.lineno
            self._error_msg = e.msg

    def _update_highlights(self, scroll_to_error: bool = True):
        if self._error_line is not None:
            self._error_bar.setText(f"⚠ 第 {self._error_line} 行: {self._error_msg}")
            self._error_bar.setVisible(True)
            self._editor.highlight_line(self._error_line, scroll_to=scroll_to_error)
        else:
            self._error_bar.setVisible(False)
            self._editor.clear_highlights()

    def _format(self):
        """格式化 JSON：清理注释和尾逗号后重新缩进"""
        text = self._editor.toPlainText()
        cleaned = strip_trailing_commas(strip_js_comments(text))
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            QMessageBox.warning(self, "格式化失败", "JSON 语法错误，无法格式化")
            return
        formatted = json.dumps(data, ensure_ascii=False, indent=4)
        # 保持滚动位置
        scroll_val = self._editor.verticalScrollBar().value()
        self._editor.setPlainText(formatted)
        self._editor.verticalScrollBar().setValue(scroll_val)
        self._detect_error()
        self._update_highlights(scroll_to_error=False)

    def _save(self):
        """保存文件并重新验证"""
        content = self._editor.toPlainText()
        self._file_path.write_text(content, encoding="utf-8")

        self._detect_error()
        self._update_highlights(scroll_to_error=False)

        if self._error_line is None:
            QMessageBox.information(self, "保存成功", "文件已保存，JSON 格式正确")
        else:
            QMessageBox.warning(self, "已保存", f"文件已保存，但仍有语法错误:\n第 {self._error_line} 行: {self._error_msg}")
