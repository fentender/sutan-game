"""
Mod 扫描器 - 扫描 workshop 目录，读取 mod 元数据
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .json_parser import load_json

log = logging.getLogger(__name__)

# 全局错误收集器，GUI 可以读取并展示
scan_errors: list[str] = []


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


def scan_config_files(mod_path: Path) -> tuple[list[str], list[str]]:
    """扫描 mod 的 config 目录，返回 (配置文件列表, 资源文件列表)"""
    config_dir = mod_path / "config"
    config_files = []
    resource_files = []

    if not config_dir.exists():
        # 有些 mod 可能没有 config 目录，扫描其他资源
        for f in mod_path.rglob("*"):
            if f.is_file() and f.name not in ("Info.json",) and f.stem.lower() != "preview":
                resource_files.append(str(f.relative_to(mod_path)))
        return config_files, resource_files

    for f in config_dir.rglob("*"):
        if f.is_file():
            rel = str(f.relative_to(config_dir)).replace("\\", "/")
            if f.suffix.lower() == '.json':
                config_files.append(rel)
            else:
                resource_files.append("config/" + rel)

    # 扫描 config 目录之外的资源
    for f in mod_path.rglob("*"):
        if f.is_file() and f.name not in ("Info.json",) and f.stem.lower() != "preview":
            rel = str(f.relative_to(mod_path)).replace("\\", "/")
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
            info.name = data.get("name", mod_id)
            info.description = data.get("description", "")
            info.tags = data.get("tags", [])
            info.version = data.get("version", "")
        except Exception as e:
            msg = f"Mod {mod_id}: Info.json 解析失败 - {e}"
            log.warning(msg)
            scan_errors.append(msg)
            info.name = mod_id
    else:
        info.name = mod_id

    info.preview_path = find_preview(mod_path)
    info.config_files, info.resource_files = scan_config_files(mod_path)

    return info


def scan_all_mods(workshop_path: Path, exclude_ids: set[str] | None = None) -> list[ModInfo]:
    """扫描 workshop 目录下所有 mod"""
    if exclude_ids is None:
        exclude_ids = set()

    mods = []
    if not workshop_path.exists():
        return mods

    for entry in sorted(workshop_path.iterdir()):
        if entry.is_dir() and entry.name not in exclude_ids:
            mod = scan_single_mod(entry)
            if mod:
                mods.append(mod)

    return mods
