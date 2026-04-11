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


# 正则：匹配双引号字符串（含转义）或 // 注释
_COMMENT_RE = re.compile(r'"(?:[^"\\]|\\.)*"|//.*$', re.MULTILINE)

# 正则：匹配双引号字符串（含转义）或连续逗号
_DUP_COMMA_RE = re.compile(r'"(?:[^"\\]|\\.)*"|,(\s*,)+', re.MULTILINE)


def strip_js_comments(text: str) -> str:
    """用正则剥离 // 注释，保留字符串内的 //"""
    def _replacer(m: re.Match) -> str:
        s = m.group()
        return s if s.startswith('"') else ''
    return _COMMENT_RE.sub(_replacer, text)


def strip_trailing_commas(text: str) -> str:
    """去除 JSON 中的尾随逗号（} 或 ] 前的逗号）"""
    return re.sub(r',(\s*[}\]])', r'\1', text)


def strip_duplicate_commas(text: str) -> str:
    """去除字符串外的连续逗号（如 ,, 或 ,,,），压缩为单个逗号"""
    def _replacer(m: re.Match) -> str:
        return m.group() if m.group().startswith('"') else ','
    return _DUP_COMMA_RE.sub(_replacer, text)


def fix_missing_commas(text: str) -> str:
    """修复对象内相邻键值对之间缺失的逗号。

    字符级解析器：遍历文本追踪字符串边界，在值结尾
    （"、]、}、数字、true/false/null）和下一个 key（"xxx": 模式）
    之间插入缺失的逗号。
    """
    result: list[str] = []
    i = 0
    n = len(text)

    def _skip_ws(pos: int) -> int:
        while pos < n and text[pos] in ' \t\r\n':
            pos += 1
        return pos

    def _is_key_start(pos: int) -> bool:
        """检查 pos 处是否是 "key": 模式"""
        if pos >= n or text[pos] != '"':
            return False
        k = pos + 1
        while k < n:
            if text[k] == '\\':
                k += 2
            elif text[k] == '"':
                k += 1
                break
            else:
                k += 1
        else:
            return False
        while k < n and text[k] in ' \t\r\n':
            k += 1
        return k < n and text[k] == ':'

    def _try_insert_comma(pos: int) -> None:
        j = _skip_ws(pos)
        if j < n and _is_key_start(j):
            result.append(',')

    while i < n:
        ch = text[i]

        if ch == '"':
            # 复制完整字符串
            result.append(ch)
            i += 1
            while i < n:
                c = text[i]
                if c == '\\':
                    result.append(c)
                    i += 1
                    if i < n:
                        result.append(text[i])
                        i += 1
                elif c == '"':
                    result.append(c)
                    i += 1
                    break
                else:
                    result.append(c)
                    i += 1
            _try_insert_comma(i)

        elif ch in ']}':
            result.append(ch)
            i += 1
            _try_insert_comma(i)

        elif ch in '0123456789' or (ch == '-' and i + 1 < n and text[i + 1] in '0123456789'):
            # 数字
            result.append(ch)
            i += 1
            while i < n and text[i] in '0123456789.eE+-':
                result.append(text[i])
                i += 1
            _try_insert_comma(i)

        elif text[i:i + 4] == 'true' and (i + 4 >= n or not text[i + 4].isalnum()):
            result.extend('true')
            i += 4
            _try_insert_comma(i)

        elif text[i:i + 5] == 'false' and (i + 5 >= n or not text[i + 5].isalnum()):
            result.extend('false')
            i += 5
            _try_insert_comma(i)

        elif text[i:i + 4] == 'null' and (i + 4 >= n or not text[i + 4].isalnum()):
            result.extend('null')
            i += 4
            _try_insert_comma(i)

        else:
            result.append(ch)
            i += 1

    return ''.join(result)


def clean_json_text(text: str) -> str:
    """统一的 JSON 文本清洗：注释 → 尾随逗号 → 缺失逗号 → 连续逗号"""
    text = strip_js_comments(text)
    text = strip_trailing_commas(text)
    text = fix_missing_commas(text)
    text = strip_duplicate_commas(text)
    return text


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

    # 检测并去除 BOM（异常格式，需要报告）
    if raw_bytes.startswith(b'\xef\xbb\xbf'):
        abnormal_fixes.append("UTF-8 BOM")
        raw_bytes = raw_bytes[3:]

    raw = raw_bytes.decode('utf-8')

    # 统一清洗：注释 → 尾随逗号 → 缺失逗号 → 连续逗号
    cleaned = clean_json_text(raw)

    # 只记录真正异常的格式问题
    if abnormal_fixes:
        msg = f"{path.name}: 已自动修正 [{', '.join(abnormal_fixes)}]"
        log.warning(msg)
        diag.warn("parse", msg)

    result = json.loads(cleaned)

    _json_cache[cache_key] = result
    return result if readonly else copy.deepcopy(result)


def clear_json_cache():
    """清空 JSON 解析缓存"""
    _json_cache.clear()


def dump_json(data: dict, file_path: str | Path):
    """将数据写入 JSON 文件（标准格式，无注释）"""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=4),
        encoding='utf-8'
    )
