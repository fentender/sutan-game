"""
MergeService — 兼容别名，实际实现在 ModManager

保留此模块使现有 import 路径继续工作。
"""
from .mod_manager import ModManager as MergeService

__all__ = ["MergeService"]
