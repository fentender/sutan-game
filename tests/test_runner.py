"""
无头测试运行器 - 不启动 GUI 直接测试核心功能和性能

用法:
    python -m tests.test_runner          # 全部测试
    python -m tests.test_runner --func   # 仅功能测试
    python -m tests.test_runner --perf   # 仅性能测试

使用项目实际的游戏本体和 Mod 数据。如果数据路径不存在，
依赖真实数据的测试会自动跳过。
"""
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
log = logging.getLogger("test")


class TestResult:
    def __init__(self):
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.skipped: list[tuple[str, str]] = []

    @property
    def summary(self) -> str:
        total = len(self.passed) + len(self.failed) + len(self.skipped)
        return (f"{len(self.passed)} 通过 / {len(self.failed)} 失败 "
                f"/ {len(self.skipped)} 跳过 / 共 {total}")


class SkipTest(Exception):
    """测试跳过信号"""
    pass


def run_test(name: str, func, result: TestResult):
    """运行单个测试，捕获异常并记录结果"""
    try:
        func()
        result.passed.append(name)
        log.info("  PASS  %s", name)
    except SkipTest as e:
        result.skipped.append((name, str(e)))
        log.info("  SKIP  %s (%s)", name, e)
    except Exception as e:
        result.failed.append((name, f"{type(e).__name__}: {e}"))
        log.error("  FAIL  %s: %s: %s", name, type(e).__name__, e)


def assert_eq(actual, expected, msg=""):
    """断言相等"""
    if actual != expected:
        raise AssertionError(
            f"{msg + ': ' if msg else ''}期望 {expected!r}，实际 {actual!r}"
        )


def assert_true(condition, msg=""):
    """断言为真"""
    if not condition:
        raise AssertionError(msg or "条件不满足")


def assert_in(item, container, msg=""):
    """断言包含"""
    if item not in container:
        raise AssertionError(
            f"{msg + ': ' if msg else ''}{item!r} 不在 {container!r} 中"
        )


def skip(reason: str):
    """跳过当前测试"""
    raise SkipTest(reason)


def main():
    args = sys.argv[1:]
    run_func = "--perf" not in args  # 默认运行
    run_perf = "--func" not in args

    result = TestResult()

    if run_func:
        log.info("=" * 60)
        log.info("功能测试")
        log.info("=" * 60)
        from tests import test_core
        test_core.run_all(result)

    if run_perf:
        log.info("=" * 60)
        log.info("性能测试")
        log.info("=" * 60)
        from tests import test_perf
        test_perf.run_all(result)

    log.info("=" * 60)
    log.info("测试结果: %s", result.summary)
    if result.failed:
        log.info("失败列表:")
        for name, err in result.failed:
            log.info("  - %s: %s", name, err)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
