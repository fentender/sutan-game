"""
配置模块 - 路径常量和用户配置读写
"""
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .core.diagnostics import diag

# 应用版本号（发版时与 git tag 同步更新）
APP_VERSION = "1.2.1"

# 更新检查源（GitHub 优先，Gitee 备用）
UPDATE_SOURCES = [
    {
        "name": "GitHub",
        "api": "https://api.github.com/repos/fentender/sutan-game/releases/latest",
        "releases_url": "https://github.com/fentender/sutan-game/releases",
    },
    {
        "name": "Gitee",
        "api": "https://gitee.com/api/v5/repos/fentende125/sutan-game/releases/latest",
        "releases_url": "https://gitee.com/fentende125/sutan-game/releases",
    },
]


# 项目根目录
if getattr(sys, 'frozen', False):
    # PyInstaller 打包后：exe 所在目录
    PROJECT_ROOT = Path(sys.executable).parent
else:
    # 源码运行：项目根目录
    PROJECT_ROOT = Path(__file__).parent.parent

# 游戏与 Workshop 常量
GAME_DIR_NAME = "Sultan's Game"
WORKSHOP_APP_ID = "3117820"
DEFAULT_CONFIG_SUBPATH = "Sultan's Game_Data/StreamingAssets/config"

# 本地 mod 目录（我的文档/DoubleCross/SultansGame/Mod）
DEFAULT_LOCAL_MOD_PATH = Path(os.path.expanduser("~/Documents/DoubleCross/SultansGame/Mod"))


# ── Steam 路径自动检测 ──────────────────────────────────────────

def _detect_steam_library_folders() -> list[Path]:
    """检测所有 Steam 库目录（支持多磁盘安装）"""
    steam_root = None

    # 方式一：通过 Windows 注册表获取 Steam 安装目录
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Valve\Steam") as key:
            steam_root = Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except Exception:
        pass

    # 方式二：尝试常见安装位置
    if not steam_root or not steam_root.exists():
        for candidate in [
            Path("C:/Program Files (x86)/Steam"),
            Path("C:/Program Files/Steam"),
            Path("D:/Steam"),
            Path("E:/Steam"),
        ]:
            if candidate.exists():
                steam_root = candidate
                break

    if not steam_root or not steam_root.exists():
        return []

    # 解析 libraryfolders.vdf 获取所有库目录
    vdf_path = steam_root / "steamapps" / "libraryfolders.vdf"
    folders = [steam_root]  # Steam 安装目录本身也是一个库

    if vdf_path.exists():
        try:
            text = vdf_path.read_text(encoding='utf-8')
            # VDF 格式中 "path" 的值就是库目录
            for match in re.finditer(r'"path"\s+"([^"]+)"', text):
                p = Path(match.group(1).replace("\\\\", "/"))
                if p.exists() and p not in folders:
                    folders.append(p)
        except Exception:
            pass

    return folders


GAME_EXE_NAME = "Sultan's Game.exe"


def detect_game_path() -> str:
    """在所有 Steam 库中查找游戏安装目录，找不到返回空字符串。
    优先选择包含游戏可执行文件的完整安装目录。
    """
    fallback = ""
    for folder in _detect_steam_library_folders():
        candidate = folder / "steamapps" / "common" / GAME_DIR_NAME
        if candidate.exists():
            if (candidate / GAME_EXE_NAME).exists():
                return str(candidate)
            # 目录存在但没有 exe，记为备选
            if not fallback:
                fallback = str(candidate)
    return fallback


def detect_workshop_path() -> str:
    """在所有 Steam 库中查找游戏的 Workshop 内容目录，找不到返回空字符串"""
    for folder in _detect_steam_library_folders():
        candidate = folder / "steamapps" / "workshop" / "content" / WORKSHOP_APP_ID
        if candidate.exists():
            return str(candidate)
    return ""


def infer_workshop_path_from_game(game_path: str) -> str:
    """根据游戏安装路径推导 Workshop 路径"""
    gp = Path(game_path)
    # 游戏路径: .../steamapps/common/Sultan's Game
    # Workshop:  .../steamapps/workshop/content/3117820
    steamapps = gp.parent.parent  # .../steamapps
    candidate = steamapps / "workshop" / "content" / WORKSHOP_APP_ID
    if candidate.exists():
        return str(candidate)
    return ""


# 默认路径（自动检测，找不到为空字符串）
DEFAULT_GAME_PATH = detect_game_path()
DEFAULT_WORKSHOP_PATH = detect_workshop_path()

# 用户配置文件
USER_CONFIG_PATH = PROJECT_ROOT / "user_config.json"

# 合并输出目录
MERGED_OUTPUT_PATH = PROJECT_ROOT / "merged_output"

# Schema 规则文件目录
SCHEMA_DIR = PROJECT_ROOT / "schemas"

# Mod 覆盖文件目录（用户手动编辑的合并结果）
MOD_OVERRIDES_DIR = PROJECT_ROOT / "mod_overrides"

# 合成 Mod 的 ID（放在 workshop 目录下）
SYNTHETIC_MOD_ID = "0000000001"

# 应用图标（打包后在 _MEIPASS 即 _internal/ 中，源码运行时在项目根目录）
if getattr(sys, 'frozen', False):
    APP_ICON_PATH = Path(sys._MEIPASS) / "app.ico"  # type: ignore[attr-defined]
else:
    APP_ICON_PATH = PROJECT_ROOT / "app.ico"


@dataclass
class UserConfig:
    """用户配置"""
    game_path: str = str(DEFAULT_GAME_PATH)
    workshop_path: str = str(DEFAULT_WORKSHOP_PATH)
    local_mod_path: str = str(DEFAULT_LOCAL_MOD_PATH)
    # mod 排序列表（mod_id 字符串，越靠后优先级越高）
    mod_order: list[str] = field(default_factory=list)
    # 启用的 mod 集合
    enabled_mods: list[str] = field(default_factory=list)
    # 合并模式（normal/smart/replace，默认 smart）
    merge_mode: str = "smart"
    # per-mod 合并模式覆盖（key=mod_id，value=模式名）
    mod_merge_modes: dict[str, str] = field(default_factory=dict)
    # 是否启用性能评估（启用后记录各函数执行时间，输出到日志）
    enable_profiler: bool = False

    @property
    def game_config_path(self) -> Path:
        return Path(self.game_path) / DEFAULT_CONFIG_SUBPATH

    @property
    def workshop_dir(self) -> Path:
        return Path(self.workshop_path)

    @property
    def local_mod_dir(self) -> Path:
        return Path(self.local_mod_path)

    def save(self) -> None:
        """原子保存配置：先写临时文件，再重命名覆盖"""
        content = json.dumps(asdict(self), ensure_ascii=False, indent=4)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=USER_CONFIG_PATH.parent, suffix='.tmp'
        )
        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                f.write(content)
            os.replace(tmp_path, USER_CONFIG_PATH)
        except BaseException:
            os.unlink(tmp_path)
            raise

    @classmethod
    def load(cls) -> 'UserConfig':
        if USER_CONFIG_PATH.exists():
            try:
                data = json.loads(USER_CONFIG_PATH.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError) as e:
                diag.warn("config", f"配置文件损坏，使用默认配置: {e}")
                return cls()
            valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
            # 迁移旧配置：allow_deletions → merge_mode
            if "allow_deletions" in data and "merge_mode" not in data:
                data["merge_mode"] = "normal" if data.pop("allow_deletions") else "smart"
            else:
                data.pop("allow_deletions", None)
            filtered = {k: v for k, v in data.items() if k in valid_fields}
            return cls(**filtered)
        return cls()
