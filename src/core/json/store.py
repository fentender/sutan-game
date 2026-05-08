"""
JSON I/O 工具 — 仅供 DataManager 内部使用

文件解析和 I/O 缓存由本模块管理。数据存储由 DataManager 负责。
外部模块不应直接使用 JsonStore，统一通过 DataManager 访问。
"""
import json
from pathlib import Path
from typing import cast

from src.accel._fast_json import (
    pairs_hook as _pairs_hook,
)

from ..infra.diagnostics import diag
from ..infra.profiler import profile
from ..infra.types import JsonObject
from .parser import clean_json_text


class JsonStore:
    """JSON I/O 工具单例（仅供 DataManager 内部使用）。

    - parse_file(): 无缓存解析
    - load_cached() / _load_json(): 带缓存解析
    - invalidate_cache(): 清除指定路径缓存
    """

    _instance: JsonStore | None = None

    def __init__(self) -> None:
        self._json_cache: dict[str, JsonObject] = {}
        self._mtime_cache: dict[str, float] = {}

    @classmethod
    def instance(cls) -> JsonStore:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── I/O：文件解析 ──

    @staticmethod
    @profile
    def parse_file(
        file_path: str | Path, *, clean: bool = True, dupkey: bool = True,
    ) -> JsonObject:
        """解析单个 JSON 文件（静态方法，无缓存）。"""
        path = Path(file_path)
        raw_bytes = path.read_bytes()

        if raw_bytes.startswith(b'\xef\xbb\xbf'):
            diag.warn("parse", f"{path.name}: 已自动修正 [UTF-8 BOM]")
            raw_bytes = raw_bytes[3:]

        text = raw_bytes.decode('utf-8')
        if clean:
            text = clean_json_text(text)
        hook = _pairs_hook if dupkey else None
        return cast(JsonObject, json.loads(text, object_pairs_hook=hook))

    @profile
    def load_cached(
        self, file_path: Path, *, clean: bool = True, dupkey: bool = True,
        check_mtime: bool = False,
    ) -> JsonObject:
        """带缓存的文件加载。"""
        return self._load_json(file_path, clean=clean, dupkey=dupkey, check_mtime=check_mtime)

    def invalidate_cache(self, file_path: Path) -> None:
        """清除指定路径的 I/O 缓存"""
        ps = str(file_path)
        self._json_cache.pop(ps, None)
        self._mtime_cache.pop(ps, None)

    @profile
    def _load_json(
        self, file_path: Path, *, clean: bool = True, dupkey: bool = True,
        check_mtime: bool = False,
    ) -> JsonObject:
        """带缓存的文件加载（私有）。"""
        path_str = str(file_path)

        if path_str in self._json_cache:
            if not check_mtime:
                return self._json_cache[path_str]
            current_mtime = file_path.stat().st_mtime
            if current_mtime == self._mtime_cache.get(path_str):
                return self._json_cache[path_str]

        mtime = file_path.stat().st_mtime
        result = JsonStore.parse_file(file_path, clean=clean, dupkey=dupkey)
        self._json_cache[path_str] = result
        self._mtime_cache[path_str] = mtime
        return result

    # ── 生命周期 ──

    def clear(self) -> None:
        self._json_cache.clear()
        self._mtime_cache.clear()
