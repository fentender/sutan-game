"""
日志面板组件 - 从 MainWindow 中提取的独立日志显示面板
"""
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.diagnostics import ERROR, INFO, WARNING

# 日志项存储级别的自定义角色
_LEVEL_ROLE = Qt.ItemDataRole.UserRole + 1
# 日志项存储字段路径的自定义角色
_FIELD_PATH_ROLE = Qt.ItemDataRole.UserRole + 2


def prefix_mod_title(msg: str, name_map: dict[str, str]) -> str:
    """尝试从消息中提取 mod_id，查找对应 mod 名称并添加前缀"""
    if not name_map:
        return msg

    # 已含 mod 名称的消息不重复添加
    if re.search(r'Mod \[.+?\]', msg) or msg.startswith('【'):
        return msg

    # "Mod {mod_id}: ..." 格式（scan_errors）
    match = re.match(r'Mod (\d+):', msg)
    if match:
        name = name_map.get(match.group(1))
        if name:
            return f"【{name}】{msg}"
        return msg

    # 路径中提取 mod_id（workshop 目录结构）
    match = re.search(r'[/\\](\d{5,})[/\\]config[/\\]', msg)
    if match:
        name = name_map.get(match.group(1))
        if name:
            return f"【{name}】{msg}"

    return msg


class LogPanel(QWidget):
    """可筛选的日志面板，支持按级别着色和文件路径提取"""

    # 双击日志条目中包含文件路径时发出，携带 (文件路径, 字段路径)
    file_open_requested = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setVisible(False)

        self._error_count = 0
        self._log_filter_mode = "all"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 头部：标题 + 筛选按钮 + 清理按钮
        header = QHBoxLayout()
        header.setContentsMargins(4, 2, 4, 2)
        label = QLabel("日志")
        label.setStyleSheet("font-size: 12px; color: #aaa;")
        header.addWidget(label)

        self._filter_buttons: dict[str, QPushButton] = {}
        for text, mode in [("全部", "all"), ("信息", INFO), ("警告", WARNING), ("错误", ERROR)]:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setFixedWidth(40)
            btn.setStyleSheet("font-size: 11px; padding: 0;")
            btn.clicked.connect(lambda _, m=mode: self._set_filter(m))
            header.addWidget(btn)
            self._filter_buttons[mode] = btn
        self._filter_buttons["all"].setChecked(True)

        header.addStretch()
        btn_clear = QPushButton("清理")
        btn_clear.setFixedSize(40, 20)
        btn_clear.setStyleSheet("font-size: 11px; padding: 0;")
        btn_clear.clicked.connect(self.clear)
        header.addWidget(btn_clear)
        layout.addLayout(header)

        # 日志列表
        self._list = QListWidget()
        self._list.setMaximumHeight(120)
        self._list.setStyleSheet(
            "QListWidget { font-family: Consolas, monospace; font-size: 12px; }"
            "QListWidget::item { padding: 3px 4px;"
            "  border-bottom: 1px solid #444; }"
        )
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self._list)

    def show_messages(self, messages: list[tuple[str, str]]) -> None:
        """重置并显示消息列表 [(level, msg), ...]"""
        self._list.clear()
        self._error_count = 0
        if messages:
            for level, msg in messages:
                self.log_message(level, msg)
        else:
            self.setVisible(False)

    def log_message(self, level: str, msg: str) -> None:
        """追加一条日志，按级别着色"""
        self._error_count += 1
        item = QListWidgetItem(f"[{self._error_count}] {msg}")
        item.setData(_LEVEL_ROLE, level)
        if level == ERROR:
            item.setForeground(QColor(238, 136, 136))
        elif level == WARNING:
            item.setForeground(QColor(238, 200, 100))
        else:
            item.setForeground(QColor(180, 180, 180))
        # 从消息中提取文件路径
        match = re.search(r'([A-Za-z]:\\[^:]+\.json|/[^:]+\.json)', msg)
        if match:
            item.setData(Qt.ItemDataRole.UserRole, match.group(1))
        # 提取字段路径（格式: ...json > field.path: ...）
        fp_match = re.search(r'\.json\s*>\s*([a-zA-Z0-9_.]+):', msg)
        if fp_match:
            item.setData(_FIELD_PATH_ROLE, fp_match.group(1))
        self._list.addItem(item)
        self.setVisible(True)
        self._apply_filter_to_item(item)

    def clear(self) -> None:
        """清空日志"""
        self._list.clear()
        self._error_count = 0
        self.setVisible(False)

    def _set_filter(self, mode: str) -> None:
        """切换日志级别筛选"""
        self._log_filter_mode = mode
        for m, btn in self._filter_buttons.items():
            btn.setChecked(m == mode)
        for i in range(self._list.count()):
            self._apply_filter_to_item(self._list.item(i))

    def _apply_filter_to_item(self, item: QListWidgetItem) -> None:
        """根据当前筛选模式显示/隐藏日志项"""
        if self._log_filter_mode == "all":
            item.setHidden(False)
        else:
            item.setHidden(item.data(_LEVEL_ROLE) != self._log_filter_mode)

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        """双击日志条目，发出文件打开信号"""
        file_path = item.data(Qt.ItemDataRole.UserRole)
        if file_path:
            field_path = item.data(_FIELD_PATH_ROLE) or ""
            self.file_open_requested.emit(file_path, field_path)
