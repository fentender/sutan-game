"""
首次运行配置对话框 — 引导用户设置游戏路径和创意工坊路径
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import infer_workshop_path_from_game


class SetupDialog(QDialog):
    """引导用户配置游戏安装目录和创意工坊目录"""

    def __init__(self, default_game: str = "", default_workshop: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("首次运行 — 路径配置")
        self.setMinimumWidth(560)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 说明文字
        hint = QLabel("请配置以下路径后才能使用。")
        layout.addWidget(hint)

        # ── 游戏安装目录 ──
        layout.addWidget(QLabel("游戏安装目录:"))
        row1 = QHBoxLayout()
        self._game_edit = QLineEdit(default_game)
        self._game_edit.setPlaceholderText("例如 D:/SteamLibrary/steamapps/common/Sultan's Game")
        self._game_edit.textChanged.connect(self._on_game_path_changed)
        row1.addWidget(self._game_edit)
        btn_game = QPushButton("浏览...")
        btn_game.clicked.connect(self._browse_game)
        row1.addWidget(btn_game)
        layout.addLayout(row1)

        # ── 创意工坊目录 ──
        layout.addWidget(QLabel("创意工坊目录:"))
        row2 = QHBoxLayout()
        self._workshop_edit = QLineEdit(default_workshop)
        self._workshop_edit.setPlaceholderText("例如 D:/SteamLibrary/steamapps/workshop/content/3117820")
        self._workshop_edit.textChanged.connect(self._validate)
        row2.addWidget(self._workshop_edit)
        btn_workshop = QPushButton("浏览...")
        btn_workshop.clicked.connect(self._browse_workshop)
        row2.addWidget(btn_workshop)
        layout.addLayout(row2)

        # ── 状态提示 ──
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #e88888;")
        layout.addWidget(self._status_label)

        # ── 确定按钮 ──
        self._ok_btn = QPushButton("确定")
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self.accept)
        layout.addWidget(self._ok_btn, alignment=Qt.AlignmentFlag.AlignRight)

        self._validate()

    # ── 属性 ──

    @property
    def game_path(self) -> str:
        return self._game_edit.text().strip()

    @property
    def workshop_path(self) -> str:
        return self._workshop_edit.text().strip()

    # ── 内部方法 ──

    def _browse_game(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择游戏安装目录",
                                                 self._game_edit.text())
        if path:
            self._game_edit.setText(path)

    def _browse_workshop(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择创意工坊目录",
                                                 self._workshop_edit.text())
        if path:
            self._workshop_edit.setText(path)

    def _on_game_path_changed(self) -> None:
        """用户修改游戏路径后，自动推导创意工坊路径"""
        game = self.game_path
        if game and Path(game).exists() and not self._workshop_edit.text().strip():
            inferred = infer_workshop_path_from_game(game)
            if inferred:
                self._workshop_edit.setText(inferred)
        self._validate()

    def _validate(self) -> None:
        """校验两个路径是否都存在，控制确定按钮状态"""
        problems = []
        if not self.game_path:
            problems.append("请设置游戏安装目录")
        elif not Path(self.game_path).exists():
            problems.append("游戏安装目录不存在")

        if not self.workshop_path:
            problems.append("请设置创意工坊目录")
        elif not Path(self.workshop_path).exists():
            problems.append("创意工坊目录不存在")

        self._ok_btn.setEnabled(len(problems) == 0)
        self._status_label.setText("；".join(problems))
