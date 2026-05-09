"""
核心合并算法 — 通过 C++ State/Delta 引擎执行深度合并
"""
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from sultan_core.json import JsonDoc
from sultan_core.state import JsonState
from sultan_core.delta import deserialize_and_apply

from ..infra.diagnostics import diag, merge_ctx
from ..infra.profiler import profile
from ..infra.types import (
    CancelCheck,
    JsonObject,
    ParseFailure,
    ProgressCallback,
)
from ..data_manager import DataManager

WHOLE_FILE_REPLACE = {'sfx_config.json'}


@dataclass
class MergeResult:
    """合并结果"""
    merged_doc: JsonDoc = field(default_factory=lambda: JsonDoc.parse("{}"))


@profile
def merge_file(
    base_doc: JsonDoc,
    mod_data_list: list[tuple[str, str, JsonDoc, str]],
    rel_path: str = "",
    schema: JsonObject | None = None,
) -> MergeResult:
    """合并单个文件（C++ State/Delta API）。

    参数:
        base_doc: 游戏本体 JSON 文档
        mod_data_list: [(mod_id, mod_name, delta_doc, source_file), ...] 按优先级排序
        rel_path: 文件相对路径
        schema: 仅用于兼容签名（C++ 层已内化 schema 语义）
    """
    result = MergeResult()
    file_name = Path(rel_path).name if rel_path else ""

    if file_name in WHOLE_FILE_REPLACE:
        if mod_data_list:
            _, last_mod_name, _, _ = mod_data_list[-1]
            state = JsonState.from_doc(base_doc)
            for step, (_, _, delta_doc, _) in enumerate(mod_data_list, 1):
                deserialize_and_apply(delta_doc, state, version=step)
            result.merged_doc = state.to_doc()
            if len(mod_data_list) > 1:
                diag.warn("merge", f"{rel_path}: 多个 mod 修改此文件（整文件替换模式），最终使用 {last_mod_name}")
        else:
            result.merged_doc = base_doc
        return result

    state = JsonState.from_doc(base_doc)
    dm = DataManager.instance()

    for version, (mod_id, mod_name, delta_doc, source_file) in enumerate(mod_data_list, 1):
        merge_ctx.mod_name = mod_name
        merge_ctx.mod_id = mod_id
        merge_ctx.rel_path = rel_path
        merge_ctx.source_file = source_file

        deserialize_and_apply(delta_doc, state, version=version)

        override_doc = dm.get_override_doc(mod_id, rel_path)
        if override_doc is not None:
            deserialize_and_apply(override_doc, state,
                                  version=version, is_override=True)

    result.merged_doc = state.to_doc()
    return result


@profile
def merge_all_files(
    mod_configs: list[tuple[str, str, Path]],
    output_path: Path,
    schema_dir: Path | None = None,
    cancel_check: CancelCheck | None = None,
    progress_cb: ProgressCallback | None = None,
) -> dict[str, MergeResult]:
    """合并所有文件并写出。"""
    diag.snapshot("merge")

    store = DataManager.instance()
    from .cache import MergeCache
    cache = MergeCache.instance()
    mod_ids = [mod_id for mod_id, _, _ in mod_configs]

    results: dict[str, MergeResult] = {}

    all_paths = sorted(store.all_rel_paths())
    total = len(all_paths)
    for i, rel_path in enumerate(all_paths):
        if cancel_check:
            cancel_check()
        if progress_cb:
            progress_cb(i, total)

        has_mod = any(store.has_mod(mid, rel_path) for mid in mod_ids)
        if not has_mod:
            continue

        state = cache.get(rel_path, mod_configs, schema_dir, need_steps=False)
        result = MergeResult(merged_doc=state.final_doc)
        results[rel_path] = result

        out_file = output_path / rel_path
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(state.final_doc.to_string(), encoding='utf-8')

    if progress_cb:
        progress_cb(total, total)
    return results


def _append_dict_content(src: Path, dest: Path) -> bool:
    """将解析失败的 dictionary 文件内容追加到已有合并结果末尾。"""
    failed_text = src.read_text(encoding='utf-8')
    first_brace = failed_text.find('{')
    last_brace = failed_text.rfind('}')
    if first_brace == -1 or last_brace == -1 or first_brace >= last_brace:
        return False
    inner = failed_text[first_brace + 1 : last_brace].strip()
    if not inner:
        return False

    existing_text = dest.read_text(encoding='utf-8')
    close_brace = existing_text.rfind('}')
    if close_brace == -1:
        return False

    before = existing_text[:close_brace].rstrip()
    if before and before[-1] not in ('{', ','):
        before += ','
    dest.write_text(before + '\n' + inner + '\n}', encoding='utf-8')
    return True


def copy_failed_files(
    mod_configs: list[tuple[str, str, Path]],
    output_path: Path,
) -> list[str]:
    """将解析失败但用户选择忽略的 JSON 文件原样复制到输出目录。"""
    from ..json import classify_json

    store = DataManager.instance()
    ignored = store.get_ignored_failures()
    if not ignored:
        return []

    enabled_ids = {mid for mid, _, _ in mod_configs}
    relevant = [f for f in ignored if f.mod_id in enabled_ids]
    if not relevant:
        return []

    priority: dict[str, int] = {mid: i for i, (mid, _, _) in enumerate(mod_configs)}
    best: dict[str, ParseFailure] = {}
    for f in relevant:
        existing = best.get(f.rel_path)
        if existing is None or priority.get(f.mod_id, -1) > priority.get(existing.mod_id, -1):
            best[f.rel_path] = f

    copied: list[str] = []
    for rel_path, failure in sorted(best.items()):
        src = failure.file_path
        dest = output_path / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.exists():
            base_doc = store.get_base(rel_path)
            file_type = classify_json(base_doc) if base_doc.valid() else "config"
            if file_type == "dictionary" and _append_dict_content(src, dest):
                diag.warn("merge", f"{rel_path}: JSON 解析失败，已从 {failure.mod_name} 追加内容到合并结果")
                copied.append(rel_path)
                continue

        shutil.copy2(src, dest)
        diag.warn("merge", f"{rel_path}: JSON 解析失败，已从 {failure.mod_name} 整文件复制")
        copied.append(rel_path)

    return copied
