"""
Steam 本地时间戳读取 - 从 .acf 文件获取游戏和 Mod 的更新时间
"""
from datetime import datetime, timezone
from pathlib import Path


def utc_timestamp(date_str: str) -> int:
    """将 'YYYY-MM-DD' 格式的日期转换为 UTC 零点的 Unix 时间戳。

    用于定义人类可读的版本更新时间常量，例如::

        MAJOR_UPDATE_TS = utc_timestamp("2026-03-31")
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


# 最近一次大版本更新日期
MAJOR_UPDATE_TS = utc_timestamp("2026-03-31")


def _parse_vdf(text: str) -> dict[str, object]:
    """简易 VDF/ACF 递归解析器，返回嵌套字典。"""
    pos = 0
    length = len(text)

    def skip_ws() -> None:
        nonlocal pos
        while pos < length and text[pos] in ' \t\r\n':
            pos += 1

    def read_string() -> str:
        nonlocal pos
        if pos >= length or text[pos] != '"':
            raise ValueError(f"预期引号，位置 {pos}")
        pos += 1
        start = pos
        while pos < length and text[pos] != '"':
            pos += 1
        s = text[start:pos]
        pos += 1  # 跳过结束引号
        return s

    def read_dict() -> dict[str, object]:
        nonlocal pos
        result: dict[str, object] = {}
        skip_ws()
        if pos < length and text[pos] == '{':
            pos += 1
        while True:
            skip_ws()
            if pos >= length or text[pos] == '}':
                pos += 1
                break
            key = read_string()
            skip_ws()
            if pos < length and text[pos] == '{':
                result[key] = read_dict()
            else:
                result[key] = read_string()
            skip_ws()
        return result

    # 顶层：key 后跟 {dict}
    skip_ws()
    root_key = read_string()
    skip_ws()
    return {root_key: read_dict()}


def get_steamapps_from_workshop(workshop_path: Path) -> Path:
    """从 workshop_path (.../steamapps/workshop/content/3117820) 反推 steamapps 路径。"""
    return workshop_path.parent.parent.parent


def get_game_update_time(steamapps: Path, app_id: str = "3117820") -> int | None:
    """读取 appmanifest_{app_id}.acf 的 LastUpdated，失败返回 None。"""
    acf = steamapps / f"appmanifest_{app_id}.acf"
    if not acf.exists():
        return None
    try:
        data = _parse_vdf(acf.read_text(encoding="utf-8"))
        return int(data["AppState"]["LastUpdated"])  # type: ignore[index]
    except Exception:
        return None


def get_mod_update_times(steamapps: Path, app_id: str = "3117820") -> dict[str, int]:
    """读取 appworkshop_{app_id}.acf，返回 {mod_id: timeupdated}。失败返回空字典。"""
    acf = steamapps / "workshop" / f"appworkshop_{app_id}.acf"
    if not acf.exists():
        return {}
    try:
        data = _parse_vdf(acf.read_text(encoding="utf-8"))
        items: dict[str, object] = data["AppWorkshop"]["WorkshopItemsInstalled"]  # type: ignore[index]
        result: dict[str, int] = {}
        for mod_id, info in items.items():
            if isinstance(info, dict) and "timeupdated" in info:
                result[mod_id] = int(str(info["timeupdated"]))
        return result
    except Exception:
        return {}
