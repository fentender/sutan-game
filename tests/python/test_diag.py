"""
诊断模块测试 — 通过 nanobind 绑定测试 C++ DiagManager
"""
import threading

from sultan_core import diag
from tests.python.test_runner import TestResult, assert_eq, assert_true, run_test


def test_emit_to_buffer() -> None:
    """emit 三级别消息 -> snapshot 返回正确消息"""
    mgr = diag.get_manager()
    mgr.snapshot()  # 清空

    diag._test_emit(diag.Level.INFO, "parse", "info msg")
    diag._test_emit(diag.Level.WARN, "parse", "warn msg")
    diag._test_emit(diag.Level.ERROR, "merge", "error msg")

    msgs = mgr.snapshot()
    assert_eq(len(msgs), 3, "消息总数")
    assert_eq(msgs[0].level, diag.Level.INFO)
    assert_eq(msgs[0].category, "parse")
    assert_eq(msgs[0].message, "info msg")
    assert_eq(msgs[1].level, diag.Level.WARN)
    assert_eq(msgs[2].level, diag.Level.ERROR)
    assert_eq(msgs[2].category, "merge")


def test_snapshot_by_category() -> None:
    """按类别拉取，另一类别保留"""
    mgr = diag.get_manager()
    mgr.snapshot()

    diag._test_emit(diag.Level.WARN, "parse", "p1")
    diag._test_emit(diag.Level.WARN, "parse", "p2")
    diag._test_emit(diag.Level.ERROR, "merge", "m1")

    parse_msgs = mgr.snapshot(["parse"])
    assert_eq(len(parse_msgs), 2, "parse 类别消息数")

    remaining = mgr.snapshot()
    assert_eq(len(remaining), 1, "剩余消息数")
    assert_eq(remaining[0].category, "merge")


def test_snapshot_clears() -> None:
    """snapshot 后再次调用返回空"""
    mgr = diag.get_manager()
    mgr.snapshot()

    diag._test_emit(diag.Level.WARN, "test", "msg")
    first = mgr.snapshot()
    assert_eq(len(first), 1)

    second = mgr.snapshot()
    assert_eq(len(second), 0, "第二次 snapshot 应为空")


def test_notify_with_callback() -> None:
    """注册回调 + notify=true -> 回调触发，消息不在 buffer"""
    mgr = diag.get_manager()
    mgr.snapshot()

    received: list[tuple[diag.Level, str, str]] = []

    def on_diag(level: diag.Level, category: str, msg: str) -> None:
        received.append((level, category, msg))

    mgr.set_callback(on_diag)
    try:
        diag._test_emit(diag.Level.ERROR, "merge", "critical", notify=True)

        assert_eq(len(received), 1, "回调应触发一次")
        assert_eq(received[0][0], diag.Level.ERROR)
        assert_eq(received[0][1], "merge")
        assert_eq(received[0][2], "critical")

        msgs = mgr.snapshot()
        assert_eq(len(msgs), 0, "notify=true 的消息不应进 buffer")
    finally:
        mgr.clear_callback()


def test_notify_without_callback() -> None:
    """无回调 + notify=true -> 消息回退进 buffer"""
    mgr = diag.get_manager()
    mgr.snapshot()
    mgr.clear_callback()

    diag._test_emit(diag.Level.ERROR, "merge", "fallback", notify=True)

    msgs = mgr.snapshot()
    assert_eq(len(msgs), 1, "应回退进 buffer")
    assert_eq(msgs[0].message, "fallback")


def test_notify_false_with_callback() -> None:
    """有回调 + notify=false -> 消息进 buffer，回调未触发"""
    mgr = diag.get_manager()
    mgr.snapshot()

    called = False

    def on_diag(level: diag.Level, category: str, msg: str) -> None:
        nonlocal called
        called = True

    mgr.set_callback(on_diag)
    try:
        diag._test_emit(diag.Level.ERROR, "merge", "normal", notify=False)

        assert_true(not called, "notify=false 不应触发回调")
        msgs = mgr.snapshot()
        assert_eq(len(msgs), 1, "应进 buffer")
    finally:
        mgr.clear_callback()


def test_clear_callback() -> None:
    """注册后清除 -> emit(notify=true) -> 消息进 buffer"""
    mgr = diag.get_manager()
    mgr.snapshot()

    called = False

    def on_diag(level: diag.Level, category: str, msg: str) -> None:
        nonlocal called
        called = True

    mgr.set_callback(on_diag)
    mgr.clear_callback()

    diag._test_emit(diag.Level.ERROR, "merge", "after clear", notify=True)

    assert_true(not called, "清除回调后不应触发")
    msgs = mgr.snapshot()
    assert_eq(len(msgs), 1, "应回退进 buffer")


def test_thread_safety() -> None:
    """8 线程各 push 100 条 -> snapshot 总数 800"""
    mgr = diag.get_manager()
    mgr.snapshot()

    n_threads = 8
    msgs_per_thread = 100
    barrier = threading.Barrier(n_threads)

    def worker(tid: int) -> None:
        barrier.wait()
        for i in range(msgs_per_thread):
            diag._test_emit(diag.Level.WARN, "thread", f"t{tid}-{i}")

    threads = [threading.Thread(target=worker, args=(t,))
               for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    msgs = mgr.snapshot()
    assert_eq(len(msgs), n_threads * msgs_per_thread, "并发消息总数")


def test_message_repr() -> None:
    """Message.__repr__ 格式正确"""
    mgr = diag.get_manager()
    mgr.snapshot()

    diag._test_emit(diag.Level.WARN, "parse", "test repr")
    msgs = mgr.snapshot()
    r = repr(msgs[0])
    assert_true("WARN" in r, "__repr__ 应包含级别")
    assert_true("parse" in r, "__repr__ 应包含类别")


def run_all(result: TestResult) -> None:
    run_test("diag_emit_to_buffer", test_emit_to_buffer, result)
    run_test("diag_snapshot_by_category", test_snapshot_by_category, result)
    run_test("diag_snapshot_clears", test_snapshot_clears, result)
    run_test("diag_notify_with_callback", test_notify_with_callback, result)
    run_test("diag_notify_without_callback", test_notify_without_callback, result)
    run_test("diag_notify_false_with_callback", test_notify_false_with_callback, result)
    run_test("diag_clear_callback", test_clear_callback, result)
    run_test("diag_thread_safety", test_thread_safety, result)
    run_test("diag_message_repr", test_message_repr, result)
