"""PyInstaller 打包入口。"""
import json
import sys

from src.core.infra.error_reporter import install_exception_hooks
from src.runtime_paths import runtime_check

install_exception_hooks()


def run() -> None:
    if "--runtime-check" in sys.argv:
        print(json.dumps(runtime_check(), ensure_ascii=False))
        return

    from src.main import main

    main()


run()
