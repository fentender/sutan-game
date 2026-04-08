"""PyInstaller 打包入口"""
import sys
import traceback
from pathlib import Path


def _excepthook(exc_type, exc_value, exc_tb):
    """将未捕获异常写入 crash.log，避免 --windowed 模式下异常不可见"""
    if getattr(sys, 'frozen', False):
        log_path = Path(sys.executable).parent / "crash.log"
        with open(log_path, 'a', encoding='utf-8') as f:
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
    traceback.print_exception(exc_type, exc_value, exc_tb)


sys.excepthook = _excepthook

from src.main import main
main()
