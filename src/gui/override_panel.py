"""
覆盖详情面板 - 按文件维度展示覆盖链和字段级覆盖情况
"""
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QLineEdit, QPushButton
)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt

from ..core.conflict import FileOverrideInfo


class OverridePanel(QWidget):
    """覆盖详情面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list[FileOverrideInfo] = []
        self._game_config_path: Path | None = None
        self._mod_configs: list[tuple[str, str, Path]] | None = None
        self._allow_deletions: bool = False
        self._filter_mode: str = "all"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 标题栏、筛选和搜索
        header_layout = QHBoxLayout()
        self.toggle_btn = QPushButton("▼ 覆盖详情")
        self.toggle_btn.setStyleSheet("font-weight: bold; text-align: left; border: none; padding: 4px;")
        self.toggle_btn.clicked.connect(self._toggle)
        header_layout.addWidget(self.toggle_btn)

        # 筛选按钮组
        self._filter_buttons: dict[str, QPushButton] = {}
        for label, mode in [("所有", "all"), ("普通", "normal"), ("冲突", "conflict")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedWidth(50)
            btn.clicked.connect(lambda _, m=mode: self._set_filter_mode(m))
            header_layout.addWidget(btn)
            self._filter_buttons[mode] = btn
        self._filter_buttons["all"].setChecked(True)

        header_layout.addStretch()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索文件名或 mod 名...")
        self.search_input.setMaximumWidth(250)
        self.search_input.textChanged.connect(lambda _: self._apply_filter())
        header_layout.addWidget(self.search_input)

        layout.addLayout(header_layout)

        # 树形视图
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["文件 / 字段", "覆盖链", "最终值"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 400)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.tree)

        self._collapsed = False

    def _toggle(self):
        self._collapsed = not self._collapsed
        self.tree.setVisible(not self._collapsed)
        self.search_input.setVisible(not self._collapsed)
        self.toggle_btn.setText("► 覆盖详情" if self._collapsed else "▼ 覆盖详情")

    def _set_filter_mode(self, mode: str):
        self._filter_mode = mode
        for m, btn in self._filter_buttons.items():
            btn.setChecked(m == mode)
        self._apply_filter()

    def _apply_filter(self):
        text = self.search_input.text().lower()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item is None:
                continue
            # 搜索匹配
            text_match = (not text or
                          text in item.text(0).lower() or
                          text in item.text(1).lower())
            # 筛选模式匹配
            info: FileOverrideInfo = item.data(0, Qt.ItemDataRole.UserRole)
            if info is not None:
                mode_match = (self._filter_mode == "all" or
                              (self._filter_mode == "conflict" and info.has_conflict) or
                              (self._filter_mode == "normal" and not info.has_conflict))
            else:
                mode_match = True
            item.setHidden(not (text_match and mode_match))

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        # 只响应文件级节点（顶层节点）
        if item.parent() is not None:
            return
        if self._game_config_path is None or self._mod_configs is None:
            return
        info: FileOverrideInfo = item.data(0, Qt.ItemDataRole.UserRole)
        if info is None:
            return

        from .diff_dialog import DiffDialog
        dlg = DiffDialog(
            rel_path=info.rel_path,
            game_config_path=self._game_config_path,
            mod_configs=self._mod_configs,
            allow_deletions=self._allow_deletions,
            parent=self
        )
        dlg.exec()

    def set_data(self, overrides: list[FileOverrideInfo],
                 game_config_path: Path = None,
                 mod_configs: list[tuple[str, str, Path]] = None,
                 allow_deletions: bool = False):
        """设置覆盖数据并刷新显示"""
        self._data = overrides
        self._game_config_path = game_config_path
        self._mod_configs = mod_configs
        self._allow_deletions = allow_deletions
        self.tree.clear()

        conflict_color = QColor(255, 180, 80)  # 橙色标记冲突

        for info in overrides:
            # 文件级节点
            chain_text = "[本体] ← " + " ← ".join(info.mod_chain) if info.mod_chain else "[仅本体]"
            file_item = QTreeWidgetItem([info.rel_path, chain_text, ""])
            file_item.setData(0, Qt.ItemDataRole.UserRole, info)

            if info.has_conflict:
                file_item.setForeground(0, conflict_color)
                file_item.setText(0, f"{info.rel_path} (冲突!)")

            # 字段级子节点
            for fo in info.field_overrides:
                override_text = "[本体] ← " + " ← ".join(name for name, _ in fo.mod_values)
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
