"""
Mod 扫描器 - 扫描 workshop 目录，读取 mod 元数据
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from .diagnostics import diag
from .json_parser import load_json
from .profiler import profile


@dataclass
class ModInfo:
    """单个 mod 的信息"""
    mod_id: str
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    version: str = ""
    path: Path = field(default_factory=Path)
    preview_path: str | None = None
    # 该 mod 包含的配置文件（相对于 config/ 的路径）
    config_files: list[str] = field(default_factory=list)
    # 该 mod 包含的非配置资源文件
    resource_files: list[str] = field(default_factory=list)


def find_preview(mod_path: Path) -> str | None:
    """查找 mod 的 preview 图片（大小写不敏感）"""
    for f in mod_path.iterdir():
        if f.is_file() and f.stem.lower() == 'preview' and f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.gif', '.bmp'):
            return str(f)
    return None


@profile
def scan_config_files(mod_path: Path) -> tuple[list[str], list[str]]:
    """扫描 mod 的 config 目录，返回 (配置文件列表, 资源文件列表)"""
    config_dir = mod_path / "config"
    config_files: list[str] = []
    resource_files: list[str] = []

    if not config_dir.exists():
        # 有些 mod 可能没有 config 目录，扫描其他资源
        for f in mod_path.rglob("*"):
            if f.is_file() and f.name not in ("Info.json",) and f.stem.lower() != "preview":
                resource_files.append(str(f.relative_to(mod_path)))
        return config_files, resource_files

    for f in config_dir.rglob("*"):
        if f.is_file():
            rel = normalize_rel_path(f, config_dir)
            if f.suffix.lower() == '.json':
                config_files.append(rel)
            else:
                resource_files.append("config/" + rel)

    # 扫描 config 目录之外的资源
    for f in mod_path.rglob("*"):
        if f.is_file() and f.name not in ("Info.json",) and f.stem.lower() != "preview":
            rel = normalize_rel_path(f, mod_path)
            if not rel.startswith("config/"):
                resource_files.append(rel)

    return config_files, resource_files


def scan_single_mod(mod_path: Path) -> ModInfo | None:
    """扫描单个 mod 目录"""
    if not mod_path.is_dir():
        return None

    mod_id = mod_path.name
    info = ModInfo(mod_id=mod_id, path=mod_path)

    # 读取 Info.json
    info_file = mod_path / "Info.json"
    if info_file.exists():
        try:
            data = load_json(info_file)
            name = data.get("name", mod_id)
            info.name = str(name) if name is not None else mod_id
            desc = data.get("description", "")
            info.description = str(desc) if desc is not None else ""
            tags = data.get("tags", [])
            info.tags = [str(t) for t in tags] if isinstance(tags, list) else []
            version = data.get("version", "")
            info.version = str(version) if version is not None else ""
        except json.JSONDecodeError as e:
            msg = f"Mod {mod_id}: Info.json 解析失败 - {e}"
            diag.warn("scan", msg)
            info.name = mod_id
    else:
        info.name = mod_id

    info.preview_path = find_preview(mod_path)
    info.config_files, info.resource_files = scan_config_files(mod_path)

    return info


@profile
def scan_all_mods(workshop_path: Path, exclude_ids: set[str] | None = None) -> list[ModInfo]:
    """扫描 workshop 目录下所有 mod"""
    if exclude_ids is None:
        exclude_ids = set()

    mods: list[ModInfo] = []
    if not workshop_path.exists():
        return mods

    for entry in sorted(workshop_path.iterdir()):
        if entry.is_dir() and entry.name not in exclude_ids:
            mod = scan_single_mod(entry)
            if mod:
                mods.append(mod)

    return mods


def normalize_rel_path(path: Path, base: Path) -> str:
    """计算相对路径并规范化分隔符为 /"""
    return str(path.relative_to(base)).replace("\\", "/")


@profile
def collect_mod_files(
    mod_configs: list[tuple[str, str, Path]]
) -> dict[str, list[tuple[str, str, Path]]]:
    """
    收集所有 mod 的 JSON 文件，按相对路径聚合。

    参数:
        mod_configs: [(mod_id, mod_name, mod_config_path), ...] 按优先级排序

    返回:
        {rel_path: [(mod_id, mod_name, file_path), ...]}
    """
    all_files: dict[str, list[tuple[str, str, Path]]] = {}
    for mod_id, mod_name, mod_config_path in mod_configs:
        if not mod_config_path.exists():
            continue
        for json_file in mod_config_path.rglob("*.json"):
            rel = normalize_rel_path(json_file, mod_config_path)
            if rel not in all_files:
                all_files[rel] = []
            all_files[rel].append((mod_id, mod_name, json_file))
    return all_files
