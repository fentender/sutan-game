"""
数据管理模块 — 管理所有 GameData 生命周期

DataManager 是全局数据所有者，持有本体和所有 Mod 的 GameData 实例。
JsonStore 退化为纯 I/O 工具，数据存储完全由本模块负责。
"""
import builtins
import enum
import json
import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from .infra.diagnostics import diag
from .infra.profiler import profile
from .infra.types import DictFieldDiff, JsonObject, ParseFailure, normalize_rel_path

# ── 数据类型定义 ──


class GameDataType(enum.Enum):
    BASE = "base"
    MOD = "mod"


@dataclass
class ConfigItem:
    """单个 JSON 配置文件条目"""
    data: JsonObject
    version: int = 0


class ConfigData:
    """config/ 目录下所有 JSON 资源，per-file version"""

    def __init__(self) -> None:
        self._items: dict[str, ConfigItem] = {}

    def get(self, rel_path: str) -> JsonObject | None:
        item = self._items.get(rel_path)
        return item.data if item is not None else None

    def has(self, rel_path: str) -> bool:
        return rel_path in self._items

    def set(self, rel_path: str, data: JsonObject) -> None:
        if rel_path in self._items:
            self._items[rel_path].data = data
            self._items[rel_path].version += 1
        else:
            self._items[rel_path] = ConfigItem(data=data, version=1)

    def remove(self, rel_path: str) -> None:
        self._items.pop(rel_path, None)

    def version_of(self, rel_path: str) -> int:
        item = self._items.get(rel_path)
        return item.version if item is not None else 0

    def rel_paths(self) -> builtins.set[str]:
        return builtins.set(self._items.keys())

    def keys(self) -> list[str]:
        return list(self._items.keys())

    def clear(self) -> None:
        self._items.clear()


class OverrideData:
    """override delta 数据。内部复用 ConfigData 存 raw JSON，额外缓存 DictFieldDiff"""

    def __init__(self) -> None:
        self._store = ConfigData()
        self._cache: dict[str, DictFieldDiff] = {}

    def get(self, rel_path: str) -> DictFieldDiff | None:
        if rel_path in self._cache:
            return self._cache[rel_path]
        raw = self._store.get(rel_path)
        if raw is None:
            return None
        delta = DictFieldDiff.from_delta_dict(raw)
        self._cache[rel_path] = delta
        return delta

    def set(self, rel_path: str, delta: DictFieldDiff) -> None:
        self._store.set(rel_path, cast(JsonObject, delta.to_delta_dict()))
        self._cache[rel_path] = delta

    def set_raw(self, rel_path: str, raw: JsonObject) -> None:
        self._store.set(rel_path, raw)
        self._cache.pop(rel_path, None)

    def has(self, rel_path: str) -> bool:
        return self._store.has(rel_path)

    def remove(self, rel_path: str) -> bool:
        self._cache.pop(rel_path, None)
        existed = self._store.has(rel_path)
        self._store.remove(rel_path)
        return existed

    def version_of(self, rel_path: str) -> int:
        return self._store.version_of(rel_path)

    def rel_paths(self) -> builtins.set[str]:
        return self._store.rel_paths()

    def clear(self) -> None:
        self._store.clear()
        self._cache.clear()


@dataclass
class ModMetadata:
    """info.json 元数据"""
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    mod_version: str = ""
    update_time: int | None = None


@dataclass
class ImageData:
    """image/ 目录下图片资源"""
    files: dict[str, Path] = field(default_factory=dict)


@dataclass
class BGMData:
    """BGM/ 目录下音频资源"""
    files: dict[str, Path] = field(default_factory=dict)


@dataclass
class OtherData:
    """其它资源"""
    files: dict[str, Path] = field(default_factory=dict)


@dataclass
class GameData:
    """单个游戏本体或 Mod 的完整数据容器"""
    data_id: str
    data_type: GameDataType

    path: Path | None = None
    config_path: Path | None = None
    preview_path: Path | None = None

    info: ModMetadata = field(default_factory=ModMetadata)
    config: ConfigData = field(default_factory=ConfigData)
    image: ImageData = field(default_factory=ImageData)
    bgm: BGMData = field(default_factory=BGMData)
    other: OtherData = field(default_factory=OtherData)
    override: OverrideData = field(default_factory=OverrideData)


# ── DataManager 单例 ──


class DataManager:
    """全局数据所有者单例"""

    _instance: DataManager | None = None

    def __init__(self) -> None:
        self._base = GameData(data_id="", data_type=GameDataType.BASE)
        self._mods: dict[str, GameData] = {}
        self._history_bases: dict[str, GameData] = {}
        self._mod_history_map: dict[str, str] = {}
        self._overrides_dir: Path | None = None
        self._failures: list[ParseFailure] = []
        self._ignored_failures: list[ParseFailure] = []
        self._lock = threading.Lock()
        self._on_override_change: Callable[[str], None] | None = None

    @classmethod
    def instance(cls) -> DataManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 文件解析（委托 JsonStore） ──

    @staticmethod
    def parse_file(
        file_path: str | Path, *, clean: bool = True, dupkey: bool = True,
    ) -> JsonObject:
        from .json.store import JsonStore
        return JsonStore.parse_file(file_path, clean=clean, dupkey=dupkey)

    # ── 初始化 ──

    @profile
    def init(
        self,
        game_config_path: Path,
        mod_configs: list[tuple[str, str, Path]],
        history_dir: Path | None = None,
        mod_update_times: dict[str, int] | None = None,
    ) -> None:
        from .json.store import JsonStore
        store = JsonStore.instance()

        with self._lock:
            self._base = GameData(
                data_id="", data_type=GameDataType.BASE,
                config_path=game_config_path,
            )
            self._mods.clear()
            self._history_bases.clear()
            self._mod_history_map.clear()
            self._failures.clear()
            self._ignored_failures.clear()

        tasks: list[tuple[Path, str, bool, str, str]] = []

        if game_config_path.exists():
            for json_file in game_config_path.rglob("*.json"):
                rel = normalize_rel_path(json_file, game_config_path)
                tasks.append((json_file, rel, True, "", ""))

        for mod_id, mod_name, config_path in mod_configs:
            gd = GameData(
                data_id=mod_id, data_type=GameDataType.MOD,
                config_path=config_path,
            )
            gd.info.name = mod_name
            with self._lock:
                self._mods[mod_id] = gd
            if not config_path.exists():
                continue
            for json_file in config_path.rglob("*.json"):
                rel = normalize_rel_path(json_file, config_path)
                tasks.append((json_file, rel, False, mod_id, mod_name))

        self._load_tasks(tasks, store)

        self._init_history(
            history_dir, mod_update_times,
            [mid for mid, _, _ in mod_configs],
            store,
        )

    def _load_tasks(
        self,
        tasks: list[tuple[Path, str, bool, str, str]],
        store: object,
    ) -> None:
        if len(tasks) <= 20:
            for task in tasks:
                self._load_single(task, store)
            return
        with ThreadPoolExecutor() as pool:
            futures = {pool.submit(self._load_single, t, store): t for t in tasks}
            for future in as_completed(futures):
                future.result()

    def _load_single(
        self,
        task: tuple[Path, str, bool, str, str],
        store: object,
    ) -> None:
        from .json.store import JsonStore
        file_path, rel_path, is_base, mod_id, mod_name = task
        try:
            data = cast(JsonStore, store).load_cached(file_path)
        except json.JSONDecodeError as e:
            diag.error("json", f"{file_path}: JSON 解析失败 ({e.msg})")
            failure = ParseFailure.from_error(
                e, file_path, rel_path,
                is_base=is_base, mod_id=mod_id, mod_name=mod_name,
            )
            with self._lock:
                self._failures.append(failure)
            return

        with self._lock:
            if is_base:
                self._base.config.set(rel_path, data)
            else:
                self._mods[mod_id].config.set(rel_path, data)

    # ── 历史版本初始化 ──

    def _init_history(
        self,
        history_dir: Path | None,
        mod_update_times: dict[str, int] | None,
        mod_ids: list[str],
        store: object,
    ) -> None:
        from .json.store import JsonStore
        from .platform.history import (
            PathResolver,
            find_base_version,
            parse_history_versions,
            resolve_path,
            set_resolver,
        )

        if history_dir is None:
            set_resolver(None)
            return

        set_resolver(PathResolver(history_dir))
        history_versions = parse_history_versions(history_dir)
        if not history_versions or mod_update_times is None:
            return

        version_to_mods: dict[str, list[str]] = {}
        for mod_id in mod_ids:
            ut = mod_update_times.get(mod_id)
            if ut is None:
                continue
            base_ver = find_base_version(ut, history_versions)
            if base_ver is None:
                continue
            ver_key = str(base_ver)
            self._mod_history_map[mod_id] = ver_key
            version_to_mods.setdefault(ver_key, []).append(mod_id)

        st = cast(JsonStore, store)
        for ver_key, ver_mod_ids in version_to_mods.items():
            ver_dir = Path(ver_key)
            gd = GameData(
                data_id=ver_dir.name,
                data_type=GameDataType.BASE,
                config_path=ver_dir,
            )

            needed_files: set[str] = set()
            for mod_id in ver_mod_ids:
                mod_gd = self._mods.get(mod_id)
                if mod_gd is not None:
                    needed_files.update(mod_gd.config.rel_paths())

            for rel_path in needed_files:
                hist_file = resolve_path(ver_dir, rel_path)
                if hist_file is None:
                    continue
                try:
                    data = st.load_cached(hist_file)
                except (json.JSONDecodeError, OSError):
                    continue
                gd.config.set(rel_path, data)

            self._history_bases[ver_key] = gd

    # ── 数据访问（历史版本 base） ──

    def get_history_base(self, mod_id: str, rel_path: str) -> JsonObject | None:
        ver_key = self._mod_history_map.get(mod_id)
        if ver_key is None:
            return None
        gd = self._history_bases.get(ver_key)
        if gd is None:
            return None
        return gd.config.get(rel_path)

    def has_history_base(self, mod_id: str) -> bool:
        return mod_id in self._mod_history_map

    # ── 数据访问（base） ──

    def get_base(self, rel_path: str) -> JsonObject:
        return self._base.config.get(rel_path) or {}

    def has_base(self, rel_path: str) -> bool:
        return self._base.config.has(rel_path)

    def base_rel_paths(self) -> set[str]:
        return self._base.config.rel_paths()

    def all_rel_paths(self) -> set[str]:
        paths = self._base.config.rel_paths()
        for gd in self._mods.values():
            paths.update(gd.config.rel_paths())
        return paths

    def game_config_path(self) -> Path | None:
        return self._base.config_path

    # ── 数据访问（mod） ──

    def get_mod(self, mod_id: str, rel_path: str) -> JsonObject:
        data = self._mods[mod_id].config.get(rel_path)
        if data is None:
            raise KeyError(f"{mod_id}/{rel_path}")
        return data

    def has_mod(self, mod_id: str, rel_path: str) -> bool:
        gd = self._mods.get(mod_id)
        if gd is None:
            return False
        return gd.config.has(rel_path)

    def mod_files(self, mod_id: str) -> list[str]:
        gd = self._mods.get(mod_id)
        if gd is None:
            return []
        return gd.config.keys()

    def mods_for_file(self, rel_path: str) -> list[str]:
        return [
            mod_id
            for mod_id, gd in self._mods.items()
            if gd.config.has(rel_path)
        ]

    def mod_name(self, mod_id: str) -> str:
        gd = self._mods.get(mod_id)
        if gd is None:
            return mod_id
        return gd.info.name or mod_id

    def mod_config_path(self, mod_id: str) -> Path:
        gd = self._mods[mod_id]
        assert gd.config_path is not None
        return gd.config_path

    # ── 数据修改 ──

    def set_mod(self, mod_id: str, rel_path: str, data: JsonObject) -> None:
        with self._lock:
            if mod_id not in self._mods:
                self._mods[mod_id] = GameData(
                    data_id=mod_id, data_type=GameDataType.MOD,
                )
            self._mods[mod_id].config.set(rel_path, data)

    def remove_mod_file(self, mod_id: str, rel_path: str) -> None:
        with self._lock:
            gd = self._mods.get(mod_id)
            if gd is not None:
                gd.config.remove(rel_path)

    def reload_mod(self, mod_id: str) -> None:
        from .json.store import JsonStore
        store = JsonStore.instance()

        gd = self._mods.get(mod_id)
        if gd is None or gd.config_path is None or not gd.config_path.exists():
            return
        config_path = gd.config_path
        with self._lock:
            gd.config.clear()
        mod_name = gd.info.name or mod_id
        tasks: list[tuple[Path, str, bool, str, str]] = []
        for json_file in config_path.rglob("*.json"):
            rel = normalize_rel_path(json_file, config_path)
            tasks.append((json_file, rel, False, mod_id, mod_name))
        self._load_tasks(tasks, store)

    # ── 错误管理 ──

    def take_failures(self) -> list[ParseFailure]:
        with self._lock:
            failures = self._failures.copy()
            self._failures.clear()
        return failures

    def set_ignored_failures(self, failures: list[ParseFailure]) -> None:
        with self._lock:
            self._ignored_failures = list(failures)

    def get_ignored_failures(self) -> list[ParseFailure]:
        with self._lock:
            return list(self._ignored_failures)

    # ── Override 管理 ──

    def set_on_override_change(self, callback: Callable[[str], None]) -> None:
        self._on_override_change = callback

    def _notify_override_change(self, rel_path: str) -> None:
        if self._on_override_change is not None:
            self._on_override_change(rel_path)

    def load_overrides(self, overrides_dir: Path, enabled_mod_ids: list[str]) -> None:
        from .json.store import JsonStore
        store = JsonStore.instance()

        with self._lock:
            for mod_gd in self._mods.values():
                mod_gd.override.clear()
            self._overrides_dir = overrides_dir

        if not overrides_dir.exists():
            return

        for mod_id in enabled_mod_ids:
            mod_dir = overrides_dir / mod_id
            if not mod_dir.exists():
                continue
            gd = self._mods.get(mod_id)
            if gd is None:
                continue
            for json_file in mod_dir.rglob("*.json"):
                rel = normalize_rel_path(json_file, mod_dir)
                try:
                    raw = store.load_cached(
                        json_file, clean=False, dupkey=False, check_mtime=True,
                    )
                except (json.JSONDecodeError, OSError):
                    diag.warn("override", f"override 文件解析失败: {json_file}")
                    continue
                gd.override.set_raw(rel, raw)

    def get_override(self, mod_id: str, rel_path: str) -> DictFieldDiff | None:
        gd = self._mods.get(mod_id)
        if gd is None:
            return None
        return gd.override.get(rel_path)

    def has_override(self, mod_id: str, rel_path: str) -> bool:
        gd = self._mods.get(mod_id)
        if gd is None:
            return False
        return gd.override.has(rel_path)

    def set_override(self, mod_id: str, rel_path: str, delta: DictFieldDiff) -> None:
        gd = self._mods.get(mod_id)
        if gd is None:
            return
        gd.override.set(rel_path, delta)

        if self._overrides_dir is not None:
            override_file = self._overrides_dir / mod_id / rel_path
            override_file.parent.mkdir(parents=True, exist_ok=True)
            serialized = json.dumps(
                delta.to_delta_dict(), ensure_ascii=False, indent=2,
            )
            override_file.write_text(serialized, encoding="utf-8")

        self._notify_override_change(rel_path)

    def remove_override(self, mod_id: str, rel_path: str) -> bool:
        gd = self._mods.get(mod_id)
        existed = False
        if gd is not None:
            existed = gd.override.remove(rel_path)

        if self._overrides_dir is not None:
            override_file = self._overrides_dir / mod_id / rel_path
            if override_file.exists():
                override_file.unlink()
                existed = True
                if override_file.parent.exists() and not any(override_file.parent.iterdir()):
                    override_file.parent.rmdir()

        if existed:
            self._notify_override_change(rel_path)

        return existed

    def invalidate_overrides(self, mod_ids: set[str]) -> list[str]:
        deleted: list[str] = []
        affected_paths: set[str] = set()
        for mod_id in mod_ids:
            had_overrides = False
            gd = self._mods.get(mod_id)
            if gd is not None:
                mod_paths = gd.override.rel_paths()
                if mod_paths:
                    had_overrides = True
                    affected_paths.update(mod_paths)
                gd.override.clear()

            had_dir = False
            if self._overrides_dir is not None:
                override_dir = self._overrides_dir / mod_id
                if override_dir.exists():
                    shutil.rmtree(override_dir)
                    had_dir = True

            if had_overrides or had_dir:
                deleted.append(mod_id)

        if affected_paths:
            for rel_path in affected_paths:
                self._notify_override_change(rel_path)

        return deleted

    # ── Reload ──

    def reload(self, paths: list[Path]) -> list[ParseFailure]:
        from .json.store import JsonStore
        store = JsonStore.instance()

        for p in paths:
            store.invalidate_cache(p)

        tasks: list[tuple[Path, str, bool, str, str]] = []

        for file_path in paths:
            base_cfg = self._base.config_path
            if base_cfg and self._is_under(file_path, base_cfg):
                rel = normalize_rel_path(file_path, base_cfg)
                with self._lock:
                    self._base.config.remove(rel)
                tasks.append((file_path, rel, True, "", ""))
            else:
                for mod_id, gd in self._mods.items():
                    if gd.config_path and self._is_under(file_path, gd.config_path):
                        rel = normalize_rel_path(file_path, gd.config_path)
                        with self._lock:
                            gd.config.remove(rel)
                        tasks.append((
                            file_path, rel, False,
                            mod_id, gd.info.name or mod_id,
                        ))
                        break

        self._load_tasks(tasks, store)
        return self.take_failures()

    # ── 版本查询 ──

    def config_version(self, mod_id: str, rel_path: str) -> int:
        if mod_id == "":
            return self._base.config.version_of(rel_path)
        gd = self._mods.get(mod_id)
        if gd is None:
            return 0
        return gd.config.version_of(rel_path)

    # ── 生命周期 ──

    def clear(self) -> None:
        with self._lock:
            self._base = GameData(data_id="", data_type=GameDataType.BASE)
            self._mods.clear()
            self._history_bases.clear()
            self._mod_history_map.clear()
            self._failures.clear()
            self._ignored_failures.clear()
            self._overrides_dir = None

    @staticmethod
    def _is_under(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False
