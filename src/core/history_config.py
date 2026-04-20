"""历史 config 版本管理 — 解析 history_config 目录，按 mod 更新时间匹配基准版本"""
from datetime import datetime, timezone
from pathlib import Path


def parse_history_versions(history_dir: Path) -> list[tuple[int, Path]]:
    """扫描 history_config/ 下的 config_YYYY.MM.DD 目录，返回 [(unix_ts, path)] 按时间升序。"""
    versions: list[tuple[int, Path]] = []
    if not history_dir.exists():
        return versions
    for d in history_dir.iterdir():
        if not d.is_dir() or not d.name.startswith("config_"):
            continue
        date_str = d.name[len("config_"):]
        try:
            dt = datetime.strptime(date_str, "%Y.%m.%d").replace(tzinfo=timezone.utc)
            versions.append((int(dt.timestamp()), d))
        except ValueError:
            continue
    versions.sort(key=lambda x: x[0])
    return versions


def find_base_version(mod_update_time: int, versions: list[tuple[int, Path]]) -> Path | None:
    """找到早于等于 mod_update_time 的最近一个历史版本目录。无匹配返回 None。"""
    best: Path | None = None
    for ts, path in versions:
        if ts <= mod_update_time:
            best = path
        else:
            break
    return best
