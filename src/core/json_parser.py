"""
JSON 工具 - 文本清洗、格式化、写出

文件读取功能统一由 JsonStore 管理，本模块只提供：
- DupList 数据类型
- 文本清洗函数（strip_js_comments 等）
- 格式化输出（format_json）
- 文件写出（dump_json）
- 解析钩子（_pairs_hook）
"""
import json
import re
from pathlib import Path

from ..accel._fast_json import (
    fix_missing_commas as _c_fix_missing_commas,
    has_duplist as _c_has_duplist,
    pairs_hook as _pairs_hook,  # noqa: F401  # re-export
    strip_js_comments as _c_strip_js_comments,
    strip_trailing_commas as _c_strip_trailing_commas,
)
from .profiler import profile

# dump_json 已创建目录缓存，避免重复 mkdir 系统调用
_created_dirs: set[str] = set()


def reset_dir_cache() -> None:
    """清空目录创建缓存。在删除输出目录后调用，避免后续写入时跳过 mkdir。"""
    _created_dirs.clear()


class DupList(list):
    """JSON 重复键展开后的值列表。

    游戏 JSON 中同一对象内可出现同名键（如 "type":"char", "type":"item"），
    解析时将同名键的多个值收集为 DupList，序列化时还原为重复键。
    与普通 list 通过类型区分，合并逻辑按索引逐元素处理。
    """
    pass


# 正则：匹配双引号字符串（含转义）或连续逗号
_DUP_COMMA_RE = re.compile(r'"(?:[^"\\]|\\.)*"|,(\s*,)+', re.MULTILINE)


@profile
def strip_js_comments(text: str) -> str:
    """剥离 // 注释，保留字符串内的 //"""
    if '//' not in text:
        return text
    return str(_c_strip_js_comments(text))


def strip_trailing_commas(text: str) -> str:
    """去除 JSON 中的尾随逗号（} 或 ] 前的逗号）"""
    return str(_c_strip_trailing_commas(text))


def strip_duplicate_commas(text: str) -> str:
    """去除字符串外的连续逗号（如 ,, 或 ,,,），压缩为单个逗号"""
    def _replacer(m: re.Match[str]) -> str:
        return m.group() if m.group().startswith('"') else ','
    return _DUP_COMMA_RE.sub(_replacer, text)


@profile
def fix_missing_commas(text: str) -> str:
    """修复对象内相邻键值对之间缺失的逗号。"""
    return str(_c_fix_missing_commas(text))


@profile
def clean_json_text(text: str) -> str:
    """统一的 JSON 文本清洗：注释 → 尾随逗号 → 缺失逗号 → 连续逗号"""
    text = strip_js_comments(text)
    text = strip_trailing_commas(text)
    text = fix_missing_commas(text)
    text = strip_duplicate_commas(text)
    return text


def _serialize(obj: object, indent: int = 4, sort_keys: bool = False, _level: int = 0) -> str:
    """自定义 JSON 序列化：DupList 值展开为重复键。"""
    ind = ' ' * indent
    current_ind = ind * _level
    next_ind = ind * (_level + 1)

    if obj is None:
        return 'null'
    if isinstance(obj, bool):
        return 'true' if obj else 'false'
    if isinstance(obj, int):
        return str(obj)
    if isinstance(obj, float):
        return repr(obj)
    if isinstance(obj, str):
        return json.dumps(obj, ensure_ascii=False)

    if isinstance(obj, dict):
        if not obj:
            return '{}'
        keys = sorted(obj.keys()) if sort_keys else list(obj.keys())
        parts = []
        for key in keys:
            value = obj[key]
            key_str = json.dumps(key, ensure_ascii=False)
            if isinstance(value, DupList):
                # 重复键：为 DupList 中每个元素输出一次同名 key
                for elem in value:
                    val_str = _serialize(elem, indent, sort_keys, _level + 1)
                    parts.append(f'{next_ind}{key_str}: {val_str}')
            else:
                val_str = _serialize(value, indent, sort_keys, _level + 1)
                parts.append(f'{next_ind}{key_str}: {val_str}')
        return '{\n' + ',\n'.join(parts) + '\n' + current_ind + '}'

    if isinstance(obj, list):
        if not obj:
            return '[]'
        parts = []
        for item in obj:
            parts.append(next_ind + _serialize(item, indent, sort_keys, _level + 1))
        return '[\n' + ',\n'.join(parts) + '\n' + current_ind + ']'

    return json.dumps(obj, ensure_ascii=False)


@profile
def format_json(data: object) -> str:
    """格式化 JSON 文本（用于 diff 面板展示），保留重复键，key 排序。"""
    # 无 DupList 时用 C 实现的 json.dumps，比自定义 _serialize 快很多
    if not _has_duplist(data):
        return json.dumps(data, indent=4, sort_keys=True, ensure_ascii=False)
    return _serialize(data, indent=4, sort_keys=True)


def _has_duplist(obj: object) -> bool:
    """快速检测数据中是否存在 DupList"""
    return bool(_c_has_duplist(obj))


@profile
def dump_json(data: dict[str, object], file_path: str | Path) -> None:
    """将数据写入 JSON 文件，保留重复键"""
    path = Path(file_path)
    parent = str(path.parent)
    if parent not in _created_dirs:
        path.parent.mkdir(parents=True, exist_ok=True)
        _created_dirs.add(parent)
    # 无 DupList 时用标准 json.dumps，比自定义 _serialize 快
    if _has_duplist(data):
        text = _serialize(data, indent=4, sort_keys=False)
    else:
        text = json.dumps(data, indent=4, ensure_ascii=False)
    path.write_text(text, encoding='utf-8')
