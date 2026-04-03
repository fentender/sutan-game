"""
Mod 列表面板 - 左侧面板，显示所有 mod 并支持排序和启用/禁用
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QListWidgetItem, QCheckBox, QLabel
)
from PySide6.QtCore import Signal

from ..core.mod_scanner import ModInfo


class ModListItem(QWidget):
    """单个 mod 列表项"""
    toggled = Signal(str, bool)  # mod_id, enabled
    move_up = Signal(str)
    move_down = Signal(str)

    def __init__(self, mod: ModInfo, enabled: bool = True, parent=None):
        super().__init__(parent)
        self.mod = mod

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        self.checkbox = QCheckBox()
        self.checkbox.setChecked(enabled)
        self.checkbox.toggled.connect(lambda checked: self.toggled.emit(self.mod.mod_id, checked))
        layout.addWidget(self.checkbox)

        self.label = QLabel(mod.name or mod.mod_id)
        self.label.setToolTip(f"ID: {mod.mod_id}\n版本: {mod.version}\n文件数: {len(mod.config_files)}")
        layout.addWidget(self.label, 1)

        btn_up = QPushButton("▲")
        btn_up.setFixedWidth(28)
        btn_up.clicked.connect(lambda: self.move_up.emit(self.mod.mod_id))
        layout.addWidget(btn_up)

        btn_down = QPushButton("▼")
        btn_down.setFixedWidth(28)
        btn_down.clicked.connect(lambda: self.move_down.emit(self.mod.mod_id))
        layout.addWidget(btn_down)


class ModListPanel(QWidget):
    """Mod 列表面板"""
    mod_selected = Signal(object)  # ModInfo
    order_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mods: list[ModInfo] = []
        self._enabled: dict[str, bool] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QLabel("已安装的 Mod")
        header.setStyleSheet("font-weight: bold; font-size: 14px; padding: 4px;")
        layout.addWidget(header)

        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        btn_select_all = QPushButton("全选")
        btn_select_all.clicked.connect(self._select_all)
        btn_layout.addWidget(btn_select_all)

        btn_deselect_all = QPushButton("全不选")
        btn_deselect_all.clicked.connect(self._deselect_all)
        btn_layout.addWidget(btn_deselect_all)

        layout.addLayout(btn_layout)

    def set_mods(self, mods: list[ModInfo], order: list[str] | None = None, enabled: list[str] | None = None):
        """设置 mod 列表"""
        self._mods = list(mods)

        # 按照保存的顺序排列
        if order:
            order_map = {mid: i for i, mid in enumerate(order)}
            self._mods.sort(key=lambda m: order_map.get(m.mod_id, 9999))

        # 设置启用状态
        if enabled is not None:
            self._enabled = {m.mod_id: m.mod_id in enabled for m in self._mods}
        else:
            self._enabled = {m.mod_id: True for m in self._mods}

        self._refresh_list()

    def _refresh_list(self):
        """刷新列表显示"""
        current_row = self.list_widget.currentRow()
        self.list_widget.clear()

        for mod in self._mods:
            item = QListWidgetItem()
            widget = ModListItem(mod, self._enabled.get(mod.mod_id, True))
            widget.toggled.connect(self._on_toggle)
            widget.move_up.connect(self._on_move_up)
            widget.move_down.connect(self._on_move_down)
            item.setSizeHint(widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)

        if 0 <= current_row < self.list_widget.count():
            self.list_widget.setCurrentRow(current_row)

    def _on_toggle(self, mod_id: str, enabled: bool):
        self._enabled[mod_id] = enabled

    def _on_move_up(self, mod_id: str):
        idx = next((i for i, m in enumerate(self._mods) if m.mod_id == mod_id), -1)
        if idx > 0:
            self._mods[idx - 1], self._mods[idx] = self._mods[idx], self._mods[idx - 1]
            self._refresh_list()
            self.list_widget.setCurrentRow(idx - 1)
            self.order_changed.emit()

    def _on_move_down(self, mod_id: str):
        idx = next((i for i, m in enumerate(self._mods) if m.mod_id == mod_id), -1)
        if 0 <= idx < len(self._mods) - 1:
            self._mods[idx], self._mods[idx + 1] = self._mods[idx + 1], self._mods[idx]
            self._refresh_list()
            self.list_widget.setCurrentRow(idx + 1)
            self.order_changed.emit()

    def _on_selection_changed(self, row: int):
        if 0 <= row < len(self._mods):
            self.mod_selected.emit(self._mods[row])

    def _select_all(self):
        self._enabled = {m.mod_id: True for m in self._mods}
        self._refresh_list()

    def _deselect_all(self):
        self._enabled = {m.mod_id: False for m in self._mods}
        self._refresh_list()

    def get_enabled_mods(self) -> list[ModInfo]:
        """获取启用的 mod 列表（按当前顺序）"""
        return [m for m in self._mods if self._enabled.get(m.mod_id, False)]

    def get_mod_order(self) -> list[str]:
        """获取当前 mod 排序"""
        return [m.mod_id for m in self._mods]

    def get_enabled_ids(self) -> list[str]:
        """获取启用的 mod ID 列表"""
        return [mid for mid, en in self._enabled.items() if en]
