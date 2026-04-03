"""
Mod 详情面板 - 右侧面板，显示选中 mod 的详细信息和 preview 图片
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QListWidget
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

        # preview 图片
        self.preview_label = QLabel()
        self.preview_label.setFixedHeight(180)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("background-color: #2a2a2a; border-radius: 4px;")
        layout.addWidget(self.preview_label)

        # 基本信息
        self.name_label = QLabel()
        self.name_label.setStyleSheet("font-size: 16px; font-weight: bold; margin-top: 8px;")
        layout.addWidget(self.name_label)

        self.version_label = QLabel()
        self.version_label.setStyleSheet("color: #888;")
        layout.addWidget(self.version_label)

        self.tags_label = QLabel()
        self.tags_label.setWordWrap(True)
        layout.addWidget(self.tags_label)

        self.desc_label = QLabel()
        self.desc_label.setWordWrap(True)
        self.desc_label.setMaximumHeight(80)
        layout.addWidget(self.desc_label)

        # 文件列表
        files_header = QLabel("修改的文件:")
        files_header.setStyleSheet("font-weight: bold; margin-top: 8px;")
        layout.addWidget(files_header)

        self.file_list = QListWidget()
        layout.addWidget(self.file_list)

        self._clear()

    def _clear(self):
        self.preview_label.clear()
        self.preview_label.setText("无预览图")
        self.name_label.setText("未选择 Mod")
        self.version_label.clear()
        self.tags_label.clear()
        self.desc_label.clear()
        self.file_list.clear()

    def show_mod(self, mod: ModInfo):
        """显示指定 mod 的详情"""
        # preview 图片
        if mod.preview_path:
            pixmap = QPixmap(mod.preview_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self.preview_label.width(), 180,
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
        self.desc_label.setText(mod.description or "无描述")

        self.file_list.clear()
        for f in sorted(mod.config_files):
            self.file_list.addItem(f)
        if mod.resource_files:
            self.file_list.addItem(f"--- 资源文件 ({len(mod.resource_files)}) ---")
            for f in sorted(mod.resource_files)[:20]:
                self.file_list.addItem(f)
            if len(mod.resource_files) > 20:
                self.file_list.addItem(f"... 还有 {len(mod.resource_files) - 20} 个文件")
