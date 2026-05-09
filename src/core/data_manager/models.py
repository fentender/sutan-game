"""数据类型定义 — GameData 及其子容器"""
import builtins
import enum
from dataclasses import dataclass, field
from pathlib import Path

from sultan_core.json import JsonDoc
from sultan_core.delta import DeltaDict, deserialize_delta


class GameDataType(enum.Enum):
    BASE = "base"
    MOD = "mod"


@dataclass
class ConfigItem:
    """单个 JSON 配置文件条目"""
    doc: JsonDoc
    version: int = 0


class ConfigData:
    """config/ 目录下所有 JSON 资源，per-file version"""

    def __init__(self) -> None:
        self._items: dict[str, ConfigItem] = {}

    def get(self, rel_path: str) -> JsonDoc | None:
        item = self._items.get(rel_path)
        return item.doc if item is not None else None

    def has(self, rel_path: str) -> bool:
        return rel_path in self._items

    def set(self, rel_path: str, doc: JsonDoc) -> None:
        if rel_path in self._items:
            self._items[rel_path].doc = doc
            self._items[rel_path].version += 1
        else:
            self._items[rel_path] = ConfigItem(doc=doc, version=1)

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
    """override delta 数据。内部存储 C++ DeltaDict"""

    def __init__(self) -> None:
        self._nodes: dict[str, DeltaDict] = {}
        self._versions: dict[str, int] = {}

    def get_node(self, rel_path: str) -> DeltaDict | None:
        return self._nodes.get(rel_path)

    def set_node(self, rel_path: str, node: DeltaDict) -> None:
        self._nodes[rel_path] = node
        self._versions[rel_path] = self._versions.get(rel_path, 0) + 1

    def set_raw(self, rel_path: str, doc: JsonDoc) -> None:
        node = deserialize_delta(doc)
        if node is None:
            return
        self._nodes[rel_path] = node
        self._versions[rel_path] = self._versions.get(rel_path, 0) + 1

    def has(self, rel_path: str) -> bool:
        return rel_path in self._nodes

    def remove(self, rel_path: str) -> bool:
        existed = rel_path in self._nodes
        self._nodes.pop(rel_path, None)
        self._versions.pop(rel_path, None)
        return existed

    def version_of(self, rel_path: str) -> int:
        return self._versions.get(rel_path, 0)

    def rel_paths(self) -> builtins.set[str]:
        return builtins.set(self._nodes.keys())

    def clear(self) -> None:
        self._nodes.clear()
        self._versions.clear()


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
