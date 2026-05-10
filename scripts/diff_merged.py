"""对比两个合并Mod目录，验证C++重构前后输出一致性。

用法：python scripts/diff_merged.py <dir_before> <dir_after>
"""
import filecmp
import json
import sys
from pathlib import Path
from collections import Counter

SKIP_PATTERNS = {'.idea', '.DS_Store', '__pycache__'}


def should_skip(rel: Path) -> bool:
    return any(part in SKIP_PATTERNS for part in rel.parts)


def collect_files(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for p in root.rglob('*'):
        if p.is_file():
            rel = p.relative_to(root)
            if not should_skip(rel):
                result[str(rel).replace('\\', '/')] = p
    return result


def load_json(path: Path) -> object:
    text = path.read_text(encoding='utf-8-sig')
    return json.loads(text)


def str_diff_detail(a: str, b: str) -> str:
    """找出两个字符串第一个不同的位置"""
    min_len = min(len(a), len(b))
    for i in range(min_len):
        if a[i] != b[i]:
            ctx_start = max(0, i - 15)
            ctx_a = a[ctx_start:i+15]
            ctx_b = b[ctx_start:i+15]
            return f'位置{i}: ...{ctx_a!r}... vs ...{ctx_b!r}...'
    if len(a) != len(b):
        shorter = 'A' if len(a) < len(b) else 'B'
        longer_tail = (b if len(a) < len(b) else a)[min_len:min_len+30]
        return f'{shorter}较短(长度{len(a)} vs {len(b)})，{shorter}末尾后: {longer_tail!r}'
    return '未知差异'


class DiffStats:
    def __init__(self) -> None:
        self.type_mismatches: Counter[str] = Counter()
        self.value_diffs: list[tuple[str, str, str]] = []  # (file, path, detail)
        self.field_only_a: list[tuple[str, str]] = []
        self.field_only_b: list[tuple[str, str]] = []
        self.str_trailing_space: int = 0
        self.int_float: int = 0

    def report(self, out: object) -> None:
        p = lambda *a, **kw: print(*a, **kw, file=out)

        if self.type_mismatches:
            p('\n=== 类型差异分布 ===')
            for (t, cnt) in self.type_mismatches.most_common():
                p(f'  {t}: {cnt}')

        if self.field_only_a or self.field_only_b:
            p(f'\n=== 字段存在性差异 ===')
            p(f'  仅在A: {len(self.field_only_a)}处')
            p(f'  仅在B: {len(self.field_only_b)}处')
            for f, path in self.field_only_a[:20]:
                p(f'    [A] {f} :: {path}')
            if len(self.field_only_a) > 20:
                p(f'    ... 还有{len(self.field_only_a) - 20}处')
            for f, path in self.field_only_b[:20]:
                p(f'    [B] {f} :: {path}')
            if len(self.field_only_b) > 20:
                p(f'    ... 还有{len(self.field_only_b) - 20}处')

        if self.int_float:
            p(f'\n=== int vs float 差异: {self.int_float}处 ===')

        if self.str_trailing_space:
            p(f'\n=== 字符串尾部空格差异: {self.str_trailing_space}处 ===')

        value_real = [v for v in self.value_diffs if '[尾部空格]' not in v[2]]
        if value_real:
            p(f'\n=== 实际值差异 ({len(value_real)}处) ===')
            for f, path, detail in value_real[:50]:
                p(f'  {f} :: {path}')
                p(f'    {detail}')
            if len(value_real) > 50:
                p(f'  ... 还有{len(value_real) - 50}处')


def deep_diff(a: object, b: object, path: str, file_rel: str, stats: DiffStats) -> int:
    """递归比较两个JSON值，返回差异数。"""
    count = 0

    if type(a) is not type(b):
        ta = type(a).__name__
        tb = type(b).__name__
        # int vs float 特殊处理
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if float(a) == float(b):
                stats.int_float += 1
                return 1
        stats.type_mismatches[f'{ta} vs {tb}'] += 1
        count += 1
        return count

    if isinstance(a, dict):
        assert isinstance(b, dict)
        keys_a = set(a.keys())
        keys_b = set(b.keys())
        for k in sorted(keys_a - keys_b):
            stats.field_only_a.append((file_rel, f'{path}.{k}'))
            count += 1
        for k in sorted(keys_b - keys_a):
            stats.field_only_b.append((file_rel, f'{path}.{k}'))
            count += 1
        for k in sorted(keys_a & keys_b):
            count += deep_diff(a[k], b[k], f'{path}.{k}', file_rel, stats)
        return count

    if isinstance(a, list):
        assert isinstance(b, list)
        if len(a) != len(b):
            stats.value_diffs.append((file_rel, path, f'长度不同 {len(a)} vs {len(b)}'))
            count += 1
            min_len = min(len(a), len(b))
        else:
            min_len = len(a)
        for i in range(min_len):
            count += deep_diff(a[i], b[i], f'{path}[{i}]', file_rel, stats)
        return count

    if a != b:
        if isinstance(a, str) and isinstance(b, str):
            if a.rstrip() == b.rstrip():
                stats.str_trailing_space += 1
                stats.value_diffs.append((file_rel, path, '[尾部空格]'))
            else:
                detail = str_diff_detail(a, b)
                stats.value_diffs.append((file_rel, path, detail))
        else:
            stats.value_diffs.append((file_rel, path, f'{a!r} → {b!r}'))
        count += 1

    return count


def main() -> None:
    if len(sys.argv) != 3:
        print(f'用法: python {sys.argv[0]} <dir_before> <dir_after>')
        sys.exit(1)

    out = open('diff_report.txt', 'w', encoding='utf-8')

    dir_a = Path(sys.argv[1])
    dir_b = Path(sys.argv[2])

    if not dir_a.is_dir() or not dir_b.is_dir():
        print('错误：目录不存在', file=out)
        sys.exit(1)

    p = lambda *a, **kw: print(*a, **kw, file=out)

    p(f'A: {dir_a}')
    p(f'B: {dir_b}')
    p()

    files_a = collect_files(dir_a)
    files_b = collect_files(dir_b)

    keys_a = set(files_a.keys())
    keys_b = set(files_b.keys())

    only_a = sorted(keys_a - keys_b)
    only_b = sorted(keys_b - keys_a)
    common = sorted(keys_a & keys_b)

    if only_a:
        p(f'=== 仅在A中存在的文件 ({len(only_a)}) ===')
        for f in only_a:
            p(f'  {f}')
        p()

    if only_b:
        p(f'=== 仅在B中存在的文件 ({len(only_b)}) ===')
        for f in only_b:
            p(f'  {f}')
        p()

    json_diff_count = 0
    json_same_count = 0
    bin_diff_count = 0
    bin_same_count = 0
    json_error_count = 0
    json_error_files: list[str] = []

    stats = DiffStats()
    diff_files: list[tuple[str, int]] = []

    for rel in common:
        path_a = files_a[rel]
        path_b = files_b[rel]

        if rel.endswith('.json'):
            try:
                data_a = load_json(path_a)
                data_b = load_json(path_b)
            except Exception:
                # JSON 解析失败，退化为纯文本比较
                text_a = path_a.read_text(encoding='utf-8-sig')
                text_b = path_b.read_text(encoding='utf-8-sig')
                if text_a != text_b:
                    json_error_count += 1
                    json_error_files.append(rel)
                else:
                    json_same_count += 1
                continue

            n = deep_diff(data_a, data_b, '$', rel, stats)
            if n > 0:
                json_diff_count += 1
                diff_files.append((rel, n))
            else:
                json_same_count += 1
        else:
            if filecmp.cmp(str(path_a), str(path_b), shallow=False):
                bin_same_count += 1
            else:
                bin_diff_count += 1
                p(f'  [二进制差异] {rel}')

    # 按差异数排序的文件列表
    if diff_files:
        p(f'\n=== 有差异的JSON文件 ({json_diff_count}个，按差异数排序) ===')
        diff_files.sort(key=lambda x: -x[1])
        for rel, n in diff_files[:30]:
            p(f'  {rel}: {n}处差异')
        if len(diff_files) > 30:
            p(f'  ... 还有{len(diff_files) - 30}个文件')

    # 解析错误（文本不一致）
    if json_error_files:
        p(f'\n=== JSON解析失败且文本不一致 ({json_error_count}个文件) ===')
        for f in json_error_files[:20]:
            p(f'  {f}')
        if len(json_error_files) > 20:
            p(f'  ... 还有{len(json_error_files) - 20}个文件')

    # 统计报告
    stats.report(out)

    p('\n=== 汇总 ===')
    p(f'仅在A的文件:     {len(only_a)}')
    p(f'仅在B的文件:     {len(only_b)}')
    p(f'JSON一致:        {json_same_count}')
    p(f'JSON有差异:      {json_diff_count}')
    p(f'JSON解析失败:    {json_error_count}')
    p(f'二进制一致:      {bin_same_count}')
    p(f'二进制差异:      {bin_diff_count}')
    p()

    total_diff = len(only_a) + len(only_b) + json_diff_count + bin_diff_count + json_error_count
    if total_diff == 0:
        p('✓ 两个目录完全一致')
    else:
        p(f'✗ 发现 {total_diff} 个文件有差异')

    out.close()
    print(f'报告已写入 diff_report.txt')


if __name__ == '__main__':
    main()
