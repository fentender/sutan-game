"""DataManager — 全局数据所有者单例"""
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from sultan_core.json import JsonDoc
from sultan_core.delta import DeltaDict, serialize_delta

from ...config import ConfigChangeEvent, HISTORY_DIR, MOD_OVERRIDES_DIR, UserConfig
from ..infra.diagnostics import diag
from ..infra.profiler import profile
from ..infra.types import ParseFailure, normalize_rel_path
from ..platform.history import (
    PathResolver,
    find_base_version,
    parse_history_versions,
    resolve_path,
    set_resolver,
)

from .models import GameData, GameDataType


class DataManager:
    """全局数据所有者单例"""

    _instance: "DataManager | None" = None

    def __init__(self) -> None:
        self._base = GameData(data_id="", data_type=GameDataType.BASE)
        self._mods: dict[str, GameData] = {}
        self._history_bases: dict[str, GameData] = {}
        self._mod_history_map: dict[str, str] = {}
        self._overrides_dir: Path | None = None
        self._failures: list[ParseFailure] = []
        self._ignored_failures: list[ParseFailure] = []

    @classmethod
    def instance(cls) -> "DataManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 初始化 ──

    @profile
    def init(
        self,
        config: UserConfig,
        mod_configs: list[tuple[str, str, Path]],
        mod_update_times: dict[str, int] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        config.register_listener(self._on_config_changed)

        game_config_path = config.game_config_path
        overrides_dir = MOD_OVERRIDES_DIR
        enabled_mod_ids = config.enabled_mods

        self._base = GameData(
            data_id="", data_type=GameDataType.BASE,
            config_path=game_config_path,
        )
        self._mods.clear()
        self._history_bases.clear()
        self._mod_history_map.clear()
        self._failures.clear()
        self._ignored_failures.clear()
        self._overrides_dir = overrides_dir
        for mod_gd in self._mods.values():
            mod_gd.override.clear()

        tasks: list[tuple[Path, str, str, str, str]] = []

        if game_config_path.exists():
            for json_file in game_config_path.rglob("*.json"):
                rel = normalize_rel_path(json_file, game_config_path)
                tasks.append((json_file, rel, "base", "", ""))

        for mod_id, mod_name, config_path in mod_configs:
            gd = GameData(
                data_id=mod_id, data_type=GameDataType.MOD,
                config_path=config_path,
            )
            gd.info.name = mod_name
            self._mods[mod_id] = gd
            if not config_path.exists():
                continue
            for json_file in config_path.rglob("*.json"):
                rel = normalize_rel_path(json_file, config_path)
                tasks.append((json_file, rel, "mod", mod_id, mod_name))

        mod_ids = [mid for mid, _, _ in mod_configs]
        tasks.extend(self._collect_history_tasks(
            HISTORY_DIR, mod_update_times, mod_ids,
        ))
        tasks.extend(self._collect_override_tasks(
            overrides_dir, enabled_mod_ids or [],
        ))

        self._batch_load(tasks, on_progress)

    def _batch_load(
        self,
        tasks: list[tuple[Path, str, str, str, str]],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> None:
        if not tasks:
            return

        paths = [str(t[0]) for t in tasks]
        handle = JsonDoc.batch_parse_files(paths)

        while not handle.done():
            if on_progress:
                on_progress(handle.completed(), handle.total())
            time.sleep(0.03)
        handle.wait()

        if on_progress:
            on_progress(handle.total(), handle.total())

        for i, (file_path, rel_path, category, owner_id, mod_name) in enumerate(tasks):
            err = handle.error(i)
            if err:
                if category == "override":
                    diag.warn("override", f"override 文件解析失败: {file_path}")
                elif category == "history":
                    pass
                else:
                    diag.error("json", f"{file_path}: JSON 解析失败 ({err})")
                    self._failures.append(ParseFailure(
                        file_path=file_path, rel_path=rel_path,
                        error_msg=err, error_line=0,
                        is_base=(category == "base"),
                        mod_id=owner_id, mod_name=mod_name,
                    ))
                continue

            doc = handle.take_doc(i)
            if not doc.valid():
                continue

            match category:
                case "base":
                    self._base.config.set(rel_path, doc)
                case "mod":
                    self._mods[owner_id].config.set(rel_path, doc)
                case "history":
                    self._history_bases[owner_id].config.set(rel_path, doc)
                case "override":
                    self._mods[owner_id].override.set_raw(rel_path, doc)

    # ── 历史版本收集 ──

    def _collect_history_tasks(
        self,
        history_dir: Path | None,
        mod_update_times: dict[str, int] | None,
        mod_ids: list[str],
    ) -> list[tuple[Path, str, str, str, str]]:
        if history_dir is None:
            set_resolver(None)
            return []

        set_resolver(PathResolver(history_dir))
        history_versions = parse_history_versions(history_dir)
        if not history_versions or mod_update_times is None:
            return []

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

        tasks: list[tuple[Path, str, str, str, str]] = []
        for ver_key, ver_mod_ids in version_to_mods.items():
            ver_dir = Path(ver_key)
            gd = GameData(
                data_id=ver_dir.name,
                data_type=GameDataType.BASE,
                config_path=ver_dir,
            )
            self._history_bases[ver_key] = gd

            needed_files: set[str] = set()
            for mod_id in ver_mod_ids:
                mod_gd = self._mods.get(mod_id)
                if mod_gd is not None:
                    needed_files.update(mod_gd.config.rel_paths())

            for rel_path in needed_files:
                hist_file = resolve_path(ver_dir, rel_path)
                if hist_file is None:
                    continue
                tasks.append((hist_file, rel_path, "history", ver_key, ""))

        return tasks

    # ── Override 收集 ──

    def _collect_override_tasks(
        self,
        overrides_dir: Path | None,
        enabled_mod_ids: list[str],
    ) -> list[tuple[Path, str, str, str, str]]:
        if overrides_dir is None or not overrides_dir.exists():
            return []

        tasks: list[tuple[Path, str, str, str, str]] = []
        for mod_id in enabled_mod_ids:
            mod_dir = overrides_dir / mod_id
            if not mod_dir.exists():
                continue
            gd = self._mods.get(mod_id)
            if gd is None:
                continue
            for json_file in mod_dir.rglob("*.json"):
                rel = normalize_rel_path(json_file, mod_dir)
                tasks.append((json_file, rel, "override", mod_id, ""))

        return tasks

    # ── 数据访问（历史版本 base） ──

    def get_history_base(self, mod_id: str, rel_path: str) -> JsonDoc | None:
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

    _EMPTY_DOC: JsonDoc = JsonDoc.parse("{}")

    def get_base(self, rel_path: str) -> JsonDoc:
        return self._base.config.get(rel_path) or self._EMPTY_DOC

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

    def get_mod(self, mod_id: str, rel_path: str) -> JsonDoc:
        doc = self._mods[mod_id].config.get(rel_path)
        if doc is None:
            raise KeyError(f"{mod_id}/{rel_path}")
        return doc

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

    def set_mod(self, mod_id: str, rel_path: str, doc: JsonDoc) -> None:
        if mod_id not in self._mods:
            self._mods[mod_id] = GameData(
                data_id=mod_id, data_type=GameDataType.MOD,
            )
        self._mods[mod_id].config.set(rel_path, doc)

    def remove_mod_file(self, mod_id: str, rel_path: str) -> None:
        gd = self._mods.get(mod_id)
        if gd is not None:
            gd.config.remove(rel_path)

    def reload_mod(self, mod_id: str) -> None:
        gd = self._mods.get(mod_id)
        if gd is None or gd.config_path is None or not gd.config_path.exists():
            return
        config_path = gd.config_path
        gd.config.clear()
        mod_name = gd.info.name or mod_id
        tasks: list[tuple[Path, str, str, str, str]] = []
        for json_file in config_path.rglob("*.json"):
            rel = normalize_rel_path(json_file, config_path)
            tasks.append((json_file, rel, "mod", mod_id, mod_name))
        self._batch_load(tasks)

    # ── 错误管理 ──

    def take_failures(self) -> list[ParseFailure]:
        failures = self._failures
        self._failures = []
        return failures

    def set_ignored_failures(self, failures: list[ParseFailure]) -> None:
        self._ignored_failures = list(failures)

    def get_ignored_failures(self) -> list[ParseFailure]:
        return list(self._ignored_failures)

    # ── Override 管理 ──

    def load_overrides(self, overrides_dir: Path, enabled_mod_ids: list[str]) -> None:
        for mod_gd in self._mods.values():
            mod_gd.override.clear()
        self._overrides_dir = overrides_dir

        tasks = self._collect_override_tasks(overrides_dir, enabled_mod_ids)
        self._batch_load(tasks)

    def get_override_node(self, mod_id: str, rel_path: str) -> DeltaDict | None:
        gd = self._mods.get(mod_id)
        if gd is None:
            return None
        return gd.override.get_node(rel_path)

    def has_override(self, mod_id: str, rel_path: str) -> bool:
        gd = self._mods.get(mod_id)
        if gd is None:
            return False
        return gd.override.has(rel_path)

    def set_override_node(self, mod_id: str, rel_path: str, node: DeltaDict) -> None:
        gd = self._mods.get(mod_id)
        if gd is None:
            return
        gd.override.set_node(rel_path, node)

        if self._overrides_dir is not None:
            override_file = self._overrides_dir / mod_id / rel_path
            override_file.parent.mkdir(parents=True, exist_ok=True)
            doc = serialize_delta(node)
            override_file.write_text(doc.to_string(), encoding="utf-8")

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

        return existed

    def invalidate_overrides(self, mod_ids: set[str]) -> list[str]:
        deleted: list[str] = []
        for mod_id in mod_ids:
            had_overrides = False
            gd = self._mods.get(mod_id)
            if gd is not None:
                if gd.override.rel_paths():
                    had_overrides = True
                gd.override.clear()

            had_dir = False
            if self._overrides_dir is not None:
                override_dir = self._overrides_dir / mod_id
                if override_dir.exists():
                    shutil.rmtree(override_dir)
                    had_dir = True

            if had_overrides or had_dir:
                deleted.append(mod_id)

        return deleted

    # ── Config 变更回调 ──

    def _on_config_changed(self, event: ConfigChangeEvent) -> None:
        if event.changed_fields & {'merge_mode', 'mod_merge_modes'}:
            all_mod_ids = set(self._mods.keys())
            deleted = self.invalidate_overrides(all_mod_ids)
            if deleted:
                diag.info("config", f"合并模式变更，已清理覆盖编辑: {'、'.join(deleted)}")
        elif event.changed_fields & {'mod_order', 'enabled_mods'}:
            stale_ids = self._compute_stale_mods(event)
            if stale_ids:
                deleted = self.invalidate_overrides(stale_ids)
                if deleted:
                    diag.info("config", f"Mod 排序/启用变化，已清理覆盖编辑: {'、'.join(deleted)}")

    def _compute_stale_mods(self, event: ConfigChangeEvent) -> set[str]:
        old_order: list[str] = event.old_values.get('mod_order', [])  # type: ignore[assignment]
        old_enabled: list[str] = event.old_values.get('enabled_mods', [])  # type: ignore[assignment]
        new_order: list[str] = event.new_values.get('mod_order', [])  # type: ignore[assignment]
        new_enabled: list[str] = event.new_values.get('enabled_mods', [])  # type: ignore[assignment]
        if 'mod_order' not in event.changed_fields:
            old_order = new_order = list(new_order)
        if 'enabled_mods' not in event.changed_fields:
            old_enabled = new_enabled = list(new_enabled)

        old_set = set(old_enabled)
        old_eo = [m for m in old_order if m in old_set]
        new_set = set(new_enabled)
        new_eo = [m for m in new_order if m in new_set]

        diverge = min(len(old_eo), len(new_eo))
        for i in range(diverge):
            if old_eo[i] != new_eo[i]:
                diverge = i
                break
        return set(old_eo[diverge:]) | set(new_eo[diverge:])

    # ── Reload ──

    def reload(self, paths: list[Path]) -> list[ParseFailure]:
        tasks: list[tuple[Path, str, str, str, str]] = []

        for file_path in paths:
            base_cfg = self._base.config_path
            if base_cfg and self._is_under(file_path, base_cfg):
                rel = normalize_rel_path(file_path, base_cfg)
                self._base.config.remove(rel)
                tasks.append((file_path, rel, "base", "", ""))
            else:
                for mod_id, gd in self._mods.items():
                    if gd.config_path and self._is_under(file_path, gd.config_path):
                        rel = normalize_rel_path(file_path, gd.config_path)
                        gd.config.remove(rel)
                        tasks.append((
                            file_path, rel, "mod",
                            mod_id, gd.info.name or mod_id,
                        ))
                        break

        self._batch_load(tasks)
        return self.take_failures()

    # ── 版本查询 ──

    def config_version(self, mod_id: str, rel_path: str) -> int:
        if mod_id == "":
            return self._base.config.version_of(rel_path)
        gd = self._mods.get(mod_id)
        if gd is None:
            return 0
        return gd.config.version_of(rel_path)

    def override_version(self, mod_id: str, rel_path: str) -> int:
        gd = self._mods.get(mod_id)
        if gd is None:
            return 0
        return gd.override.version_of(rel_path)

    # ── 生命周期 ──

    def clear(self) -> None:
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
