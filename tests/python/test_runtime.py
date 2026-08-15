"""运行时路径与异常基础设施回归测试。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from tests.python.test_runner import TestResult, assert_eq, assert_in, assert_true, run_test


def test_frozen_macos_runtime_paths() -> None:
    from src.runtime_paths import resolve_runtime_paths

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        executable = Path(tmp) / "SuDanModMerger.app/Contents/MacOS/SuDanModMerger"
        resources = Path(tmp) / "SuDanModMerger.app/Contents/Frameworks"
        paths = resolve_runtime_paths(
            frozen=True,
            platform="darwin",
            executable=executable,
            bundle_dir=resources,
            home=home,
        )

        assert_eq(
            paths.user_data_root,
            home / "Library/Application Support/SuDanModMerger",
        )
        assert_eq(paths.error_log, home / "Library/Logs/SuDanModMerger/error.log")
        assert_eq(paths.resource_root, resources)
        assert_eq(paths.legacy_root, executable.parent)
        assert_true(paths.app_bundle_root not in paths.user_config.parents)


def test_migrate_legacy_runtime_data() -> None:
    from src.runtime_paths import RuntimePaths, migrate_legacy_runtime_data

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        legacy = root / "legacy"
        user_data = root / "user"
        legacy.mkdir()
        (legacy / "user_config.json").write_text('{"game_path": "old"}', encoding="utf-8")
        override = legacy / "mod_overrides/123/config.json"
        override.parent.mkdir(parents=True)
        override.write_text("{}", encoding="utf-8")

        paths = RuntimePaths(
            project_root=legacy,
            resource_root=root / "resources",
            user_data_root=user_data,
            error_log=root / "logs/error.log",
            legacy_root=legacy,
            app_bundle_root=root / "SuDanModMerger.app",
        )
        migrate_legacy_runtime_data(paths)

        new_config = user_data / "user_config.json"
        new_override = user_data / "mod_overrides/123/config.json"
        assert_eq(new_config.read_text(encoding="utf-8"), '{"game_path": "old"}')
        assert_eq(new_override.read_text(encoding="utf-8"), "{}")
        assert_true((legacy / "user_config.json").exists())

        new_config.write_text('{"game_path": "new"}', encoding="utf-8")
        migrate_legacy_runtime_data(paths)
        assert_eq(new_config.read_text(encoding="utf-8"), '{"game_path": "new"}')


def test_exception_report_writes_traceback() -> None:
    from src.core.infra.error_reporter import report_exception

    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "logs/error.log"
        try:
            raise RuntimeError("测试异常")
        except RuntimeError as exc:
            report = report_exception(exc, log_path=log_path)

        assert_in("RuntimeError", report.summary)
        assert_in("测试异常", report.summary)
        assert_in("Traceback", report.details)
        content = log_path.read_text(encoding="utf-8")
        assert_in("RuntimeError: 测试异常", content)


def test_runtime_check_rejects_user_data_inside_app() -> None:
    from src.runtime_paths import RuntimePaths, runtime_check

    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp) / "SuDanModMerger.app"
        resources = app / "Contents/Resources"
        (resources / "schemas").mkdir(parents=True)
        (resources / "history_config_pack").mkdir()
        paths = RuntimePaths(
            project_root=app / "Contents/MacOS",
            resource_root=resources,
            user_data_root=app / "Contents/MacOS/data",
            error_log=Path(tmp) / "error.log",
            legacy_root=app / "Contents/MacOS",
            app_bundle_root=app,
        )
        try:
            runtime_check(paths)
        except RuntimeError as exc:
            assert_in("应用包内部", str(exc))
        else:
            raise AssertionError("未拒绝应用包内部的用户数据目录")


def test_async_task_reports_unhandled_exception() -> None:
    from src.core.infra import error_reporter
    from src.core.infra.async_task import TaskError, async_task
    from src.runtime_paths import RuntimePaths

    events: list[object] = []

    @async_task
    def fail(cb: object, *, handle: object) -> None:
        del cb, handle
        raise ValueError("后台失败")

    original_paths = error_reporter.RUNTIME_PATHS
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        error_reporter.RUNTIME_PATHS = RuntimePaths(
            project_root=root,
            resource_root=root,
            user_data_root=root,
            error_log=root / "error.log",
            legacy_root=root,
            app_bundle_root=root,
        )
        try:
            task = fail(cb=events.append)
            assert_true(task.wait(2), "异步异常任务未结束")
        finally:
            error_reporter.RUNTIME_PATHS = original_paths

        assert_eq(len(events), 1)
        assert_true(isinstance(events[0], TaskError))
        event = events[0]
        assert isinstance(event, TaskError)
        assert_in("ValueError", event.message)
        assert_in("Traceback", event.details)


def test_async_task_reports_cancelled() -> None:
    from src.core.infra.async_task import AsyncTaskHandle, TaskCancelled, async_task

    events: list[object] = []

    @async_task
    def cancel(cb: object, *, handle: AsyncTaskHandle) -> None:
        del cb
        handle.cancel()
        handle.check_cancel()

    task = cancel(cb=events.append)
    assert_true(task.wait(2), "异步取消任务未结束")
    assert_eq(len(events), 1)
    assert_true(isinstance(events[0], TaskCancelled))


def test_analysis_error_clears_pending_action() -> None:
    from src.gui.app import MainWindow

    class Progress:
        visible = True

        def setVisible(self, visible: bool) -> None:
            self.visible = visible

    class Status:
        message = ""

        def showMessage(self, message: str) -> None:
            self.message = message

    class Window:
        progress_bar = Progress()
        _pending_action = object()
        shown_error: tuple[str, str, str] | None = None
        logged = ""
        status = Status()

        def statusBar(self) -> Status:
            return self.status

        def _log_message(self, level: str, message: str) -> None:
            del level
            self.logged = message

        def _show_error(self, title: str, message: str, details: str = "") -> None:
            self.shown_error = (title, message, details)

    window = Window()
    MainWindow._on_prep_error(window, "准备失败", "traceback")  # type: ignore[arg-type]
    assert_true(not window.progress_bar.visible)
    assert_eq(window._pending_action, None)
    assert_eq(window.shown_error, ("准备分析失败", "准备失败", "traceback"))


def run_all(result: TestResult) -> None:
    tests = [
        ("test_frozen_macos_runtime_paths", test_frozen_macos_runtime_paths),
        ("test_migrate_legacy_runtime_data", test_migrate_legacy_runtime_data),
        ("test_exception_report_writes_traceback", test_exception_report_writes_traceback),
        ("test_runtime_check_rejects_user_data_inside_app", test_runtime_check_rejects_user_data_inside_app),
        ("test_async_task_reports_unhandled_exception", test_async_task_reports_unhandled_exception),
        ("test_async_task_reports_cancelled", test_async_task_reports_cancelled),
        ("test_analysis_error_clears_pending_action", test_analysis_error_clears_pending_action),
    ]
    for name, func in tests:
        run_test(name, func, result)
