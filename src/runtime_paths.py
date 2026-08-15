"""应用运行时目录解析与旧版数据迁移。"""
from __future__ import annotations

import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "SuDanModMerger"


@dataclass(frozen=True)
class RuntimePaths:
    """区分应用只读资源与用户可写数据目录。"""

    project_root: Path
    resource_root: Path
    user_data_root: Path
    error_log: Path
    legacy_root: Path
    app_bundle_root: Path

    @property
    def user_config(self) -> Path:
        return self.user_data_root / "user_config.json"

    @property
    def merged_output(self) -> Path:
        return self.user_data_root / "merged_output"

    @property
    def mod_overrides(self) -> Path:
        return self.user_data_root / "mod_overrides"


def resolve_runtime_paths(
    *,
    frozen: bool | None = None,
    platform: str | None = None,
    executable: Path | None = None,
    bundle_dir: Path | None = None,
    home: Path | None = None,
) -> RuntimePaths:
    """计算当前进程使用的资源、数据和日志目录。"""
    is_frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    current_platform = sys.platform if platform is None else platform
    executable_path = Path(sys.executable) if executable is None else executable
    home_path = Path.home() if home is None else home
    source_root = Path(__file__).resolve().parent.parent

    if not is_frozen:
        return RuntimePaths(
            project_root=source_root,
            resource_root=source_root,
            user_data_root=source_root,
            error_log=source_root / "error.log",
            legacy_root=source_root,
            app_bundle_root=source_root,
        )

    legacy_root = executable_path.parent
    resource_root = (
        Path(sys._MEIPASS)  # type: ignore[attr-defined]
        if bundle_dir is None
        else bundle_dir
    )
    if current_platform == "darwin":
        user_data_root = home_path / "Library/Application Support" / APP_DIR_NAME
        error_log = home_path / "Library/Logs" / APP_DIR_NAME / "error.log"
        app_bundle_root = executable_path.parent.parent.parent
    else:
        user_data_root = legacy_root
        error_log = legacy_root / "error.log"
        app_bundle_root = legacy_root

    return RuntimePaths(
        project_root=legacy_root,
        resource_root=resource_root,
        user_data_root=user_data_root,
        error_log=error_log,
        legacy_root=legacy_root,
        app_bundle_root=app_bundle_root,
    )


def _copy_missing_tree(source: Path, target: Path) -> None:
    for source_file in source.rglob("*"):
        relative = source_file.relative_to(source)
        target_file = target / relative
        if source_file.is_dir():
            target_file.mkdir(parents=True, exist_ok=True)
        elif not target_file.exists():
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)


def migrate_legacy_runtime_data(paths: RuntimePaths) -> None:
    """将旧包目录内的数据复制到用户目录，绝不覆盖已有新数据。"""
    if paths.legacy_root == paths.user_data_root:
        return

    legacy_config = paths.legacy_root / "user_config.json"
    if legacy_config.is_file() and not paths.user_config.exists():
        paths.user_config.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_config, paths.user_config)

    legacy_overrides = paths.legacy_root / "mod_overrides"
    if legacy_overrides.is_dir():
        _copy_missing_tree(legacy_overrides, paths.mod_overrides)


RUNTIME_PATHS = resolve_runtime_paths()


def runtime_check(paths: RuntimePaths = RUNTIME_PATHS) -> dict[str, str]:
    """验证发布包资源和用户数据目录，失败时直接抛错。"""
    schema_dir = paths.resource_root / "schemas"
    history_dir = paths.resource_root / "history_config_pack"
    if not schema_dir.is_dir():
        raise FileNotFoundError(f"Schema 目录不存在: {schema_dir}")
    if not history_dir.is_dir():
        raise FileNotFoundError(f"历史配置目录不存在: {history_dir}")
    if (
        paths.user_data_root == paths.app_bundle_root
        or paths.app_bundle_root in paths.user_data_root.parents
    ):
        raise RuntimeError(f"用户数据目录位于应用包内部: {paths.user_data_root}")

    paths.user_data_root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=paths.user_data_root, suffix=".tmp"):
        pass

    return {
        "resource_root": str(paths.resource_root),
        "user_data_root": str(paths.user_data_root),
        "error_log": str(paths.error_log),
    }
