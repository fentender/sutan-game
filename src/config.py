"""
配置模块 - 路径常量和用户配置读写
"""
import json
import os
from pathlib import Path
from dataclasses import dataclass, field, asdict


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# 默认路径
DEFAULT_GAME_PATH = Path("d:/SteamLibrary/steamapps/common/Sultan's Game")
DEFAULT_WORKSHOP_PATH = Path("d:/SteamLibrary/steamapps/workshop/content/3117820")
DEFAULT_CONFIG_SUBPATH = "Sultan's Game_Data/StreamingAssets/config"

# 本地 mod 目录（我的文档/DoubleCross/SultansGame/Mod）
DEFAULT_LOCAL_MOD_PATH = Path(os.path.expanduser("~/Documents/DoubleCross/SultansGame/Mod"))

# 用户配置文件
USER_CONFIG_PATH = PROJECT_ROOT / "user_config.json"

# 合并输出目录
MERGED_OUTPUT_PATH = PROJECT_ROOT / "merged_output"

# Schema 规则文件目录
SCHEMA_DIR = PROJECT_ROOT / "schemas"

# 合成 Mod 的 ID（放在 workshop 目录下）
SYNTHETIC_MOD_ID = "0000000001"


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
    # 每个 mod 的覆盖模式：mod_id -> "replace" | "merge_as_array"
    merge_modes: dict[str, str] = field(default_factory=dict)
    # 是否允许删减（mod 中缺少的条目从合并结果中删除）
    allow_deletions: bool = False

    @property
    def game_config_path(self) -> Path:
        return Path(self.game_path) / DEFAULT_CONFIG_SUBPATH

    @property
    def workshop_dir(self) -> Path:
        return Path(self.workshop_path)

    @property
    def local_mod_dir(self) -> Path:
        return Path(self.local_mod_path)

    def save(self):
        USER_CONFIG_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=4),
            encoding='utf-8'
        )

    @classmethod
    def load(cls) -> 'UserConfig':
        if USER_CONFIG_PATH.exists():
            data = json.loads(USER_CONFIG_PATH.read_text(encoding='utf-8'))
            # 兼容旧配置（没有 local_mod_path 字段）
            valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
            filtered = {k: v for k, v in data.items() if k in valid_fields}
            return cls(**filtered)
        return cls()
