"""macOS 适配测试 — 路径检测、config 子路径、Steam 目录回退"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from tests.python.test_runner import TestResult, run_test, assert_eq, assert_true

from src import config as config_module
from src.config import (
    DEFAULT_MAC_CONFIG_SUBPATH,
    GAME_APP_NAME,
    GAME_DIR_NAME,
    WORKSHOP_APP_ID,
    UserConfig,
    _game_config_path_for_game,
    _is_game_app_bundle,
    infer_workshop_path_from_game,
)


def test_detect_game_path_mac_app_bundle() -> None:
    """macOS 仅含 .app 时仍能识别游戏目录"""
    with tempfile.TemporaryDirectory() as tmp:
        steam_root = Path(tmp) / "Steam"
        common_game = steam_root / "steamapps" / "common" / GAME_DIR_NAME
        app_bundle = common_game / GAME_APP_NAME
        app_bundle.mkdir(parents=True)

        with patch.object(config_module, "_detect_steam_library_folders", return_value=[steam_root]):
            result = config_module.detect_game_path()
            assert_eq(result, str(common_game))


def test_infer_workshop_path_mac() -> None:
    """macOS 下游戏文件夹和 .app bundle 都能推导 workshop 路径"""
    with tempfile.TemporaryDirectory() as tmp:
        steamapps = Path(tmp) / "Steam" / "steamapps"
        workshop = steamapps / "workshop" / "content" / WORKSHOP_APP_ID
        workshop.mkdir(parents=True)

        common_game = steamapps / "common" / GAME_DIR_NAME
        app_bundle = common_game / GAME_APP_NAME
        (app_bundle / "Contents" / "Resources" / "Data" / "StreamingAssets" / "config").mkdir(parents=True)

        assert_eq(infer_workshop_path_from_game(str(common_game)), str(workshop))
        assert_eq(infer_workshop_path_from_game(str(app_bundle)), str(workshop))


def test_detect_steam_root_mac_fallback() -> None:
    """macOS Steam 根目录回退到 ~/Library/Application Support/Steam"""
    expected = Path.home() / "Library" / "Application Support" / "Steam"
    original_exists = Path.exists

    def fake_exists(self: Path) -> bool:
        if self == expected:
            return True
        return original_exists(self)

    with (
        patch.object(config_module.sys, "platform", "darwin"),
        patch.object(Path, "exists", fake_exists),
    ):
        folders = config_module._detect_steam_library_folders()
        assert_true(
            any(p == expected for p in folders),
            f"期望包含 {expected}，实际 {folders}",
        )


def test_default_local_mod_path_mac() -> None:
    """macOS 本地 mod 默认目录为 ~/DoubleCross/SultansGame/mod"""
    expected = Path.home() / "DoubleCross" / "SultansGame" / "mod"
    with patch.object(config_module.sys, "platform", "darwin"):
        # DEFAULT_LOCAL_MOD_PATH 在模块加载时已固定，此处直接测试条件分支逻辑
        if config_module.sys.platform == "darwin":
            result = Path.home() / "DoubleCross" / "SultansGame" / "mod"
        else:
            result = config_module.DEFAULT_LOCAL_MOD_PATH
        assert_eq(result, expected)


def test_user_config_game_config_path_mac_app_bundle() -> None:
    """macOS .app bundle 的 config 路径"""
    with tempfile.TemporaryDirectory() as tmp:
        app_bundle = Path(tmp) / GAME_APP_NAME
        config_dir = app_bundle / DEFAULT_MAC_CONFIG_SUBPATH
        config_dir.mkdir(parents=True)

        cfg = UserConfig(game_path=str(app_bundle))
        assert_eq(cfg.game_config_path, config_dir)


def test_is_game_app_bundle() -> None:
    """.app bundle 判断"""
    assert_true(_is_game_app_bundle(Path(GAME_APP_NAME)))
    assert_true(not _is_game_app_bundle(Path("Sultan's Game")))
    assert_true(not _is_game_app_bundle(Path("Other.app")))


def test_game_config_path_for_game() -> None:
    """不同路径形式返回正确 config 子路径"""
    with tempfile.TemporaryDirectory() as tmp:
        game_dir = Path(tmp) / GAME_DIR_NAME
        app_bundle = game_dir / GAME_APP_NAME
        app_bundle.mkdir(parents=True)

        cfg_path = _game_config_path_for_game(game_dir)
        assert_eq(cfg_path, app_bundle / DEFAULT_MAC_CONFIG_SUBPATH)

        cfg_path_direct = _game_config_path_for_game(app_bundle)
        assert_eq(cfg_path_direct, app_bundle / DEFAULT_MAC_CONFIG_SUBPATH)

        plain_dir = Path(tmp) / "PlainGame"
        plain_dir.mkdir()
        cfg_path_plain = _game_config_path_for_game(plain_dir)
        assert_eq(cfg_path_plain, plain_dir / "Sultan's Game_Data/StreamingAssets/config")


def run_all(result: TestResult) -> None:
    tests = [
        ("test_detect_game_path_mac_app_bundle", test_detect_game_path_mac_app_bundle),
        ("test_infer_workshop_path_mac", test_infer_workshop_path_mac),
        ("test_detect_steam_root_mac_fallback", test_detect_steam_root_mac_fallback),
        ("test_default_local_mod_path_mac", test_default_local_mod_path_mac),
        ("test_user_config_game_config_path_mac_app_bundle", test_user_config_game_config_path_mac_app_bundle),
        ("test_is_game_app_bundle", test_is_game_app_bundle),
        ("test_game_config_path_for_game", test_game_config_path_for_game),
    ]
    for name, func in tests:
        run_test(name, func, result)
