"""不会再次抛错的统一异常记录。"""
from __future__ import annotations

import sys
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType

from src.runtime_paths import RUNTIME_PATHS


@dataclass(frozen=True)
class ExceptionReport:
    summary: str
    details: str
    log_path: Path


_report_callback: Callable[[ExceptionReport], None] | None = None


def report_exception(
    exception: BaseException,
    traceback_value: TracebackType | None = None,
    *,
    log_path: Path | None = None,
) -> ExceptionReport:
    """生成并持久化异常报告；记录失败时仅回退到标准错误。"""
    target = RUNTIME_PATHS.error_log if log_path is None else log_path
    tb = exception.__traceback__ if traceback_value is None else traceback_value
    trace = "".join(traceback.format_exception(type(exception), exception, tb))
    summary = f"{type(exception).__name__}: {exception}"
    details = (
        f"时间: {datetime.now().astimezone().isoformat()}\n"
        f"线程: {threading.current_thread().name}\n"
        f"错误: {summary}\n\n{trace}"
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as file:
            file.write(details)
            file.write("\n" + "=" * 80 + "\n")
    except Exception as log_error:
        try:
            sys.stderr.write(
                f"无法写入异常日志 {target}: {log_error}\n{details}\n",
            )
        except Exception:
            pass
    return ExceptionReport(summary=summary, details=details, log_path=target)


def set_report_callback(callback: Callable[[ExceptionReport], None] | None) -> None:
    """设置异常报告的 GUI 转发函数。"""
    global _report_callback
    _report_callback = callback


def report_unhandled_exception(
    exception: BaseException,
    traceback_value: TracebackType | None = None,
) -> ExceptionReport:
    """记录未处理异常，并在 GUI 可用时通知主线程。"""
    report = report_exception(exception, traceback_value)
    if _report_callback is not None:
        try:
            _report_callback(report)
        except Exception as callback_error:
            report_exception(callback_error)
    return report


def install_exception_hooks() -> None:
    """安装主线程和普通后台线程的最外层异常钩子。"""
    def _sys_hook(
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_type
        report_unhandled_exception(exc_value, exc_tb)

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is not None:
            report_unhandled_exception(args.exc_value, args.exc_traceback)

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook
