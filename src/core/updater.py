"""
更新检查模块 - 通过 GitHub/Gitee Releases API 检查新版本
"""
import json
import urllib.request

from ..config import APP_VERSION, UPDATE_SOURCES
from .diagnostics import diag


def _parse_version(v: str) -> tuple[int, ...]:
    """将版本字符串解析为整数元组，如 'v1.0.1' -> (1, 0, 1)"""
    return tuple(int(x) for x in v.lstrip("vV").split("."))


def check_for_update(timeout: int = 8) -> dict | None:
    """检查是否有新版本可用

    依次尝试 UPDATE_SOURCES 中的各个源，第一个成功响应即决定结果。
    返回更新信息字典（有新版本时），或 None（已最新/全部失败）。
    """
    current = _parse_version(APP_VERSION)

    for source in UPDATE_SOURCES:
        try:
            result = _check_source(source, current, timeout)
            # 请求成功（无论是否有新版本），直接返回结果
            return result
        except Exception as e:
            diag.info("update", f"检查更新失败 [{source['name']}]: {e}")
            continue

    return None


def _check_source(source: dict, current: tuple[int, ...], timeout: int) -> dict | None:
    """从单个源检查更新，返回更新信息或 None

    返回 None 有两种含义：已是最新版本 / 该源请求失败。
    请求失败时抛异常由调用方处理。
    """
    req = urllib.request.Request(
        source["api"],
        headers={
            "Accept": "application/json",
            "User-Agent": "SuDanModMerger-UpdateCheck",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    tag = data.get("tag_name", "")
    if not tag:
        return None

    remote = _parse_version(tag)
    if remote <= current:
        return None

    # 有新版本
    return {
        "tag_name": tag,
        "name": data.get("name") or tag,
        "body": data.get("body") or "",
        "download_url": data.get("html_url") or source["releases_url"],
    }
