"""
JSON 解析器 - 处理带有 JS 风格注释和尾随逗号的 JSON 文件
"""
import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# 全局解析警告收集器
parse_warnings: list[str] = []


def strip_js_comments(text: str) -> str:
    """逐行剥离 // 注释，保留字符串内的 //"""
    result = []
    for line in text.split('\n'):
        new_line = []
        in_string = False
        escape = False
        i = 0
        while i < len(line):
            ch = line[i]
            if escape:
                new_line.append(ch)
                escape = False
                i += 1
                continue
            if ch == '\\' and in_string:
                new_line.append(ch)
                escape = True
                i += 1
                continue
            if ch == '"':
                in_string = not in_string
                new_line.append(ch)
                i += 1
                continue
            if not in_string and ch == '/' and i + 1 < len(line) and line[i + 1] == '/':
                break
            new_line.append(ch)
            i += 1
        result.append(''.join(new_line))
    return '\n'.join(result)


def strip_trailing_commas(text: str) -> str:
    """去除 JSON 中的尾随逗号（} 或 ] 前的逗号）"""
    # 匹配逗号后面跟着可选空白和 } 或 ]
    return re.sub(r',(\s*[}\]])', r'\1', text)


def load_json(file_path: str | Path) -> dict:
    """读取带注释的 JSON 文件并解析，自动修正常见格式问题并记录警告"""
    path = Path(file_path)
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
        parse_warnings.append(msg)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(
            f"{path}: {e.msg}",
            e.doc,
            e.pos,
        ) from None


def dump_json(data: dict, file_path: str | Path):
    """将数据写入 JSON 文件（标准格式，无注释）"""
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=4),
        encoding='utf-8'
    )
