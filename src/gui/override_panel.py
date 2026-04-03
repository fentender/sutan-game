"""
覆盖详情面板 - 按文件维度展示覆盖链和字段级覆盖情况
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QLineEdit, QPushButton
)
from PySide6.QtGui import QColor

from ..core.conflict import FileOverrideInfo


class OverridePanel(QWidget):
    """覆盖详情面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list[FileOverrideInfo] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 标题栏和搜索
        header_layout = QHBoxLayout()
        self.toggle_btn = QPushButton("▼ 覆盖详情")
        self.toggle_btn.setStyleSheet("font-weight: bold; text-align: left; border: none; padding: 4px;")
        self.toggle_btn.clicked.connect(self._toggle)
        header_layout.addWidget(self.toggle_btn)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索文件名或 mod 名...")
        self.search_input.setMaximumWidth(250)
        self.search_input.textChanged.connect(self._filter)
        header_layout.addWidget(self.search_input)

        layout.addLayout(header_layout)

        # 树形视图
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["文件 / 字段", "覆盖链", "最终值"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 400)
        self.tree.setAlternatingRowColors(True)
        layout.addWidget(self.tree)

        self._collapsed = False

    def _toggle(self):
        self._collapsed = not self._collapsed
        self.tree.setVisible(not self._collapsed)
        self.search_input.setVisible(not self._collapsed)
        self.toggle_btn.setText("► 覆盖详情" if self._collapsed else "▼ 覆盖详情")

    def _filter(self, text: str):
        text = text.lower()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item is None:
                continue
            visible = (not text or
                       text in item.text(0).lower() or
                       text in item.text(1).lower())
            item.setHidden(not visible)

    def set_data(self, overrides: list[FileOverrideInfo]):
        """设置覆盖数据并刷新显示"""
        self._data = overrides
        self.tree.clear()

        conflict_color = QColor(255, 180, 80)  # 橙色标记冲突

        for info in overrides:
            # 文件级节点
            chain_text = "[本体] ← " + " ← ".join(info.mod_chain) if info.mod_chain else "[仅本体]"
            file_item = QTreeWidgetItem([info.rel_path, chain_text, ""])

            if info.has_conflict:
                file_item.setForeground(0, conflict_color)
                file_item.setText(0, f"{info.rel_path} (冲突!)")

            # 字段级子节点
            for fo in info.field_overrides:
                values_text = " → ".join(
                    f"{name}={_format_value(val)}" for name, val in fo.mod_values
                )
                override_text = f"本体={_format_value(fo.base_value)} → {values_text}"
                child = QTreeWidgetItem([
                    fo.field_path,
                    override_text,
                    _format_value(fo.final_value)
                ])
                if fo.is_conflict:
                    child.setForeground(0, conflict_color)
                file_item.addChild(child)

            # 新增条目
            for mod_name, desc in info.new_entries:
                child = QTreeWidgetItem([desc, f"来自: {mod_name}", "新增"])
                child.setForeground(2, QColor(100, 200, 100))
                file_item.addChild(child)

            self.tree.addTopLevelItem(file_item)

    def clear(self):
        self._data = []
        self.tree.clear()


def _format_value(val: object) -> str:
    """格式化值用于显示"""
    if val is None:
        return "null"
    if isinstance(val, str) and len(val) > 30:
        return val[:30] + "..."
    return str(val)
