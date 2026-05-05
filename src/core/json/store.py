"""
JSON I/O 工具 + 向后兼容门面

文件解析和 I/O 缓存由本模块管理。数据存储已迁移到 DataManager。
现有核心模块通过 JsonStore.instance().get_base() 等方法访问数据，
内部委托到 DataManager.instance()，保持向后兼容。
"""
import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

from src.accel._fast_json import (
    pairs_hook as _pairs_hook,
)

from ..infra.diagnostics import diag
from ..infra.profiler import profile
from ..infra.types import DictFieldDiff, JsonObject, ParseFailure
from .parser import clean_json_text


class JsonStore:
    """JSON I/O 工具 + 向后兼容门面单例。

    I/O 职责（本模块实现）：
    - parse_file(): 无缓存解析
    - load_cached() / _load_json(): 带缓存解析
    - invalidate_cache(): 清除指定路径缓存

    数据职责（委托 DataManager）：
    - init / get_base / get_mod / override 等全部委托
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
        """带缓存的文件加载（公共 API）。"""
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

    # ── 委托：初始化 ──

    def init(
        self,
        game_config_path: Path,
        mod_configs: list[tuple[str, str, Path]],
    ) -> None:
        from ..data_manager import DataManager
        DataManager.instance().init(game_config_path, mod_configs)

    # ── 委托：数据访问 ──

    def get_base(self, rel_path: str) -> JsonObject:
        from ..data_manager import DataManager
        return DataManager.instance().get_base(rel_path)

    def get_mod(self, mod_id: str, rel_path: str) -> JsonObject:
        from ..data_manager import DataManager
        return DataManager.instance().get_mod(mod_id, rel_path)

    def has_base(self, rel_path: str) -> bool:
        from ..data_manager import DataManager
        return DataManager.instance().has_base(rel_path)

    def has_mod(self, mod_id: str, rel_path: str) -> bool:
        from ..data_manager import DataManager
        return DataManager.instance().has_mod(mod_id, rel_path)

    def mod_files(self, mod_id: str) -> list[str]:
        from ..data_manager import DataManager
        return DataManager.instance().mod_files(mod_id)

    def all_rel_paths(self) -> set[str]:
        from ..data_manager import DataManager
        return DataManager.instance().all_rel_paths()

    def base_rel_paths(self) -> set[str]:
        from ..data_manager import DataManager
        return DataManager.instance().base_rel_paths()

    def mods_for_file(self, rel_path: str) -> list[str]:
        from ..data_manager import DataManager
        return DataManager.instance().mods_for_file(rel_path)

    def mod_name(self, mod_id: str) -> str:
        from ..data_manager import DataManager
        return DataManager.instance().mod_name(mod_id)

    def game_config_path(self) -> Path | None:
        from ..data_manager import DataManager
        return DataManager.instance().game_config_path()

    def mod_config_path(self, mod_id: str) -> Path:
        from ..data_manager import DataManager
        return DataManager.instance().mod_config_path(mod_id)

    # ── 委托：数据修改 ──

    def set_mod(self, mod_id: str, rel_path: str, data: JsonObject) -> None:
        from ..data_manager import DataManager
        DataManager.instance().set_mod(mod_id, rel_path, data)

    def remove_mod_file(self, mod_id: str, rel_path: str) -> None:
        from ..data_manager import DataManager
        DataManager.instance().remove_mod_file(mod_id, rel_path)

    def reload_mod(self, mod_id: str) -> None:
        from ..data_manager import DataManager
        DataManager.instance().reload_mod(mod_id)

    # ── 委托：错误管理 ──

    def take_failures(self) -> list[ParseFailure]:
        from ..data_manager import DataManager
        return DataManager.instance().take_failures()

    def set_ignored_failures(self, failures: list[ParseFailure]) -> None:
        from ..data_manager import DataManager
        DataManager.instance().set_ignored_failures(failures)

    def get_ignored_failures(self) -> list[ParseFailure]:
        from ..data_manager import DataManager
        return DataManager.instance().get_ignored_failures()

    def reload(self, paths: list[Path]) -> list[ParseFailure]:
        from ..data_manager import DataManager
        return DataManager.instance().reload(paths)

    # ── 委托：Override 管理 ──

    def set_on_override_change(self, callback: Callable[[str], None]) -> None:
        from ..data_manager import DataManager
        DataManager.instance().set_on_override_change(callback)

    def load_overrides(self, overrides_dir: Path, enabled_mod_ids: list[str]) -> None:
        from ..data_manager import DataManager
        DataManager.instance().load_overrides(overrides_dir, enabled_mod_ids)

    def get_override(self, mod_id: str, rel_path: str) -> DictFieldDiff | None:
        from ..data_manager import DataManager
        return DataManager.instance().get_override(mod_id, rel_path)

    def has_override(self, mod_id: str, rel_path: str) -> bool:
        from ..data_manager import DataManager
        return DataManager.instance().has_override(mod_id, rel_path)

    def set_override(self, mod_id: str, rel_path: str, delta: DictFieldDiff) -> None:
        from ..data_manager import DataManager
        DataManager.instance().set_override(mod_id, rel_path, delta)

    def remove_override(self, mod_id: str, rel_path: str) -> bool:
        from ..data_manager import DataManager
        return DataManager.instance().remove_override(mod_id, rel_path)

    def invalidate_overrides(self, mod_ids: set[str]) -> list[str]:
        from ..data_manager import DataManager
        return DataManager.instance().invalidate_overrides(mod_ids)

    # ── 生命周期 ──

    def clear(self) -> None:
        from ..data_manager import DataManager
        DataManager.instance().clear()
        self._json_cache.clear()
        self._mtime_cache.clear()

    @staticmethod
    def _is_under(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False
