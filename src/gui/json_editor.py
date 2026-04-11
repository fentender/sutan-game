"""
内置 JSON 文本编辑器 - 行号栏 + 错误行高亮 + 保存
"""
import json
from pathlib import Path

from ..core.json_parser import clean_json_text

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QTextEdit,
    QLabel, QPushButton, QMessageBox, QWidget
)
from PySide6.QtGui import (
    QFont, QColor, QTextCursor, QPainter, QTextFormat, QTextBlockUserData
)
from PySide6.QtCore import Qt, QRect, QSize, QMimeData

_FONT = QFont("Consolas", 10)
_INDENT = "    "


def _split_code_comment(line: str) -> tuple[str, str]:
    """将一行拆分为 (代码部分, 注释部分)，正确处理字符串内的 //"""
    in_string = False
    escape = False
    for i, ch in enumerate(line):
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string and ch == '/' and i + 1 < len(line) and line[i + 1] == '/':
            return line[:i], line[i:]
    return line, ""


def _count_brackets(code: str) -> tuple[int, int, int]:
    """统计代码中的开/闭括号数和行首闭括号数（排除字符串内的）
    返回 (opens, closes, leading_closes)"""
    in_string = False
    escape = False
    opens = 0
    closes = 0
    leading_closes = 0
    found_non_bracket = False

    for ch in code:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            found_non_bracket = True
            continue
        if in_string:
            continue
        if ch in ('{', '['):
            opens += 1
            found_non_bracket = True
        elif ch in ('}', ']'):
            closes += 1
            if not found_non_bracket:
                leading_closes += 1
        elif not ch.isspace():
            found_non_bracket = True

    return opens, closes, leading_closes


def _format_with_comments(text: str) -> str:
    """基于括号深度的缩进格式化，保留注释"""
    lines = text.split('\n')
    result = []
    indent_level = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append("")
            continue

        code, comment = _split_code_comment(stripped)
        code = code.rstrip()
        opens, closes, leading_closes = _count_brackets(code)

        this_indent = max(0, indent_level - leading_closes)

        if code and comment:
            result.append(_INDENT * this_indent + code + "  " + comment)
        elif code:
            result.append(_INDENT * this_indent + code)
        else:
            # 纯注释行
            result.append(_INDENT * this_indent + comment)

        indent_level += opens - closes
        indent_level = max(0, indent_level)

    # 去除末尾多余空行
    while result and result[-1] == "":
        result.pop()

    return '\n'.join(result) + '\n'


class _DiffBlockData(QTextBlockUserData):
    """Diff 模式下的 block 元数据，用于区分真实行和填充行"""
    def __init__(self, real_line: int | None):
        super().__init__()
        self.real_line = real_line  # None = 填充行, int = 原始行号(0-based)


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

        # 填充行背景色（与 diff_dialog._CLR_PADDING 一致）
        self._padding_color = QColor(30, 30, 30)

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

    def paintEvent(self, event):
        """先正常绘制，再用填充行背景色覆盖选区高亮，使填充行不显示选中状态"""
        super().paintEvent(event)
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return
        # 仅在存在 diff 填充行时才做额外绘制
        block = self.firstVisibleBlock()
        if not block.isValid():
            return
        painter = QPainter(self.viewport())
        offset = self.contentOffset()
        while block.isValid():
            geom = self.blockBoundingGeometry(block).translated(offset)
            if geom.top() > event.rect().bottom():
                break
            if geom.bottom() >= event.rect().top():
                data = block.userData()
                if isinstance(data, _DiffBlockData) and data.real_line is None:
                    painter.fillRect(
                        0, round(geom.top()),
                        self.viewport().width(), round(geom.height()),
                        self._padding_color
                    )
            block = block.next()
        painter.end()

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
                data = block.userData()
                if isinstance(data, _DiffBlockData):
                    if data.real_line is not None:
                        painter.drawText(
                            0, top, self._line_number_area.width() - 4,
                            self.fontMetrics().height(),
                            Qt.AlignmentFlag.AlignRight, str(data.real_line + 1)
                        )
                    # 填充行不绘制行号
                else:
                    painter.drawText(
                        0, top, self._line_number_area.width() - 4,
                        self.fontMetrics().height(),
                        Qt.AlignmentFlag.AlignRight, str(block_number + 1)
                    )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

        painter.end()

    def highlight_line(self, line_no: int, scroll_to: bool = True,
                       color: QColor | None = None, append: bool = False):
        """用指定背景色高亮指定行（1-based），默认红色。
        append=True 时追加到已有高亮，不覆盖。"""
        if color is None:
            color = QColor(100, 30, 30)
        selections = list(self.extraSelections()) if append else []
        block = self.document().findBlockByLineNumber(line_no - 1)
        if block.isValid():
            selection = QTextEdit.ExtraSelection()
            selection.format.setBackground(color)
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

    def createMimeDataFromSelection(self) -> QMimeData:
        """复制时自动剥离 diff 填充行"""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return super().createMimeDataFromSelection()

        doc = self.document()
        start_block = doc.findBlock(cursor.selectionStart())
        end_block = doc.findBlock(cursor.selectionEnd())

        # 选区内没有 diff 标记则走默认逻辑
        has_diff_data = False
        block = start_block
        while block.isValid() and block.blockNumber() <= end_block.blockNumber():
            if isinstance(block.userData(), _DiffBlockData):
                has_diff_data = True
                break
            block = block.next()
        if not has_diff_data:
            return super().createMimeDataFromSelection()

        # 提取选区内真实行（跳过填充行）
        lines = []
        block = start_block
        while block.isValid() and block.blockNumber() <= end_block.blockNumber():
            data = block.userData()
            if not isinstance(data, _DiffBlockData) or data.real_line is not None:
                lines.append(block.text())
            block = block.next()

        mime = QMimeData()
        mime.setText('\n'.join(lines))
        return mime


class JsonEditorDialog(QDialog):
    """JSON 文件编辑器弹窗，出错行标红"""

    def __init__(self, file_path: Path, parent=None, search_key: str = ""):
        super().__init__(parent)
        self._file_path = file_path
        self._search_key = search_key
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
        # 语法错误优先；无论是否有语法错误，都按 search_key 定位字段
        if self._search_key:
            line = self._find_key_line(self._search_key)
            if line:
                self._editor.highlight_line(line, scroll_to=True,
                                            color=QColor(80, 70, 20),
                                            append=True)

    def _find_key_line(self, field_path: str) -> int | None:
        """根据字段路径定位行号，取路径最后一段在文件文本中搜索"""
        if not field_path:
            return None
        key = field_path.rsplit(".", 1)[-1]
        pattern = f'"{key}"'
        text = self._editor.toPlainText()
        for i, line in enumerate(text.split('\n'), 1):
            if pattern in line:
                return i
        return None

    def _detect_error(self):
        """基于编辑器当前文本检测 JSON 语法错误"""
        self._error_line = None
        self._error_msg = ""
        text = self._editor.toPlainText()
        cleaned = clean_json_text(text)
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
        """格式化 JSON：调整缩进，保留注释"""
        text = self._editor.toPlainText()
        formatted = _format_with_comments(text)
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
