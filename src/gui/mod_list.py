"""
Mod 列表面板 - 左侧面板，显示所有 mod 并支持排序和启用/禁用
"""
from PySide6.QtCore import QEvent, QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDrag,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..core.mod_scanner import ModInfo
from ..core.steam_time import MAJOR_UPDATE_TS


class DraggableModList(QListWidget):
    """支持拖拽排序的 Mod 列表"""
    item_moved = Signal(int, int)  # from_row, to_row

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._drag_from_row = -1
        self._drop_indicator_row = -1

    def startDrag(self, supportedActions: Qt.DropAction) -> None:
        """绘制简洁的拖拽预览：半透明圆角背景 + Mod 名称"""
        item = self.currentItem()
        if not item:
            return
        self._drag_from_row = self.currentRow()
        widget: ModListItem | None = self.itemWidget(item)  # type: ignore[assignment]
        drag = QDrag(self)
        drag.setMimeData(QMimeData())
        if widget:
            rect = self.visualItemRect(item)
            pixmap = QPixmap(rect.width(), rect.height())
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setOpacity(0.85)
            painter.setBrush(QColor(60, 60, 60, 220))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(pixmap.rect(), 6, 6)
            painter.setOpacity(1.0)
            painter.setPen(QColor(255, 255, 255))
            painter.setFont(widget.label.font())
            painter.drawText(pixmap.rect().adjusted(12, 0, -12, 0),
                             Qt.AlignmentFlag.AlignVCenter, widget.label.text())
            painter.end()
            drag.setPixmap(pixmap)
            drag.setHotSpot(self.mapFromGlobal(self.cursor().pos())
                            - self.visualItemRect(item).topLeft())
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.source() is self:
            event.accept()
        else:
            super().dragEnterEvent(event)

    def _row_at_pos(self, pos: QPoint) -> int:
        """根据鼠标位置计算插入行号"""
        target_item = self.itemAt(pos)
        if target_item:
            rect = self.visualItemRect(target_item)
            row = self.row(target_item)
            # 鼠标在项的下半部分时，插入到下一行
            if pos.y() > rect.center().y():
                return row + 1
            return row
        return self.count()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.source() is self:
            self._drop_indicator_row = self._row_at_pos(event.position().toPoint())
            self.viewport().update()
            event.accept()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._drop_indicator_row = -1
        self.viewport().update()
        if event.source() is not self:
            super().dropEvent(event)
            return
        from_row = self._drag_from_row
        to_row = self._row_at_pos(event.position().toPoint())
        # 从上往下拖时，pop 后目标索引需要减 1
        if from_row < to_row:
            to_row -= 1
        event.accept()
        self._drag_from_row = -1
        if from_row != to_row and from_row >= 0 and 0 <= to_row < self.count():
            self.item_moved.emit(from_row, to_row)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if self._drop_indicator_row < 0:
            return
        # 计算指示线的 y 坐标
        if self._drop_indicator_row < self.count():
            rect = self.visualItemRect(self.item(self._drop_indicator_row))
            y = rect.top()
        else:
            rect = self.visualItemRect(self.item(self.count() - 1))
            y = rect.bottom()
        painter = QPainter(self.viewport())
        pen = QPen(QColor(51, 153, 255), 2)
        painter.setPen(pen)
        painter.drawLine(0, y, self.viewport().width(), y)
        painter.end()


class ModListItem(QWidget):
    """单个 mod 列表项"""
    toggled = Signal(str, bool)  # mod_id, enabled
    move_up = Signal(str)
    move_down = Signal(str)
    merge_mode_changed = Signal(str, str)  # mod_id, mode_value ("" 表示跟随全局)

    def __init__(self, mod: ModInfo, enabled: bool = True,
                 merge_mode: str = "", game_update_time: int | None = None,
                 has_base_overlap: bool | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mod = mod

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        # 过时警告图标（用富文本渲染带颜色的感叹号）
        self._warn_label = QLabel()
        self._warn_label.setFixedWidth(18)
        self._warn_label.setTextFormat(Qt.TextFormat.RichText)
        # 大版本更新时间常量来自 steam_time.MAJOR_UPDATE_TS
        if (mod.update_time is not None
                and game_update_time is not None
                and mod.update_time < game_update_time):
            from datetime import datetime, timezone
            mod_date = datetime.fromtimestamp(mod.update_time, tz=timezone.utc).strftime("%Y-%m-%d")
            game_date = datetime.fromtimestamp(game_update_time, tz=timezone.utc).strftime("%Y-%m-%d")
            if has_base_overlap is False:
                # 过时但纯增量，风险低
                self._warn_label.setText('<b style="color:#1976d2; font-size:14px;">ℹ</b>')
                self._warn_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._warn_label.setToolTip(
                    f"该 Mod 已过时，但仅包含增量内容（未修改本体），风险较低\n"
                    f"Mod 最后更新: {mod_date}\n"
                    f"游戏最后更新: {game_date}"
                )
            elif mod.update_time < MAJOR_UPDATE_TS:
                # 严重：Mod 更新在大版本之前
                self._warn_label.setText('<b style="color:#d32f2f; font-size:16px;">❗</b>')
                self._warn_label.setToolTip(
                    f"⚠ 该 Mod 严重过时（早于大版本更新）\n"
                    f"Mod 最后更新: {mod_date}\n"
                    f"游戏大版本更新: 2026-03-31\n"
                    f"游戏最后更新: {game_date}"
                )
            else:
                # 轻微：Mod 在大版本之后更新过，但早于游戏最新更新
                self._warn_label.setText('<b style="color:#e8a200; font-size:14px;">⚠</b>')
                self._warn_label.setToolTip(
                    f"该 Mod 可能过时\n"
                    f"Mod 最后更新: {mod_date}\n"
                    f"游戏最后更新: {game_date}"
                )
        else:
            self._warn_label.setText("")
        # 让警告图标的 tooltip 立即显示（无延迟）
        self._warn_label.installEventFilter(self)
        layout.addWidget(self._warn_label)

        self.checkbox = QCheckBox()
        self.checkbox.setStyleSheet("""
            QCheckBox::indicator:unchecked {
                border: 1px solid #000;
                background-color: #fff;
            }
        """)
        self.checkbox.setChecked(enabled)
        self.checkbox.toggled.connect(lambda checked: self.toggled.emit(self.mod.mod_id, checked))
        layout.addWidget(self.checkbox)

        self.label = QLabel(mod.name or mod.mod_id)
        self.label.setToolTip(f"ID: {mod.mod_id}\n版本: {mod.version}\n文件数: {len(mod.config_files)}")
        layout.addWidget(self.label, 1)

        # per-mod 合并模式
        self.cmb_mode = QComboBox()
        self.cmb_mode.setFixedWidth(80)
        self.cmb_mode.addItem("跟随全局", "")
        self.cmb_mode.addItem("智能", "smart")
        self.cmb_mode.addItem("正常", "normal")
        self.cmb_mode.addItem("替换", "replace")
        idx = self.cmb_mode.findData(merge_mode)
        if idx >= 0:
            self.cmb_mode.setCurrentIndex(idx)
        self.cmb_mode.currentIndexChanged.connect(self._on_mode_changed)
        layout.addWidget(self.cmb_mode)

        btn_up = QPushButton("▲")
        btn_up.setFixedWidth(28)
        btn_up.clicked.connect(lambda: self.move_up.emit(self.mod.mod_id))
        layout.addWidget(btn_up)

        btn_down = QPushButton("▼")
        btn_down.setFixedWidth(28)
        btn_down.clicked.connect(lambda: self.move_down.emit(self.mod.mod_id))
        layout.addWidget(btn_down)

    def _on_mode_changed(self, index: int) -> None:
        mode_value = self.cmb_mode.itemData(index)
        self.merge_mode_changed.emit(self.mod.mod_id, mode_value)

    def eventFilter(self, obj: QWidget, event: QEvent) -> bool:  # type: ignore[override]
        """拦截警告图标的 ToolTip 事件，立即显示（无系统延迟）。"""
        if obj is self._warn_label and event.type() == QEvent.Type.ToolTip:
            tip = self._warn_label.toolTip()
            if tip:
                from PySide6.QtGui import QHelpEvent
                help_event: QHelpEvent = event  # type: ignore[assignment]
                QToolTip.showText(help_event.globalPos(), tip, self._warn_label)
                return True
        return super().eventFilter(obj, event)

class ModListPanel(QWidget):
    """Mod 列表面板"""
    mod_selected = Signal(object)  # ModInfo
    order_changed = Signal()
    merge_mode_changed = Signal(str, str)  # mod_id, mode_value

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mods: list[ModInfo] = []
        self._enabled: dict[str, bool] = {}
        self._merge_modes: dict[str, str] = {}  # per-mod 合并模式
        self._overlap: dict[str, bool] = {}  # mod_id → has_base_overlap
        self._game_update_time: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QLabel("已安装的 Mod")
        header.setStyleSheet("font-weight: bold; font-size: 14px; padding: 4px;")
        layout.addWidget(header)

        self.list_widget = DraggableModList()
        self.list_widget.currentRowChanged.connect(self._on_selection_changed)
        self.list_widget.item_moved.connect(self._on_item_moved)
        layout.addWidget(self.list_widget)

        btn_layout = QHBoxLayout()
        btn_select_all = QPushButton("全选")
        btn_select_all.clicked.connect(self._select_all)
        btn_layout.addWidget(btn_select_all)

        btn_deselect_all = QPushButton("全不选")
        btn_deselect_all.clicked.connect(self._deselect_all)
        btn_layout.addWidget(btn_deselect_all)

        layout.addLayout(btn_layout)

    def set_mods(self, mods: list[ModInfo], order: list[str] | None = None,
                 enabled: list[str] | None = None,
                 merge_modes: dict[str, str] | None = None,
                 game_update_time: int | None = None) -> None:
        """设置 mod 列表"""
        self._mods = list(mods)
        self._game_update_time = game_update_time

        # 按照保存的顺序排列
        if order:
            order_map = {mid: i for i, mid in enumerate(order)}
            self._mods.sort(key=lambda m: order_map.get(m.mod_id, 9999))

        # 设置启用状态
        if enabled is not None:
            self._enabled = {m.mod_id: m.mod_id in enabled for m in self._mods}
        else:
            self._enabled = {m.mod_id: True for m in self._mods}

        # 设置 per-mod 合并模式
        self._merge_modes = dict(merge_modes) if merge_modes else {}

        # 未勾选的排到勾选的后面（稳定排序，保持组内相对顺序）
        self._mods.sort(key=lambda m: 0 if self._enabled.get(m.mod_id, True) else 1)

        self._refresh_list()

    def _refresh_list(self) -> None:
        """刷新列表显示"""
        current_row = self.list_widget.currentRow()
        self.list_widget.clear()

        for mod in self._mods:
            item = QListWidgetItem()
            widget = ModListItem(mod, self._enabled.get(mod.mod_id, True),
                                 merge_mode=self._merge_modes.get(mod.mod_id, ""),
                                 game_update_time=self._game_update_time,
                                 has_base_overlap=self._overlap.get(mod.mod_id))
            widget.toggled.connect(self._on_toggle)
            widget.move_up.connect(self._on_move_up)
            widget.move_down.connect(self._on_move_down)
            widget.merge_mode_changed.connect(self._on_merge_mode_changed)
            item.setSizeHint(widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)

        if 0 <= current_row < self.list_widget.count():
            self.list_widget.setCurrentRow(current_row)

    def _on_toggle(self, mod_id: str, enabled: bool) -> None:
        self._enabled[mod_id] = enabled
        self.order_changed.emit()

    def _on_merge_mode_changed(self, mod_id: str, mode_value: str) -> None:
        if mode_value:
            self._merge_modes[mod_id] = mode_value
        else:
            self._merge_modes.pop(mod_id, None)
        self.merge_mode_changed.emit(mod_id, mode_value)

    def get_merge_modes(self) -> dict[str, str]:
        """获取 per-mod 合并模式"""
        return dict(self._merge_modes)

    def _on_item_moved(self, from_row: int, to_row: int) -> None:
        """拖拽排序后同步数据模型"""
        mod = self._mods.pop(from_row)
        self._mods.insert(to_row, mod)
        self._refresh_list()
        self.list_widget.setCurrentRow(to_row)
        self.order_changed.emit()

    def _on_move_up(self, mod_id: str) -> None:
        idx = next((i for i, m in enumerate(self._mods) if m.mod_id == mod_id), -1)
        if idx > 0:
            self._mods[idx - 1], self._mods[idx] = self._mods[idx], self._mods[idx - 1]
            self._refresh_list()
            self.list_widget.setCurrentRow(idx - 1)
            self.order_changed.emit()

    def _on_move_down(self, mod_id: str) -> None:
        idx = next((i for i, m in enumerate(self._mods) if m.mod_id == mod_id), -1)
        if 0 <= idx < len(self._mods) - 1:
            self._mods[idx], self._mods[idx + 1] = self._mods[idx + 1], self._mods[idx]
            self._refresh_list()
            self.list_widget.setCurrentRow(idx + 1)
            self.order_changed.emit()

    def _on_selection_changed(self, row: int) -> None:
        if 0 <= row < len(self._mods):
            self.mod_selected.emit(self._mods[row])

    def _select_all(self) -> None:
        self._enabled = {m.mod_id: True for m in self._mods}
        self._refresh_list()
        self.order_changed.emit()

    def _deselect_all(self) -> None:
        self._enabled = {m.mod_id: False for m in self._mods}
        self._refresh_list()
        self.order_changed.emit()

    def get_enabled_mods(self) -> list[ModInfo]:
        """获取启用的 mod 列表（按当前顺序）"""
        return [m for m in self._mods if self._enabled.get(m.mod_id, False)]

    def get_mod_order(self) -> list[str]:
        """获取当前 mod 排序"""
        return [m.mod_id for m in self._mods]

    def get_enabled_ids(self) -> list[str]:
        """获取启用的 mod ID 列表"""
        return [mid for mid, en in self._enabled.items() if en]

    def update_overlap(self, overlap: dict[str, bool]) -> None:
        """更新重叠状态并刷新列表图标"""
        self._overlap = overlap
        self._refresh_list()

