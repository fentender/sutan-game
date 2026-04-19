"""
全局 JSON 资源管理器单例

启动时扫描本体 + 所有 mod 的 config 目录，批量加载所有 JSON 文件。
提供只读数据访问，集中管理解析错误。其他模块通过 get_base() / get_mod()
获取数据，无需 try-except。

文件读取统一由本模块管理：
- parse_file() 静态方法：无缓存解析，供 store 初始化前的模块使用
- _load_json() 私有方法：带缓存的文件加载，store 内部使用
"""
import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import cast

from ..accel._fast_json import (
    pairs_hook as _pairs_hook,
)
from .diagnostics import diag
from .json_parser import (
    fix_missing_commas,
    strip_duplicate_commas,
    strip_js_comments,
    strip_trailing_commas,
)
from .profiler import profile
from .types import DiffDict, ParseFailure, normalize_rel_path


class JsonStore:
    """全局 JSON 资源管理器单例。

    典型用法::

        store = JsonStore.instance()
        # 后台线程中初始化
        store.init(game_config_path, mod_configs)
        # 主线程处理错误
        failures = store.take_failures()
        # 其他模块直接取数据
        base = store.get_base("cards.json")  # {} 如果本体无此文件
        mod = store.get_mod("12345", "cards.json")
    """

    _instance: "JsonStore | None" = None

    def __init__(self) -> None:
        self._base_data: dict[str, dict[str, object]] = {}  # rel_path → data
        self._mod_data: dict[str, dict[str, dict[str, object]]] = {}  # mod_id → {rel_path → data}
        self._override_data: dict[str, dict[str, DiffDict]] = {}  # mod_id → {rel_path → delta}
        self._overrides_dir: Path | None = None
        self._mod_names: dict[str, str] = {}  # mod_id → mod_name
        self._mod_config_paths: dict[str, Path] = {}  # mod_id → config_path
        self._game_config_path: Path | None = None
        self._failures: list[ParseFailure] = []
        self._lock = threading.Lock()
        # JSON 解析缓存：(路径, mtime) → 解析结果
        self._json_cache: dict[tuple[str, float], dict[str, object]] = {}

    @classmethod
    def instance(cls) -> "JsonStore":
        """获取全局单例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 文件解析 ──

    @staticmethod
    def _parse_progressive(raw: str) -> dict[str, object]:
        """分级尝试解析 JSON 文本。

        按需逐步清洗，成功即停：
        1. 直接解析原始文本（仅无 // 时尝试）
        2. 仅去 // 注释
        3. 去注释 + 去尾随逗号
        4. 完整清洗
        """
        has_comment = '//' in raw

        if not has_comment:
            try:
                return cast(dict[str, object], json.loads(raw, object_pairs_hook=_pairs_hook))
            except json.JSONDecodeError:
                pass

        text = strip_js_comments(raw) if has_comment else raw
        try:
            return cast(dict[str, object], json.loads(text, object_pairs_hook=_pairs_hook))
        except json.JSONDecodeError:
            pass

        text = strip_trailing_commas(text)
        try:
            return cast(dict[str, object], json.loads(text, object_pairs_hook=_pairs_hook))
        except json.JSONDecodeError:
            pass

        text = fix_missing_commas(text)
        text = strip_duplicate_commas(text)
        return cast(dict[str, object], json.loads(text, object_pairs_hook=_pairs_hook))

    @staticmethod
    @profile
    def parse_file(file_path: str | Path) -> dict[str, object]:
        """解析单个 JSON 文件（静态方法，不依赖 store 初始化，无缓存）。

        处理 BOM、JS 注释、尾随逗号等非标准格式。
        供 store 初始化前的模块使用（如 schema_generator、mod_scanner）。
        解析失败抛出 json.JSONDecodeError。
        """
        path = Path(file_path)
        raw_bytes = path.read_bytes()

        if raw_bytes.startswith(b'\xef\xbb\xbf'):
            raw_bytes = raw_bytes[3:]

        raw = raw_bytes.decode('utf-8')
        return JsonStore._parse_progressive(raw)

    @profile
    def _load_json(self, file_path: Path) -> dict[str, object]:
        """带缓存的文件加载（私有）。

        缓存 key 为 (路径, mtime)，文件修改后自动失效。
        解析失败抛出 json.JSONDecodeError。
        """
        mtime = file_path.stat().st_mtime
        cache_key = (str(file_path), mtime)
        if cache_key in self._json_cache:
            return self._json_cache[cache_key]

        path = Path(file_path)
        raw_bytes = path.read_bytes()
        abnormal_fixes: list[str] = []

        if raw_bytes.startswith(b'\xef\xbb\xbf'):
            abnormal_fixes.append("UTF-8 BOM")
            raw_bytes = raw_bytes[3:]

        raw = raw_bytes.decode('utf-8')
        result = self._parse_progressive(raw)

        if abnormal_fixes:
            msg = f"{path.name}: 已自动修正 [{', '.join(abnormal_fixes)}]"
            diag.warn("parse", msg)

        self._json_cache[cache_key] = result
        return result

    # ── 初始化 ──

    def init(
        self,
        game_config_path: Path,
        mod_configs: list[tuple[str, str, Path]],
    ) -> None:
        """批量加载本体 + 所有 mod 的 config JSON。

        - 扫描 game_config_path 下所有 .json → _base_data
        - 扫描每个 mod 的 config_path 下所有 .json → _mod_data[mod_id]
        - 多线程并行加载
        - 解析失败记录到 _failures + diag.error
        - 可在后台线程中调用
        """
        with self._lock:
            self._base_data.clear()
            self._mod_data.clear()
            self._mod_names.clear()
            self._mod_config_paths.clear()
            self._failures.clear()
            self._game_config_path = game_config_path

        # 收集需要加载的任务：(file_path, rel_path, is_base, mod_id, mod_name)
        tasks: list[tuple[Path, str, bool, str, str]] = []

        # 本体文件
        if game_config_path.exists():
            for json_file in game_config_path.rglob("*.json"):
                rel = normalize_rel_path(json_file, game_config_path)
                tasks.append((json_file, rel, True, "", ""))

        # mod 文件
        for mod_id, mod_name, config_path in mod_configs:
            with self._lock:
                self._mod_names[mod_id] = mod_name
                self._mod_config_paths[mod_id] = config_path
                self._mod_data[mod_id] = {}
            if not config_path.exists():
                continue
            for json_file in config_path.rglob("*.json"):
                rel = normalize_rel_path(json_file, config_path)
                tasks.append((json_file, rel, False, mod_id, mod_name))

        # 多线程加载
        self._load_tasks(tasks)

    def _load_tasks(
        self,
        tasks: list[tuple[Path, str, bool, str, str]],
    ) -> None:
        """多线程执行加载任务"""
        # 少量文件直接串行
        if len(tasks) <= 20:
            for task in tasks:
                self._load_single(task)
            return

        with ThreadPoolExecutor() as pool:
            futures = {pool.submit(self._load_single, t): t for t in tasks}
            for future in as_completed(futures):
                # 异常已在 _load_single 内部处理，这里只确保不会丢失
                future.result()

    def _load_single(
        self,
        task: tuple[Path, str, bool, str, str],
    ) -> None:
        """加载单个文件并存入对应的数据结构"""
        file_path, rel_path, is_base, mod_id, mod_name = task
        try:
            data = self._load_json(file_path)
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
                self._base_data[rel_path] = data
            else:
                self._mod_data[mod_id][rel_path] = data

    # ── 数据访问 ──

    def get_base(self, rel_path: str) -> dict[str, object]:
        """获取本体文件数据（只读）。不存在返回 {}。"""
        return self._base_data.get(rel_path, {})

    def get_mod(self, mod_id: str, rel_path: str) -> dict[str, object]:
        """获取 mod 文件数据（只读）。不存在则 KeyError。"""
        return self._mod_data[mod_id][rel_path]

    def has_base(self, rel_path: str) -> bool:
        """本体是否有该文件"""
        return rel_path in self._base_data

    def has_mod(self, mod_id: str, rel_path: str) -> bool:
        """mod 是否有该文件（已成功加载）"""
        return rel_path in self._mod_data.get(mod_id, {})

    def mod_files(self, mod_id: str) -> list[str]:
        """获取 mod 的所有已加载 config 文件 rel_path 列表"""
        return list(self._mod_data.get(mod_id, {}).keys())

    def all_rel_paths(self) -> set[str]:
        """所有出现过的 rel_path（base + 所有 mod 的并集）"""
        paths = set(self._base_data.keys())
        for mod_files in self._mod_data.values():
            paths.update(mod_files.keys())
        return paths

    def base_rel_paths(self) -> set[str]:
        """本体的所有 rel_path 集合"""
        return set(self._base_data.keys())

    def mods_for_file(self, rel_path: str) -> list[str]:
        """哪些 mod 修改了该文件（返回 mod_id 列表）"""
        return [
            mod_id
            for mod_id, files in self._mod_data.items()
            if rel_path in files
        ]

    def mod_name(self, mod_id: str) -> str:
        """获取 mod 名称"""
        return self._mod_names.get(mod_id, mod_id)

    def game_config_path(self) -> Path | None:
        """获取本体 config 路径"""
        return self._game_config_path

    def mod_config_path(self, mod_id: str) -> Path:
        """获取 mod 的 config 目录路径"""
        return self._mod_config_paths[mod_id]

    # ── 数据修改（用于 ID 重分配等场景） ──

    def set_mod(self, mod_id: str, rel_path: str, data: dict[str, object]) -> None:
        """写入/覆盖 mod 文件数据"""
        with self._lock:
            if mod_id not in self._mod_data:
                self._mod_data[mod_id] = {}
            self._mod_data[mod_id][rel_path] = data

    def remove_mod_file(self, mod_id: str, rel_path: str) -> None:
        """删除单个 mod 文件记录"""
        with self._lock:
            if mod_id in self._mod_data:
                self._mod_data[mod_id].pop(rel_path, None)

    # ── 错误管理 ──

    def take_failures(self) -> list[ParseFailure]:
        """取出并清空所有 ParseFailure"""
        with self._lock:
            failures = self._failures.copy()
            self._failures.clear()
        return failures

    def reload(self, paths: list[Path]) -> list[ParseFailure]:
        """用户修复文件后，清除指定路径缓存并重新加载。

        返回仍然失败的文件列表。
        """
        self._json_cache.clear()
        tasks: list[tuple[Path, str, bool, str, str]] = []

        for file_path in paths:
            # 判断是本体还是 mod 文件
            if self._game_config_path and self._is_under(file_path, self._game_config_path):
                rel = normalize_rel_path(file_path, self._game_config_path)
                # 先从失败列表中移除旧记录
                with self._lock:
                    self._base_data.pop(rel, None)
                tasks.append((file_path, rel, True, "", ""))
            else:
                # 查找属于哪个 mod
                for mod_id, config_path in self._mod_config_paths.items():
                    if self._is_under(file_path, config_path):
                        rel = normalize_rel_path(file_path, config_path)
                        with self._lock:
                            if mod_id in self._mod_data:
                                self._mod_data[mod_id].pop(rel, None)
                        tasks.append((
                            file_path, rel, False,
                            mod_id, self._mod_names.get(mod_id, mod_id),
                        ))
                        break

        self._load_tasks(tasks)
        return self.take_failures()

    @staticmethod
    def _is_under(path: Path, parent: Path) -> bool:
        """检查 path 是否在 parent 目录下"""
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    # ── Override 管理 ──

    def load_overrides(self, overrides_dir: Path, enabled_mod_ids: list[str]) -> None:
        """扫描 overrides_dir，加载所有启用 mod 的 override delta 文件"""
        with self._lock:
            self._override_data.clear()
            self._overrides_dir = overrides_dir

        if not overrides_dir.exists():
            return

        for mod_id in enabled_mod_ids:
            mod_dir = overrides_dir / mod_id
            if not mod_dir.exists():
                continue
            for json_file in mod_dir.rglob("*.json"):
                rel = normalize_rel_path(json_file, mod_dir)
                try:
                    raw = json.loads(json_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    diag.warn("override", f"override 文件解析失败: {json_file}")
                    continue
                if not isinstance(raw, dict):
                    diag.warn("override", f"override 文件格式无效: {json_file}")
                    continue
                delta = DiffDict.from_delta_dict(raw)
                if delta is None:
                    # 旧格式，跳过并删除
                    diag.warn("override", f"旧格式 override 已清理: {json_file}")
                    json_file.unlink(missing_ok=True)
                    continue
                with self._lock:
                    if mod_id not in self._override_data:
                        self._override_data[mod_id] = {}
                    self._override_data[mod_id][rel] = delta

    def get_override(self, mod_id: str, rel_path: str) -> DiffDict | None:
        """获取 override delta，不存在返回 None"""
        return self._override_data.get(mod_id, {}).get(rel_path)

    def has_override(self, mod_id: str, rel_path: str) -> bool:
        """是否存在 override"""
        return rel_path in self._override_data.get(mod_id, {})

    def set_override(self, mod_id: str, rel_path: str, delta: DiffDict) -> None:
        """保存 override delta：更新内存 + 写磁盘 + 清合并缓存"""
        with self._lock:
            if mod_id not in self._override_data:
                self._override_data[mod_id] = {}
            self._override_data[mod_id][rel_path] = delta

        if self._overrides_dir is not None:
            override_file = self._overrides_dir / mod_id / rel_path
            override_file.parent.mkdir(parents=True, exist_ok=True)
            serialized = json.dumps(delta.to_delta_dict(), ensure_ascii=False, indent=2)
            override_file.write_text(serialized, encoding="utf-8")

        from .merge_cache import MergeCache
        MergeCache.instance().invalidate(rel_path)

    def remove_override(self, mod_id: str, rel_path: str) -> bool:
        """删除 override：清除内存 + 删磁盘 + 清合并缓存。返回是否存在并删除成功。"""
        existed = False
        with self._lock:
            mod_overrides = self._override_data.get(mod_id)
            if mod_overrides and rel_path in mod_overrides:
                del mod_overrides[rel_path]
                if not mod_overrides:
                    del self._override_data[mod_id]
                existed = True

        if self._overrides_dir is not None:
            override_file = self._overrides_dir / mod_id / rel_path
            if override_file.exists():
                override_file.unlink()
                existed = True
                if override_file.parent.exists() and not any(override_file.parent.iterdir()):
                    override_file.parent.rmdir()

        if existed:
            from .merge_cache import MergeCache
            MergeCache.instance().invalidate(rel_path)

        return existed

    def invalidate_overrides(self, mod_ids: set[str]) -> list[str]:
        """批量删除指定 mod 的所有 override（内存 + 磁盘），返回实际删除的 mod_id 列表"""
        deleted: list[str] = []
        for mod_id in mod_ids:
            with self._lock:
                had_data = mod_id in self._override_data
                self._override_data.pop(mod_id, None)

            had_dir = False
            if self._overrides_dir is not None:
                override_dir = self._overrides_dir / mod_id
                if override_dir.exists():
                    shutil.rmtree(override_dir)
                    had_dir = True

            if had_data or had_dir:
                deleted.append(mod_id)

        return deleted

    # ── 生命周期 ──

    def clear(self) -> None:
        """清空所有数据和缓存"""
        with self._lock:
            self._base_data.clear()
            self._mod_data.clear()
            self._override_data.clear()
            self._mod_names.clear()
            self._mod_config_paths.clear()
            self._failures.clear()
            self._game_config_path = None
            self._overrides_dir = None
        self._json_cache.clear()
