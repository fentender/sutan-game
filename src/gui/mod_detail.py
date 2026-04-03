"""
Mod 详情面板 - 右侧面板，显示选中 mod 的详细信息和 preview 图片
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea
)
from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt

from ..core.mod_scanner import ModInfo


class ModDetailPanel(QWidget):
    """Mod 详情面板"""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)

        header = QLabel("Mod 详情")
        header.setStyleSheet("font-weight: bold; font-size: 14px; padding: 4px;")
        layout.addWidget(header)

        # ── 顶部区域：左图片 + 右基本信息 ──
        top_layout = QHBoxLayout()
        top_layout.setSpacing(10)

        # 左栏：预览图片
        left_col = QVBoxLayout()
        self.preview_label = QLabel()
        self.preview_label.setFixedSize(140, 140)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("background-color: #2a2a2a; border-radius: 4px;")
        left_col.addWidget(self.preview_label)
        left_col.addStretch()
        top_layout.addLayout(left_col)

        # 右栏：名称 + 版本 + 标签
        right_col = QVBoxLayout()
        right_col.setSpacing(2)

        self.name_label = QLabel()
        self.name_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        right_col.addWidget(self.name_label)

        self.version_label = QLabel()
        self.version_label.setStyleSheet("color: #888;")
        right_col.addWidget(self.version_label)

        self.tags_label = QLabel()
        self.tags_label.setWordWrap(True)
        right_col.addWidget(self.tags_label)

        right_col.addStretch()
        top_layout.addLayout(right_col, 1)
        layout.addLayout(top_layout)

        # ── 底部区域：描述文本（可滚动） ──
        self.desc_scroll = QScrollArea()
        self.desc_scroll.setWidgetResizable(True)
        self.desc_scroll.setStyleSheet("QScrollArea { border: none; }")

        self.desc_label = QLabel()
        self.desc_label.setWordWrap(True)
        self.desc_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.desc_label.setStyleSheet("padding: 4px;")
        self.desc_scroll.setWidget(self.desc_label)

        layout.addWidget(self.desc_scroll, 1)

        self._clear()

    def _clear(self):
        self.preview_label.clear()
        self.preview_label.setText("无预览图")
        self.name_label.setText("未选择 Mod")
        self.version_label.clear()
        self.tags_label.clear()
        self.desc_label.clear()

    def show_mod(self, mod: ModInfo):
        """显示指定 mod 的详情"""
        # 预览图片
        if mod.preview_path:
            pixmap = QPixmap(mod.preview_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    140, 140,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self.preview_label.setPixmap(scaled)
            else:
                self.preview_label.setText("无法加载预览图")
        else:
            self.preview_label.setText("无预览图")

        self.name_label.setText(mod.name or mod.mod_id)
        self.version_label.setText(f"版本: {mod.version}" if mod.version else "")
        self.tags_label.setText(f"标签: {', '.join(mod.tags)}" if mod.tags else "")

        # 描述
        desc = mod.description or "无描述"
        self.desc_label.setText(desc)
        line_count = desc.count('\n') + 1
        if line_count <= 3 and len(desc) < 150:
            self.desc_scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        else:
            self.desc_scroll.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded)
