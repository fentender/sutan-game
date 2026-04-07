"""
可选性能评估模块

使用方法：
  1. 装饰器模式：@profile 标记需要监控的函数
  2. 上下文管理器：with profile_block("操作名"): ...
  3. 调用 get_report() 获取统计报告

全局开关通过 enable() / disable() 控制。
默认关闭，关闭时 @profile 和 profile_block 零开销（直接透传）。
通过 user_config.json 的 enable_profiler 字段控制是否在启动时自动开启。
"""
import time
import logging
import threading
from functools import wraps
from dataclasses import dataclass

log = logging.getLogger(__name__)

_enabled = False
_lock = threading.Lock()


@dataclass
class _Stats:
    """单个函数/代码块的统计信息"""
    call_count: int = 0
    total_time: float = 0.0
    min_time: float = float('inf')
    max_time: float = 0.0


_registry: dict[str, _Stats] = {}


def enable():
    """启用性能评估"""
    global _enabled
    _enabled = True
    log.info("性能评估已启用")


def disable():
    """禁用性能评估"""
    global _enabled
    _enabled = False


def is_enabled() -> bool:
    return _enabled


def reset():
    """清空所有统计数据"""
    with _lock:
        _registry.clear()


def profile(func):
    """装饰器：记录函数执行时间。禁用时零开销。"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not _enabled:
            return func(*args, **kwargs)
        name = f"{func.__module__}.{func.__qualname__}"
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            _record(name, time.perf_counter() - start)
    return wrapper


class profile_block:
    """上下文管理器：记录代码块执行时间。禁用时零开销。"""
    __slots__ = ('name', 'start')

    def __init__(self, name: str):
        self.name = name
        self.start = 0.0

    def __enter__(self):
        if _enabled:
            self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        if _enabled:
            _record(self.name, time.perf_counter() - self.start)
        return False


def _record(name: str, elapsed: float):
    with _lock:
        stats = _registry.setdefault(name, _Stats())
        stats.call_count += 1
        stats.total_time += elapsed
        if elapsed < stats.min_time:
            stats.min_time = elapsed
        if elapsed > stats.max_time:
            stats.max_time = elapsed


def get_report(top_n: int = 20) -> str:
    """生成性能报告，按总耗时降序排列"""
    with _lock:
        items = sorted(_registry.items(),
                       key=lambda x: x[1].total_time, reverse=True)

    if not items:
        return "（无性能数据，请先 enable() 并执行操作）"

    lines = [
        "性能评估报告",
        "=" * 90,
        f"{'函数/代码块':<50} {'次数':>6} {'总耗时':>9} {'平均':>9} {'最大':>9}",
        "-" * 90,
    ]

    for name, s in items[:top_n]:
        avg = s.total_time / s.call_count if s.call_count else 0
        lines.append(
            f"{name:<50} {s.call_count:>6} "
            f"{s.total_time:>8.3f}s {avg:>8.3f}s {s.max_time:>8.3f}s"
        )

    lines.append("-" * 90)
    total = sum(s.total_time for _, s in items)
    lines.append(f"{'总计':<50} {'':>6} {total:>8.3f}s")
    return "\n".join(lines)


def log_report(top_n: int = 20):
    """输出性能报告到日志"""
    if _registry:
        log.info("\n%s", get_report(top_n))
