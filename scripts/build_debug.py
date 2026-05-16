"""Debug 模式构建 C++ 扩展并部署，同时禁用 editable rebuild 防止覆盖。"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / "build_debug"
PYTHON = r"C:\Python314\python.exe"


def _find_editable_py() -> Path | None:
    import site
    for sp in site.getsitepackages():
        p = Path(sp) / "_sudan_game_merger_editable.py"
        if p.exists():
            return p
    return None


def _patch_editable(enable_rebuild: bool) -> None:
    editable = _find_editable_py()
    if editable is None:
        print("警告：未找到 _sudan_game_merger_editable.py")
        return
    text = editable.read_text(encoding="utf-8")
    old = ", True, True," if not enable_rebuild else ", False, True,"
    new = ", False, True," if not enable_rebuild else ", True, True,"
    if old in text:
        text = text.replace(old, new)
        editable.write_text(text, encoding="utf-8")
        action = "恢复" if enable_rebuild else "禁用"
        print(f"已{action} editable rebuild")
    else:
        print(f"editable rebuild 已经是{'启用' if enable_rebuild else '禁用'}状态")


def _site_packages_dir() -> Path:
    import site
    for sp in site.getsitepackages():
        if (Path(sp) / "_sudan_game_merger_editable.py").exists():
            return Path(sp)
    return Path(site.getsitepackages()[-1])


def main() -> None:
    if "--restore" in sys.argv:
        _patch_editable(enable_rebuild=True)
        subprocess.check_call([PYTHON, "-m", "pip", "install", "-e", str(ROOT)])
        print("已恢复 Release 版本。")
        return

    subprocess.check_call([
        "cmake", "-B", str(BUILD_DIR), "-S", str(ROOT),
        "-G", "Visual Studio 17 2022", "-A", "x64",
        f"-DPython_EXECUTABLE={PYTHON}",
        "-DSULTAN_PERF=ON",
        "-DCMAKE_C_FLAGS_RELWITHDEBINFO=/Zi /Od /Ob0 /DNDEBUG",
        "-DCMAKE_CXX_FLAGS_RELWITHDEBINFO=/Zi /Od /Ob0 /DNDEBUG",
    ])

    subprocess.check_call([
        "cmake", "--build", str(BUILD_DIR),
        "--config", "RelWithDebInfo", "--parallel",
    ])

    csrc_dir = BUILD_DIR / "csrc" / "RelWithDebInfo"
    pyd_files = list(csrc_dir.glob("sultan_core*.pyd"))
    pdb_files = list(csrc_dir.glob("sultan_core*.pdb"))
    pdb_files += list(csrc_dir.glob("sultan_core_lib*.pdb"))

    if not pyd_files:
        print(f"错误：在 {csrc_dir} 中未找到 .pyd 文件")
        sys.exit(1)

    # 先禁用 editable rebuild，防止 import 触发 Release 覆盖
    _patch_editable(enable_rebuild=False)

    site_pkg = _site_packages_dir()

    for f in pyd_files + pdb_files:
        dst = site_pkg / f.name
        try:
            shutil.copy2(f, dst)
            print(f"复制 {f.name} -> {dst}")
        except PermissionError:
            print(f"跳过（文件被占用）：{f.name}  请关闭占用进程后重试")

    print(f"\nDebug 构建完成（/Od 禁优化）。可直接用任意 Python 配置启动调试。")
    print(f"恢复 Release: python scripts/build_debug.py --restore")


if __name__ == "__main__":
    main()
