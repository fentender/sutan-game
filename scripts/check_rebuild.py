"""启动包装器：检测 C++ 源文件是否有修改，无修改则跳过 editable rebuild。"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_STAMP = ROOT / "build" / ".rebuild_stamp"

_SOURCE_PATTERNS: list[str] = [
    "csrc/**/*.cpp",
    "csrc/**/*.h",
    "csrc/**/*.cxx",
]
_CMAKE_FILES: list[str] = [
    "CMakeLists.txt",
    "csrc/CMakeLists.txt",
]


def _newest_source_mtime() -> float:
    mtimes: list[float] = []
    for pat in _SOURCE_PATTERNS:
        mtimes.extend(p.stat().st_mtime for p in ROOT.glob(pat))
    for name in _CMAKE_FILES:
        p = ROOT / name
        if p.exists():
            mtimes.append(p.stat().st_mtime)
    return max(mtimes, default=0.0)


def _find_build_dir() -> str | None:
    """从已注册的 editable finder 中获取 build 目录路径。"""
    for finder in sys.meta_path:
        if type(finder).__name__ == "ScikitBuildRedirectingFinder":
            path = getattr(finder, "path", None)
            if path:
                return str(path)
    return None


def _needs_rebuild() -> bool:
    if not _STAMP.exists():
        return True
    return _newest_source_mtime() > _STAMP.stat().st_mtime


def _touch_stamp() -> None:
    _STAMP.parent.mkdir(parents=True, exist_ok=True)
    _STAMP.touch()


def main() -> None:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    if _needs_rebuild():
        print("[check_rebuild] C++ 源文件有变动，将触发重编译")
        _touch_stamp()
    else:
        build_dir = _find_build_dir()
        if build_dir:
            existing = os.environ.get("SKBUILD_EDITABLE_SKIP", "")
            if existing:
                os.environ["SKBUILD_EDITABLE_SKIP"] = existing + os.pathsep + build_dir
            else:
                os.environ["SKBUILD_EDITABLE_SKIP"] = build_dir
        else:
            print("[check_rebuild] 未找到 build 目录，将触发重编译")

    runpy.run_module("src.main", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
