"""
JSON 解析器 - 处理带有 JS 风格注释和尾随逗号的 JSON 文件
"""
import copy
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

from .diagnostics import diag
from .profiler import profile

# JSON 解析缓存：(路径, mtime) → 解析结果
_json_cache: dict[tuple[str, float], dict] = {}

# dump_json 已创建目录缓存，避免重复 mkdir 系统调用
_created_dirs: set[str] = set()


class DupList(list):
    """JSON 重复键展开后的值列表。

    游戏 JSON 中同一对象内可出现同名键（如 "type":"char", "type":"item"），
    解析时将同名键的多个值收集为 DupList，序列化时还原为重复键。
    与普通 list 通过类型区分，合并逻辑按索引逐元素处理。
    """
    pass


# C 加速模块（可选；macOS 源码执行时可能不存在）
try:
    from ..accel._fast_json import (  # pyright: ignore[reportMissingImports]
        strip_js_comments as _c_strip_js_comments,
        strip_trailing_commas as _c_strip_trailing_commas,
        fix_missing_commas as _c_fix_missing_commas,
        has_duplist as _c_has_duplist,
        pairs_hook as _c_pairs_hook,
    )
except ModuleNotFoundError:
    _c_strip_js_comments = None
    _c_strip_trailing_commas = None
    _c_fix_missing_commas = None
    _c_has_duplist = None
    _c_pairs_hook = None

# 正则：匹配双引号字符串（含转义）或连续逗号
_DUP_COMMA_RE = re.compile(r'"(?:[^"\\]|\\.)*"|,(\s*,)+', re.MULTILINE)


def _strip_js_comments_py(text: str) -> str:
    out = []
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == '\\':
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == '/' and i + 1 < len(text) and text[i + 1] == '/':
            while i < len(text) and text[i] not in '\r\n':
                i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


@profile
def strip_js_comments(text: str) -> str:
    """剥离 // 注释，保留字符串内的 //"""
    if '//' not in text:
        return text
    if _c_strip_js_comments is not None:
        return _c_strip_js_comments(text)
    return _strip_js_comments_py(text)


def _strip_trailing_commas_py(text: str) -> str:
    return re.sub(r',(?=\s*[}\]])', '', text)


def strip_trailing_commas(text: str) -> str:
    """去除 JSON 中的尾随逗号（} 或 ] 前的逗号）"""
    if _c_strip_trailing_commas is not None:
        return _c_strip_trailing_commas(text)
    return _strip_trailing_commas_py(text)


def strip_duplicate_commas(text: str) -> str:
    """去除字符串外的连续逗号（如 ,, 或 ,,,），压缩为单个逗号"""
    def _replacer(m: re.Match) -> str:
        return m.group() if m.group().startswith('"') else ','
    return _DUP_COMMA_RE.sub(_replacer, text)


def _fix_missing_commas_py(text: str) -> str:
    pattern = re.compile(r'(?<=[}\]"0-9eE\w])(?=\s*"(?:[^"\\]|\\.)*"\s*:)')
    while True:
        fixed = pattern.sub(', ', text)
        if fixed == text:
            return text
        text = fixed


@profile
def fix_missing_commas(text: str) -> str:
    """修复对象内相邻键值对之间缺失的逗号。"""
    if _c_fix_missing_commas is not None:
        return _c_fix_missing_commas(text)
    return _fix_missing_commas_py(text)


@profile
def clean_json_text(text: str) -> str:
    """统一的 JSON 文本清洗：注释 → 尾随逗号 → 缺失逗号 → 连续逗号"""
    text = strip_js_comments(text)
    text = strip_trailing_commas(text)
    text = fix_missing_commas(text)
    text = strip_duplicate_commas(text)
    return text


def _pairs_hook_py(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            current = result[key]
            if isinstance(current, DupList):
                current.append(value)
            else:
                result[key] = DupList([current, value])
        else:
            result[key] = value
    return result


def _has_duplist_py(obj) -> bool:
    if isinstance(obj, DupList):
        return True
    if isinstance(obj, dict):
        return any(_has_duplist_py(value) for value in obj.values())
    if isinstance(obj, list):
        return any(_has_duplist_py(item) for item in obj)
    return False


def _pairs_hook(pairs):
    if _c_pairs_hook is not None:
        return _c_pairs_hook(pairs)
    return _pairs_hook_py(pairs)


def _has_duplist(obj) -> bool:
    """快速检测数据中是否存在 DupList"""
    if _c_has_duplist is not None:
        return _c_has_duplist(obj)
    return _has_duplist_py(obj)


def _try_parse_progressive(raw: str) -> dict:
    """分级尝试解析 JSON 文本。

    按需逐步清洗，成功即停：
    1. 直接解析原始文本（仅无 // 时尝试，避免白解析后抛异常）
    2. 仅去 // 注释
    3. 去注释 + 去尾随逗号
    4. 完整清洗（去注释 + 去尾随逗号 + 修缺失逗号 + 去连续逗号）

    绝大部分文件在第 2 或第 3 步即可成功，避免全量跑 fix_missing_commas。
    """
    has_comment = '//' in raw

    if not has_comment:
        try:
            return json.loads(raw, object_pairs_hook=_pairs_hook)
        except json.JSONDecodeError:
            pass

    text = strip_js_comments(raw) if has_comment else raw
    try:
        return json.loads(text, object_pairs_hook=_pairs_hook)
    except json.JSONDecodeError:
        pass

    text = strip_trailing_commas(text)
    try:
        return json.loads(text, object_pairs_hook=_pairs_hook)
    except json.JSONDecodeError:
        pass

    text = fix_missing_commas(text)
    text = strip_duplicate_commas(text)
    return json.loads(text, object_pairs_hook=_pairs_hook)


@profile
def load_json(file_path: str | Path, readonly: bool = False) -> dict:
    """读取带注释的 JSON 文件并解析，自动修正常见格式问题并记录警告。
    内置 (路径, mtime) 缓存。

    readonly=True 时直接返回缓存引用，调用方必须保证不修改返回值；
    默认 False 返回 deepcopy。"""
    path = Path(file_path)
    mtime = path.stat().st_mtime
    cache_key = (str(path), mtime)
    if cache_key in _json_cache:
        cached = _json_cache[cache_key]
        return cached if readonly else copy.deepcopy(cached)

    raw_bytes = path.read_bytes()
    abnormal_fixes = []

    if raw_bytes.startswith(b'\xef\xbb\xbf'):
        abnormal_fixes.append("UTF-8 BOM")
        raw_bytes = raw_bytes[3:]

    raw = raw_bytes.decode('utf-8')
    result = _try_parse_progressive(raw)

    if abnormal_fixes:
        msg = f"{path.name}: 已自动修正 [{', '.join(abnormal_fixes)}]"
        log.warning(msg)
        diag.warn("parse", msg)

    _json_cache[cache_key] = result
    return result if readonly else copy.deepcopy(result)


def clear_json_cache():
    """清空 JSON 解析缓存"""
    _json_cache.clear()
    _created_dirs.clear()



def _serialize(obj, indent=4, sort_keys=False, _level=0):
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


@profile
def dump_json(data: dict, file_path: str | Path):
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
