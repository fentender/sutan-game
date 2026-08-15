"""
GUI 层类型/常量/纯工具函数的唯一导入点。

GUI 模块应仅从此模块 import 类型和工具，通过 AppService 实例调用业务逻辑。
"""
from sultan_core.delta import DeltaDict
from sultan_core.json import JsonDoc, ParseError

from ..config import infer_workshop_path_from_game
from .app_service import AppService, InitState
from .infra.async_task import (
    AsyncTaskHandle,
    TaskCallback,
    TaskCancelled,
    TaskDone,
    TaskError,
    TaskEvent,
    TaskProgress,
    TaskStage,
)
from .infra.diagnostics import ERROR, INFO, WARNING, diag
from .infra.error_reporter import ExceptionReport, report_exception
from .infra.types import FIELD_SEP, ChangeKind, MergeMode, ParseFailure
from .merge.cache import FileMergeState, StepState
from .merge.formatter import build_padded_texts, diff_opcodes
from .mod.conflict import DeletionRecord, FileOverrideInfo
from .mod.id_remap import RemapTable
from .mod.scanner import ModInfo

__all__ = [
    "AppService",
    "AsyncTaskHandle",
    "ChangeKind",
    "DeletionRecord",
    "DeltaDict",
    "ERROR",
    "ExceptionReport",
    "FIELD_SEP",
    "FileOverrideInfo",
    "FileMergeState",
    "INFO",
    "InitState",
    "JsonDoc",
    "MergeMode",
    "ModInfo",
    "ParseError",
    "ParseFailure",
    "RemapTable",
    "StepState",
    "TaskCallback",
    "TaskCancelled",
    "TaskDone",
    "TaskError",
    "TaskEvent",
    "TaskProgress",
    "TaskStage",
    "WARNING",
    "build_padded_texts",
    "diag",
    "diff_opcodes",
    "infer_workshop_path_from_game",
    "report_exception",
]
