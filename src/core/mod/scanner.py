"""
Mod 扫描器 - 扫描 workshop 目录，读取 mod 元数据
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..infra.diagnostics import diag
from ..infra.profiler import profile
from ..infra.types import normalize_rel_path
from ..platform.steam import get_mod_update_times, get_steamapps_from_workshop


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
    # Steam 上的最后更新时间（Unix 时间戳），来自 .acf 文件
    update_time: int | None = None
    # 是否修改了本体内容（True=有重叠, False=纯增量, None=未检测）
    has_base_overlap: bool | None = None


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
    config_exists = config_dir.exists()

    for f in mod_path.rglob("*"):
        if not f.is_file():
            continue
        if f.name == "Info.json" or f.stem.lower() == "preview":
            continue
        rel = normalize_rel_path(f, mod_path)
        if config_exists and rel.startswith("config/"):
            config_rel = normalize_rel_path(f, config_dir)
            if f.suffix.lower() == '.json':
                config_files.append(config_rel)
            else:
                resource_files.append(rel)
        else:
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
            import json as _json
            from sultan_core.json import JsonDoc
            data = _json.loads(JsonDoc.parse_file(str(info_file)).to_string())
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

    # 从 .acf 文件批量注入 Mod 更新时间
    steamapps = get_steamapps_from_workshop(workshop_path)
    update_times = get_mod_update_times(steamapps)
    for mod in mods:
        mod.update_time = update_times.get(mod.mod_id, (1 << 31) - 1)

    return mods


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
