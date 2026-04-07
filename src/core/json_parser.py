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


def strip_js_comments(text: str) -> str:
    """用正则剥离 // 注释，保留字符串内的 //"""
    def _replacer(m: re.Match) -> str:
        s = m.group()
        return s if s.startswith('"') else ''
    return _COMMENT_RE.sub(_replacer, text)


def strip_trailing_commas(text: str) -> str:
    """去除 JSON 中的尾随逗号（} 或 ] 前的逗号）"""
    # 匹配逗号后面跟着可选空白和 } 或 ]
    return re.sub(r',(\s*[}\]])', r'\1', text)


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

    # 去除 // 注释（游戏 JSON 常态，不报告）
    cleaned = strip_js_comments(raw)

    # 去除尾随逗号（游戏 JSON 常态，不报告）
    cleaned = strip_trailing_commas(cleaned)

    # 只记录真正异常的格式问题
    if abnormal_fixes:
        msg = f"{path.name}: 已自动修正 [{', '.join(abnormal_fixes)}]"
        log.warning(msg)
        diag.warn("parse", msg)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(
            f"{path}: {e.msg}",
            e.doc,
            e.pos,
        ) from None

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
