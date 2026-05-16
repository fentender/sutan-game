"""对比两个合成Mod目录的JSON差异。"""

import json
import os
import re
import sys
from pathlib import Path

MOD_OLD = Path(r"C:\Users\Lenovo\Documents\DoubleCross\SultansGame\Mod\0000000001\config")
MOD_NEW = Path(r"C:\Users\Lenovo\Documents\DoubleCross\SultansGame\Mod\0000000002\config")

STRIP_COMMENT_RE = re.compile(r'//.*?$', re.MULTILINE)
TRAILING_COMMA_RE = re.compile(r',\s*([}\]])')


def clean_json(text: str) -> str:
    text = STRIP_COMMENT_RE.sub('', text)
    text = TRAILING_COMMA_RE.sub(r'\1', text)
    return text


def load_json(path: Path) -> tuple[object, str | None]:
    try:
        raw = path.read_text(encoding='utf-8-sig')
        cleaned = clean_json(raw)
        return json.loads(cleaned), None
    except Exception as e:
        return None, str(e)


def collect_json_files(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for p in root.rglob('*'):
        if p.suffix == '.json':
            rel = p.relative_to(root).as_posix()
            result[rel] = p
    return result


def deep_diff(a: object, b: object, path: str = "") -> list[str]:
    """递归对比两个JSON对象，返回差异描述列表。"""
    diffs: list[str] = []
    if type(a) != type(b):
        diffs.append(f"  {path}: 类型不同 {type(a).__name__} vs {type(b).__name__}")
        return diffs

    if isinstance(a, dict) and isinstance(b, dict):
        all_keys = set(a.keys()) | set(b.keys())
        for k in sorted(all_keys):
            child_path = f"{path}.{k}" if path else k
            if k not in a:
                val_preview = _preview(b[k])
                diffs.append(f"  {child_path}: 仅新版有 {val_preview}")
            elif k not in b:
                val_preview = _preview(a[k])
                diffs.append(f"  {child_path}: 仅旧版有 {val_preview}")
            else:
                diffs.extend(deep_diff(a[k], b[k], child_path))

    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"  {path}: 数组长度不同 {len(a)} vs {len(b)}")
        for i in range(min(len(a), len(b))):
            diffs.extend(deep_diff(a[i], b[i], f"{path}[{i}]"))
        if len(a) > len(b):
            for i in range(len(b), len(a)):
                diffs.append(f"  {path}[{i}]: 仅旧版有 {_preview(a[i])}")
        elif len(b) > len(a):
            for i in range(len(a), len(b)):
                diffs.append(f"  {path}[{i}]: 仅新版有 {_preview(b[i])}")

    else:
        if a != b:
            diffs.append(f"  {path}: 值不同 {_preview(a)} vs {_preview(b)}")

    return diffs


def _preview(val: object, max_len: int = 80) -> str:
    s = json.dumps(val, ensure_ascii=False)
    if len(s) > max_len:
        s = s[:max_len] + "..."
    return s


def main() -> None:
    old_files = collect_json_files(MOD_OLD)
    new_files = collect_json_files(MOD_NEW)

    all_rels = sorted(set(old_files.keys()) | set(new_files.keys()))

    only_old: list[str] = []
    only_new: list[str] = []
    diff_files: list[tuple[str, list[str]]] = []
    identical_count = 0
    error_files: list[tuple[str, str]] = []

    for rel in all_rels:
        if rel not in new_files:
            only_old.append(rel)
            continue
        if rel not in old_files:
            only_new.append(rel)
            continue

        obj_old, err_old = load_json(old_files[rel])
        obj_new, err_new = load_json(new_files[rel])

        if err_old or err_new:
            error_files.append((rel, f"旧版: {err_old}, 新版: {err_new}"))
            continue

        diffs = deep_diff(obj_old, obj_new)
        if diffs:
            diff_files.append((rel, diffs))
        else:
            identical_count += 1

    # 输出报告
    print("=" * 80)
    print(f"合成Mod对比报告")
    print(f"旧版(0000000001): {MOD_OLD}")
    print(f"新版(0000000002): {MOD_NEW}")
    print("=" * 80)

    print(f"\n共 {len(all_rels)} 个JSON文件，"
          f"相同 {identical_count}，"
          f"有差异 {len(diff_files)}，"
          f"仅旧版 {len(only_old)}，"
          f"仅新版 {len(only_new)}，"
          f"解析错误 {len(error_files)}")

    if only_old:
        print(f"\n--- 仅旧版有的文件 ({len(only_old)}) ---")
        for f in only_old:
            print(f"  {f}")

    if only_new:
        print(f"\n--- 仅新版有的文件 ({len(only_new)}) ---")
        for f in only_new:
            print(f"  {f}")

    if error_files:
        print(f"\n--- 解析错误 ({len(error_files)}) ---")
        for f, err in error_files:
            print(f"  {f}: {err}")

    if diff_files:
        print(f"\n--- 有差异的文件 ({len(diff_files)}) ---")
        for rel, diffs in diff_files:
            print(f"\n[{rel}] ({len(diffs)} 处差异)")
            # 限制每个文件输出的差异数
            for d in diffs[:50]:
                print(d)
            if len(diffs) > 50:
                print(f"  ... 还有 {len(diffs) - 50} 处差异未显示")

    # 保存完整报告
    report_path = Path(__file__).parent / "compare_report.txt"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(f"合成Mod对比报告\n")
        f.write(f"旧版: {MOD_OLD}\n新版: {MOD_NEW}\n\n")

        if only_old:
            f.write(f"仅旧版有的文件 ({len(only_old)}):\n")
            for name in only_old:
                f.write(f"  {name}\n")

        if only_new:
            f.write(f"\n仅新版有的文件 ({len(only_new)}):\n")
            for name in only_new:
                f.write(f"  {name}\n")

        if diff_files:
            f.write(f"\n有差异的文件 ({len(diff_files)}):\n")
            for rel, diffs in diff_files:
                f.write(f"\n[{rel}] ({len(diffs)} 处差异)\n")
                for d in diffs:
                    f.write(d + "\n")

    print(f"\n完整报告已保存: {report_path}")


if __name__ == '__main__':
    main()
