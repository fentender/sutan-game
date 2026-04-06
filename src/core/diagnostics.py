"""
线程安全的全局诊断信息收集器

替代原来散落在各模块中的 parse_warnings / scan_errors / merge_warnings 列表。
使用 category 区分消息来源："parse" / "scan" / "merge"。
"""
import threading


class Diagnostics:
    def __init__(self):
        self._lock = threading.Lock()
        self._messages: dict[str, list[str]] = {}

    def warn(self, category: str, msg: str):
        """线程安全地追加一条诊断消息"""
        with self._lock:
            self._messages.setdefault(category, []).append(msg)

    def snapshot(self, *categories: str) -> list[str]:
        """返回指定类别的消息并清空。不传参则返回全部类别。"""
        with self._lock:
            if not categories:
                categories = tuple(self._messages.keys())
            result = []
            for cat in categories:
                result.extend(self._messages.get(cat, []))
                self._messages.pop(cat, None)
            return result


diag = Diagnostics()
