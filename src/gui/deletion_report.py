"""
删减报告对话框 - 展示各 Mod 对各文件的字段删除情况及统计
"""
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.conflict import DeletionRecord, FileOverrideInfo
from ..core.schema_generator import SEP

# 颜色
_CLR_FILE = QColor(180, 210, 255)     # 文件节点：浅蓝
_CLR_DELETION = QColor(238, 136, 136)  # 删除条目：浅红
_CLR_STAT_GROUP = QColor(200, 200, 200)  # 统计分组
_CLR_STAT_ITEM = QColor(238, 200, 100)   # 统计条目
_CLR_DELETED_LIGHT = QColor(80, 30, 30)    # 预览中被删除行的背景（浅红）
_CLR_DELETED_DARK = QColor(140, 30, 30)    # 预览中当前选中被删行的背景（深红）

# QTreeWidgetItem 自定义数据角色
_ROLE_REL_PATH = Qt.ItemDataRole.UserRole
_ROLE_RECORD = Qt.ItemDataRole.UserRole + 1


def _dedup_records(records: list[DeletionRecord]) -> list[tuple[DeletionRecord, list[str]]]:
    """按 field_path 去重，返回 [(record, [mod_name, ...]), ...]。

    同一 field_path 只保留第一条 record（base_value 相同），
    但收集所有涉及的 mod_name。
    """
    seen: dict[str, tuple[DeletionRecord, list[str]]] = {}
    result: list[tuple[DeletionRecord, list[str]]] = []
    for rec in records:
        if rec.field_path in seen:
            _, mods = seen[rec.field_path]
            if rec.mod_name not in mods:
                mods.append(rec.mod_name)
        else:
            mods = [rec.mod_name]
            entry = (rec, mods)
            seen[rec.field_path] = entry
            result.append(entry)
    return result


def _display_path(field_path: str) -> str:
    """将 SEP 分隔的字段路径转换为可读格式"""
    return field_path.replace(SEP, " → ")


def _format_value(val: object) -> str:
    """格式化值用于显示，过长截断"""
    if val is None:
        return "null"
    s = str(val)
    if len(s) > 80:
        return s[:80] + "..."
    return s


def _get_container_name(field_path: str) -> tuple[str | None, str]:
    """从字段路径提取最深容器名和叶子部分。

    返回 (container, leaf)：
    - container: 最深的非数组标记段（即该删减归属的容器），顶层条目时为 None
    - leaf: 容器之后的剩余路径（展开时显示的具体字段）
    """
    parts = field_path.split(SEP)
    if len(parts) <= 1:
        return None, parts[0]

    # 从右向左（跳过最后一个段）找第一个非数组标记段
    for i in range(len(parts) - 2, -1, -1):
        if not parts[i].startswith('['):
            container = parts[i]
            leaf = SEP.join(parts[i + 1:])
            return container, leaf

    return None, field_path


def _build_tree(header_labels: list[str]) -> QTreeWidget:
    """创建通用 QTreeWidget"""
    tree = QTreeWidget()
    tree.setHeaderLabels(header_labels)
    tree.setColumnWidth(0, 400)
    tree.setColumnWidth(1, 300)
    tree.setAlternatingRowColors(True)
    tree.setRootIsDecorated(True)
    return tree


class DeletionReportDialog(QDialog):
    """删减报告对话框"""

    def __init__(self, override_data: list[FileOverrideInfo],
                 game_config_path: Path | None = None,
                 mod_configs: list[tuple[str, str, Path]] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("删减报告")
        self.resize(900, 600)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint
        )

        self._game_config_path = game_config_path
        self._mod_configs = mod_configs

        # 收集所有删减记录：{rel_path: [DeletionRecord, ...]}
        self._deletions_by_file: dict[str, list[DeletionRecord]] = defaultdict(list)
        # 涉及删减的 Mod 名称（有序）
        self._mod_names: list[str] = []
        seen_mods: set[str] = set()

        for info in override_data:
            if not info.deletions:
                continue
            for rec in info.deletions:
                self._deletions_by_file[info.rel_path].append(rec)
                if rec.mod_name not in seen_mods:
                    seen_mods.add(rec.mod_name)
                    self._mod_names.append(rec.mod_name)

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        if not self._deletions_by_file:
            label = QLabel("当前没有检测到任何删减操作。\n"
                           "请确认已启用「允许删减」选项，且有 Mod 执行了删减。")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #aaa; font-size: 14px; padding: 40px;")
            layout.addWidget(label)
            return

        total = sum(
            len(_dedup_records(recs)) for recs in self._deletions_by_file.values()
        )
        summary = QLabel(
            f"共检测到 {total} 项删减，涉及 {len(self._deletions_by_file)} 个文件、"
            f"{len(self._mod_names)} 个 Mod"
        )
        summary.setStyleSheet("font-size: 12px; color: #ccc; padding: 2px 4px;")
        layout.addWidget(summary)

        tabs = QTabWidget()

        # "所有" tab
        tabs.addTab(self._build_all_tab(), "所有")

        # 每个 Mod 一个 tab
        for mod_name in self._mod_names:
            tabs.addTab(self._build_mod_tab(mod_name), mod_name)

        # "统计" tab
        tabs.addTab(self._build_stats_tab(), "统计")

        layout.addWidget(tabs)

    def _build_all_tab(self) -> QTreeWidget:
        """构建"所有"tab：文件 → 容器 → 叶子字段，含 Mod 名（去重）"""
        tree = _build_tree(["文件 / 容器 / 字段", "Mod", "被删除的值"])
        tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        for rel_path in sorted(self._deletions_by_file.keys()):
            deduped = _dedup_records(self._deletions_by_file[rel_path])
            file_item = QTreeWidgetItem([
                f"{rel_path}（{len(deduped)} 项删减）", "", ""
            ])
            file_item.setForeground(0, _CLR_FILE)

            self._add_container_children(
                file_item, deduped, rel_path=rel_path, show_mod=True)

            tree.addTopLevelItem(file_item)
            file_item.setExpanded(True)

        return tree

    def _build_mod_tab(self, mod_name: str) -> QTreeWidget:
        """构建单个 Mod tab：文件 → 容器 → 叶子字段（单 Mod 内去重）"""
        tree = _build_tree(["文件 / 容器 / 字段", "被删除的值", ""])
        tree.itemDoubleClicked.connect(self._on_item_double_clicked)

        for rel_path in sorted(self._deletions_by_file.keys()):
            mod_records = [r for r in self._deletions_by_file[rel_path]
                           if r.mod_name == mod_name]
            if not mod_records:
                continue
            deduped = _dedup_records(mod_records)

            file_item = QTreeWidgetItem([
                f"{rel_path}（{len(deduped)} 项删减）", "", ""
            ])
            file_item.setForeground(0, _CLR_FILE)

            self._add_container_children(
                file_item, deduped, rel_path=rel_path, show_mod=False)

            tree.addTopLevelItem(file_item)
            file_item.setExpanded(True)

        return tree

    def _add_container_children(self, parent: QTreeWidgetItem,
                                records: list[tuple[DeletionRecord, list[str]]],
                                rel_path: str,
                                show_mod: bool) -> None:
        """按容器分组添加子节点。

        records: [(DeletionRecord, [mod_name, ...]), ...] 已去重
        """
        container_groups: dict[str, list[tuple[str, DeletionRecord, list[str]]]] = defaultdict(list)
        toplevel_items: list[tuple[DeletionRecord, list[str]]] = []

        for rec, mods in records:
            container, leaf = _get_container_name(rec.field_path)
            if container is None:
                toplevel_items.append((rec, mods))
            else:
                container_groups[container].append((leaf, rec, mods))

        # 容器节点（按数量降序）
        for container, items in sorted(container_groups.items(),
                                       key=lambda x: -len(x[1])):
            container_node = QTreeWidgetItem([
                f"{container}（{len(items)} 个元素被删除）", "", ""
            ])
            container_node.setForeground(0, _CLR_STAT_ITEM)

            for _leaf, rec, mods in items:
                mod_text = ", ".join(mods)
                if len(mods) > 1:
                    mod_text += f" ({len(mods)}个Mod)"
                # 显示完整路径而非仅叶子部分，避免不同父元素下同名字段看起来重复
                display = _display_path(rec.field_path)
                if show_mod:
                    child = QTreeWidgetItem([
                        display, mod_text,
                        _format_value(rec.base_value),
                    ])
                else:
                    child = QTreeWidgetItem([
                        display,
                        _format_value(rec.base_value), "",
                    ])
                child.setForeground(0, _CLR_DELETION)
                child.setData(0, _ROLE_REL_PATH, rel_path)
                child.setData(0, _ROLE_RECORD, rec)
                container_node.addChild(child)

            parent.addChild(container_node)
            # 默认不展开

        # 顶层条目收纳到一个"(顶层条目)"节点中
        if toplevel_items:
            toplevel_node = QTreeWidgetItem([
                f"(顶层条目)（{len(toplevel_items)} 项被删除）", "", ""
            ])
            toplevel_node.setForeground(0, _CLR_STAT_ITEM)

            for rec, mods in toplevel_items:
                mod_text = ", ".join(mods)
                if len(mods) > 1:
                    mod_text += f" ({len(mods)}个Mod)"
                display = _display_path(rec.field_path)
                if show_mod:
                    child = QTreeWidgetItem([
                        display, mod_text,
                        _format_value(rec.base_value),
                    ])
                else:
                    child = QTreeWidgetItem([
                        display,
                        _format_value(rec.base_value), "",
                    ])
                child.setForeground(0, _CLR_DELETION)
                child.setData(0, _ROLE_REL_PATH, rel_path)
                child.setData(0, _ROLE_RECORD, rec)
                toplevel_node.addChild(child)

            parent.addChild(toplevel_node)
            # 默认不展开

    def _build_stats_tab(self) -> QTreeWidget:
        """构建统计 tab：按容器聚合，展开可看具体被删元素（去重）"""
        tree = _build_tree(["分类 / 容器", "删减数量", "详情"])

        # 对每个文件的记录去重后汇总
        all_deduped: list[tuple[str, DeletionRecord]] = []
        for rel_path, recs in self._deletions_by_file.items():
            for rec, _mods in _dedup_records(recs):
                all_deduped.append((rel_path, rec))

        # --- 所有 Mod 合计 ---
        total = len(all_deduped)
        all_group = QTreeWidgetItem([
            "所有 Mod 合计", f"共 {total} 项删减", ""
        ])
        all_group.setForeground(0, _CLR_STAT_GROUP)
        self._add_container_stats(all_group, all_deduped)
        tree.addTopLevelItem(all_group)
        all_group.setExpanded(True)

        # --- 每个 Mod ---
        for mod_name in self._mod_names:
            mod_deduped: list[tuple[str, DeletionRecord]] = []
            for rel_path, recs in self._deletions_by_file.items():
                mod_recs = [r for r in recs if r.mod_name == mod_name]
                for rec, _mods in _dedup_records(mod_recs):
                    mod_deduped.append((rel_path, rec))
            if not mod_deduped:
                continue
            mod_group = QTreeWidgetItem([
                mod_name, f"共 {len(mod_deduped)} 项删减", ""
            ])
            mod_group.setForeground(0, _CLR_STAT_GROUP)
            self._add_container_stats(mod_group, mod_deduped)
            tree.addTopLevelItem(mod_group)
            mod_group.setExpanded(True)

        return tree

    def _add_container_stats(self, parent: QTreeWidgetItem,
                             records: list[tuple[str, DeletionRecord]]) -> None:
        """按容器聚合，添加可展开的统计子节点。

        结构：
          容器名 — N 个元素被删除    （默认折叠）
            └ 叶子路径 — 被删除的值
          文件名（顶层条目）— N 项    （顶层条目归到文件名下，默认折叠）
            └ 条目 key — 被删除的值
        """
        # 按容器分组：{container_name: [(leaf, rec, rel_path), ...]}
        # container=None 的按 rel_path（文件名）再分组
        container_groups: dict[str, list[tuple[str, DeletionRecord, str]]] = defaultdict(list)
        toplevel_groups: dict[str, list[tuple[str, DeletionRecord]]] = defaultdict(list)

        for rel_path, rec in records:
            container, leaf = _get_container_name(rec.field_path)
            if container is None:
                toplevel_groups[rel_path].append((leaf, rec))
            else:
                container_groups[container].append((leaf, rec, rel_path))

        # 先按数量排序输出容器
        for container, items in sorted(container_groups.items(),
                                       key=lambda x: -len(x[1])):
            container_node = QTreeWidgetItem([
                container, f"{len(items)} 个元素被删除", ""
            ])
            container_node.setForeground(0, _CLR_STAT_ITEM)

            for leaf, rec, rel_path in items:
                leaf_display = _display_path(leaf)
                child = QTreeWidgetItem([
                    leaf_display, _format_value(rec.base_value), rel_path
                ])
                child.setForeground(0, _CLR_DELETION)
                container_node.addChild(child)

            parent.addChild(container_node)
            # 默认不展开

        # 顶层条目按文件名分组
        for rel_path, toplevel_items in sorted(toplevel_groups.items(),
                                      key=lambda x: -len(x[1])):
            file_node = QTreeWidgetItem([
                f"{rel_path}（顶层条目）", f"{len(toplevel_items)} 项被删除", ""
            ])
            file_node.setForeground(0, _CLR_FILE)

            for leaf, rec in toplevel_items:
                child = QTreeWidgetItem([
                    leaf, _format_value(rec.base_value), ""
                ])
                child.setForeground(0, _CLR_DELETION)
                file_node.addChild(child)

            parent.addChild(file_node)
            # 默认不展开

    # ── 双击预览 ──

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """双击叶子节点打开合并预览，被删除的字段标红"""
        rel_path = item.data(0, _ROLE_REL_PATH)
        record: DeletionRecord | None = item.data(0, _ROLE_RECORD)
        if rel_path is None or record is None:
            return  # 非叶子节点
        if not self._game_config_path or not self._mod_configs:
            return

        self._open_deletion_preview(rel_path, record)

    def _open_deletion_preview(self, rel_path: str, clicked_record: DeletionRecord) -> None:
        """打开删减预览：合并结果（不执行删除）+ 被删字段标红"""
        from PySide6.QtGui import QTextCharFormat, QTextCursor, QTextFormat
        from PySide6.QtWidgets import QTextEdit

        from ..config import SCHEMA_DIR
        from ..core.json_parser import format_json, load_json
        from ..core.merger import classify_json, compute_mod_delta, merge_file
        from ..core.schema_loader import get_schema_root_key, load_schemas, resolve_schema
        from .json_editor import CodeEditor

        # 调用方已确认 _game_config_path 和 _mod_configs 非 None
        assert self._game_config_path is not None
        assert self._mod_configs is not None

        # 加载游戏本体
        base_file = self._game_config_path / rel_path
        base_data = load_json(base_file, readonly=True) if base_file.exists() else {}
        file_type = classify_json(base_data) if base_data else "config"

        # 加载 schema
        schemas = load_schemas(SCHEMA_DIR)
        schema = resolve_schema(rel_path, schemas)
        root_key = get_schema_root_key(schema) if schema else None

        # 计算一次 delta（不含 allow_deletions 参数，统一产出完整差异）
        mod_data_list_shared = []
        for mod_id, mod_name, config_path in self._mod_configs:
            mod_file = config_path / rel_path
            if not mod_file.exists():
                continue
            mod_data = load_json(mod_file, readonly=True)
            delta = compute_mod_delta(
                base_data, mod_data, file_type,
                schema=schema, root_key=root_key,
            )
            if delta:
                mod_data_list_shared.append((mod_id, mod_name, delta, str(mod_file)))

        # 两次合并：allow_deletions=False（保留全部）和 True（执行删除）
        result_no_del = merge_file(
            base_data, mod_data_list_shared, rel_path, schema=schema,
            allow_deletions=False,
        )
        result_del = merge_file(
            base_data, mod_data_list_shared, rel_path, schema=schema,
            allow_deletions=True,
        )

        # 格式化两份 JSON 文本
        text_no_del = format_json(result_no_del.merged_data)
        text_del = format_json(result_del.merged_data)

        # 用行级 diff 找出"无删除版本有但有删除版本没有"的行 = 被删行
        lines_no_del = text_no_del.splitlines()
        lines_del = text_del.splitlines()

        deleted_line_set = self._diff_deleted_lines(lines_no_del, lines_del)

        # 找到被双击字段在文本中的行号（用于深红高亮和滚动定位）
        scroll_key = self._get_scroll_key(clicked_record.field_path)
        clicked_line: int | None = None
        if scroll_key:
            for i, line in enumerate(lines_no_del, 1):
                if f'"{scroll_key}"' in line and i in deleted_line_set:
                    clicked_line = i
                    break

        # 打开预览对话框
        dlg = QDialog(self)
        dlg.setWindowTitle(f"删减预览 - {rel_path}")
        dlg.resize(800, 600)
        dlg.setWindowFlags(
            dlg.windowFlags() | Qt.WindowType.WindowMaximizeButtonHint
        )
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(4, 4, 4, 4)

        info_label = QLabel(
            f"文件: {rel_path}　|　"
            f"浅红 = 被删除的字段（共 {len(deleted_line_set)} 行）　"
            f"深红 = 当前选中的字段"
        )
        info_label.setStyleSheet("font-size: 12px; color: #ccc; padding: 2px;")
        layout.addWidget(info_label)

        editor = CodeEditor()
        editor.setReadOnly(True)
        editor.setPlainText(text_no_del)
        layout.addWidget(editor, 1)

        # 批量构建 ExtraSelections（O(n) 替代逐行 append 的 O(n²)）
        doc = editor.document()
        selections = []
        for line_no in sorted(deleted_line_set):
            color = _CLR_DELETED_DARK if line_no == clicked_line else _CLR_DELETED_LIGHT
            block = doc.findBlockByLineNumber(line_no - 1)
            if not block.isValid():
                continue
            sel = QTextEdit.ExtraSelection()
            fmt = QTextCharFormat()
            fmt.setBackground(color)
            fmt.setProperty(QTextFormat.Property.FullWidthSelection, True)
            sel.format = fmt
            cursor = QTextCursor(block)
            cursor.clearSelection()
            sel.cursor = cursor
            selections.append(sel)
        editor.setExtraSelections(selections)

        # 滚动到被双击的字段
        if clicked_line:
            block = doc.findBlockByLineNumber(clicked_line - 1)
            if block.isValid():
                editor.setTextCursor(QTextCursor(block))
                editor.ensureCursorVisible()

        dlg.exec()

    @staticmethod
    def _diff_deleted_lines(lines_full: list[str],
                            lines_trimmed: list[str]) -> set[int]:
        """对比无删除版本和有删除版本，返回被删除的行号集合（1-based）。

        使用 difflib 找出 lines_full 中存在但 lines_trimmed 中不存在的行。
        """
        import difflib
        matcher = difflib.SequenceMatcher(None, lines_full, lines_trimmed)
        deleted_lines: set[int] = set()
        for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
            if tag == 'delete' or tag == 'replace':
                for i in range(i1, i2):
                    deleted_lines.add(i + 1)  # 转为 1-based
        return deleted_lines

    @staticmethod
    def _get_scroll_key(field_path: str) -> str:
        """从 field_path 提取用于滚动定位的 key"""
        parts = field_path.split(SEP)
        # 取最后一个非数组标记的段
        for part in reversed(parts):
            if not part.startswith('['):
                return part
        return parts[-1] if parts else ""
