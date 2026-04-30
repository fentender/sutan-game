"""历史 config 版本管理 — 解析、匹配、CAS 路径解析"""
import json
from datetime import datetime, timezone
from pathlib import Path


def parse_history_versions(history_dir: Path) -> list[tuple[int, Path]]:
    """扫描历史版本目录，返回 [(unix_ts, path)] 按时间升序。

    兼容两种布局：
    - 原始布局：history_dir/config_YYYY.MM.DD/
    - CAS 布局：history_dir/versions/config_YYYY.MM.DD/
    """
    versions: list[tuple[int, Path]] = []
    if not history_dir.exists():
        return versions

    # 优先尝试 CAS 布局
    versions_root = history_dir / "versions"
    scan_root = versions_root if versions_root.is_dir() else history_dir

    for d in scan_root.iterdir():
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


# ── CAS 路径解析 ──────────────────────────────────────────────────────


class PathResolver:
    """版本 config 的路径解析器。

    游戏本体及非 CAS 布局：原样拼接返回 base_dir / rel_path。
    CAS 版本目录（位于 versions_dir 下）：查 manifest，返回 blob 实际路径。
    """

    def __init__(self, history_dir: Path) -> None:
        self.history_dir = history_dir
        self.blobs_dir = history_dir / "blobs"
        self.versions_dir = history_dir / "versions"
        self._is_cas = self.blobs_dir.is_dir() and self.versions_dir.is_dir()
        self._manifests: dict[str, dict[str, str]] = {}

    def resolve(self, base_dir: Path, rel_path: str) -> Path | None:
        """返回 rel_path 在 base_dir 版本下的真实文件路径；不存在返回 None。"""
        if self._is_cas:
            try:
                base_dir.relative_to(self.versions_dir)
                is_cas_version = True
            except ValueError:
                is_cas_version = False

            if is_cas_version:
                manifest = self._load_manifest(base_dir.name)
                h = manifest.get(rel_path.replace("\\", "/"))
                if h is None:
                    return None
                blob = self.blobs_dir / h[:2] / f"{h[2:]}.json"
                return blob if blob.exists() else None

        # 游戏本体或非 CAS 布局 → 原样拼接
        p = base_dir / rel_path
        return p if p.exists() else None

    def _load_manifest(self, version_name: str) -> dict[str, str]:
        if version_name not in self._manifests:
            path = self.versions_dir / version_name / ".manifest.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            self._manifests[version_name] = data
        return self._manifests[version_name]


# 全局 resolver。app 启动时调 set_resolver 初始化；不初始化则走原路径拼接。
_resolver: PathResolver | None = None


def set_resolver(r: PathResolver | None) -> None:
    global _resolver
    _resolver = r


def resolve_path(base_dir: Path, rel_path: str) -> Path | None:
    """统一的路径解析入口。delta_store 通过该函数读历史文件。"""
    if _resolver is not None:
        return _resolver.resolve(base_dir, rel_path)
    p = base_dir / rel_path
    return p if p.exists() else None
