"""
一键发布脚本 —— 构建 CAS + 创建 tag + 推送（CI 自动完成打包和创建 Release）

用法:
    python packaging/release.py                          # 正常发布
    python packaging/release.py --dry-run                # 仅打印步骤，不执行
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# ── 常量 ─────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent

GITHUB_OWNER = "fentender"
GITHUB_REPO = "sutan-game"
GITEE_OWNER = "fentende125"
GITEE_REPO = "sutan-game"

PROXY = os.environ.get("HTTPS_PROXY", os.environ.get("HTTP_PROXY", ""))


# ── 工具函数 ──────────────────────────────────────────────────────────


def _read_version() -> str:
    config_path = ROOT / "src" / "config.py"
    text = config_path.read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    if not match:
        print("错误: 无法从 src/config.py 读取 APP_VERSION")
        sys.exit(1)
    return match.group(1)


def _run(cmd: list[str], *, dry_run: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    print(f"  → {' '.join(cmd)}")
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    env = os.environ.copy()
    if PROXY:
        env.setdefault("HTTPS_PROXY", PROXY)
        env.setdefault("HTTP_PROXY", PROXY)
    return subprocess.run(cmd, check=check, capture_output=True, text=True, cwd=str(ROOT), env=env, encoding="utf-8", errors="replace")


def _check_prerequisites() -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=str(ROOT),
        encoding="utf-8", errors="replace"
    )
    if result.stdout.strip():
        print("错误: git 工作区有未提交的更改:")
        print(result.stdout)
        sys.exit(1)


def _create_tag(version: str, dry_run: bool) -> None:
    result = subprocess.run(
        ["git", "tag", "-l", version], capture_output=True, text=True, cwd=str(ROOT)
    )
    if version in result.stdout.strip().splitlines():
        print(f"  标签 {version} 已存在，跳过")
        return
    _run(["git", "tag", version], dry_run=dry_run)


def _git_push(version: str, dry_run: bool) -> None:
    _run(["git", "push", "origin", "master"], dry_run=dry_run)
    _run(["git", "push", "origin", version], dry_run=dry_run, check=False)
    _run(["git", "push", "gitee", "master"], dry_run=dry_run, check=False)
    _run(["git", "push", "gitee", version], dry_run=dry_run, check=False)


def _build_history_pack(dry_run: bool) -> None:
    print("  → 构建 history_config_pack（CAS 内容寻址去重）")
    if dry_run:
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_history_pack import build
    build(clean=True)


# ── 主流程 ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="一键发布脚本（CI 自动打包）")
    parser.add_argument("--dry-run", action="store_true", help="仅打印步骤，不执行")
    args = parser.parse_args()

    dry_run: bool = args.dry_run
    version = f"v{_read_version()}"

    print(f"{'[DRY RUN] ' if dry_run else ''}发布 {version}")
    print("=" * 50)

    # 1. 检查前置条件
    if not dry_run:
        print("\n[1/4] 检查前置条件...")
        _check_prerequisites()
        print("  全部通过")
    else:
        print("\n[1/4] 检查前置条件... (跳过)")

    # 2. 构建历史 config CAS 包
    print("\n[2/4] 构建历史 config CAS 包...")
    _build_history_pack(dry_run)

    # 3. 创建 tag
    print(f"\n[3/4] 创建标签 {version}...")
    _create_tag(version, dry_run)

    # 4. 推送
    print("\n[4/4] 推送到 GitHub 和 Gitee...")
    _git_push(version, dry_run)

    print("\n" + "=" * 50)
    print(f"{'[DRY RUN] ' if dry_run else ''}推送完成！CI 将自动构建并创建 Release。")
    print(f"  GitHub Actions: https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/actions")
    print(f"  Release: https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tag/{version}")


if __name__ == "__main__":
    main()
