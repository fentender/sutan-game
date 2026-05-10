"""通用异步任务基础设施"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any


class _Cancelled(Exception):
    pass


@dataclass
class TaskProgress:
    completed: int
    total: int


@dataclass
class TaskDone:
    pass


@dataclass
class TaskError:
    message: str


type TaskEvent = TaskProgress | TaskDone | TaskError
type TaskCallback = Callable[[TaskEvent], None]


class AsyncTaskHandle:
    """异步任务句柄。调用者持有，用于取消正在执行的任务。"""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check_cancel(self) -> None:
        if self._cancelled.is_set():
            raise _Cancelled()


def async_task(fn: Callable[..., None]) -> Callable[..., AsyncTaskHandle]:
    """装饰器：将同步方法转为异步任务。

    装饰器负责：创建 handle、启动后台线程、注入 handle 参数、返回 handle。
    方法体负责：所有回调调用（cb(TaskProgress/Done/Error)）、异常处理。
    """
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> AsyncTaskHandle:
        handle = AsyncTaskHandle()
        kwargs['handle'] = handle

        threading.Thread(
            target=fn, args=args, kwargs=kwargs, daemon=True,
        ).start()
        return handle

    return wrapper
