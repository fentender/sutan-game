"""
一键发布脚本 —— 推送代码 + 打包 + 创建 GitHub/Gitee Release

用法:
    python release.py                          # 交互式输入 release notes
    python release.py --notes "修复若干问题"    # 命令行指定 release notes
    python release.py --dry-run                # 仅打印步骤，不执行
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener, install_opener, urlopen

# ── 常量 ─────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent
SPEC_FILE = ROOT / "sudan.spec"
DIST_DIR = ROOT / "dist"
BUILD_OUTPUT = DIST_DIR / "SuDanModMerger"

GITHUB_OWNER = "fentender"
GITHUB_REPO = "sutan-game"
GITEE_OWNER = "fentende125"
GITEE_REPO = "sutan-game"

# 代理设置（访问 GitHub/Gitee API 时使用）
PROXY = os.environ.get("HTTPS_PROXY", os.environ.get("HTTP_PROXY", ""))

# gh CLI 完整路径（Windows 安装后可能不在 PATH 中）
GH_CMD = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"


# ── 工具函数 ──────────────────────────────────────────────────────────


def _read_version() -> str:
    """从 src/config.py 读取 APP_VERSION"""
    config_path = ROOT / "src" / "config.py"
    text = config_path.read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    if not match:
        print("错误: 无法从 src/config.py 读取 APP_VERSION")
        sys.exit(1)
    return match.group(1)


def _run(cmd: list[str], *, dry_run: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    """执行命令并打印"""
    print(f"  → {' '.join(cmd)}")
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    env = os.environ.copy()
    if PROXY:
        env.setdefault("HTTPS_PROXY", PROXY)
        env.setdefault("HTTP_PROXY", PROXY)
    return subprocess.run(cmd, check=check, capture_output=True, text=True, cwd=str(ROOT), env=env, encoding="utf-8", errors="replace")


def _check_prerequisites() -> None:
    """检查前置条件"""
    # git 工作区是否干净
    result = subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=str(ROOT)
    )
    if result.stdout.strip():
        print("错误: git 工作区有未提交的更改:")
        print(result.stdout)
        sys.exit(1)

    # gh CLI
    if not os.path.isfile(GH_CMD):
        print("错误: 未找到 gh CLI，请先安装: https://cli.github.com/")
        sys.exit(1)

    result = subprocess.run([GH_CMD, "auth", "status"], capture_output=True, text=True)
    if result.returncode != 0:
        print("错误: gh CLI 未认证，请先执行: gh auth login")
        sys.exit(1)

    # Gitee token
    if not os.environ.get("GITEE_TOKEN"):
        print("错误: 未设置环境变量 GITEE_TOKEN（Gitee 个人访问令牌）")
        sys.exit(1)

    # PyInstaller
    if not shutil.which("pyinstaller"):
        print("错误: 未找到 pyinstaller，请先安装: pip install pyinstaller")
        sys.exit(1)


def _git_push(version: str, dry_run: bool) -> None:
    """推送代码和新标签到 origin 和 gitee"""
    tag = f"v{version}"
    _run(["git", "push", "origin", "master"], dry_run=dry_run)
    _run(["git", "push", "origin", tag], dry_run=dry_run)
    _run(["git", "push", "gitee", "master"], dry_run=dry_run)
    _run(["git", "push", "gitee", tag], dry_run=dry_run)


def _create_tag(version: str, dry_run: bool) -> None:
    """创建 git tag（已存在则跳过）"""
    tag = f"v{version}"
    result = subprocess.run(
        ["git", "tag", "-l", tag], capture_output=True, text=True, cwd=str(ROOT)
    )
    if tag in result.stdout.strip().splitlines():
        print(f"  标签 {tag} 已存在，跳过")
        return
    _run(["git", "tag", tag], dry_run=dry_run)


def _build(dry_run: bool) -> None:
    """编译 C 扩展并 PyInstaller 打包"""
    _run([sys.executable, "setup.py", "build_ext", "--inplace"], dry_run=dry_run)
    _run(["pyinstaller", "sudan.spec", "--noconfirm"], dry_run=dry_run)


def _make_zip(version: str, dry_run: bool) -> Path:
    """将 dist/SuDanModMerger/ 压缩为 zip"""
    zip_name = f"SuDanModMerger-V{version}.zip"
    zip_path = DIST_DIR / zip_name
    print(f"  → 压缩 {BUILD_OUTPUT} → {zip_path}")
    if dry_run:
        return zip_path

    if not BUILD_OUTPUT.is_dir():
        print(f"错误: 打包输出目录不存在: {BUILD_OUTPUT}")
        sys.exit(1)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in BUILD_OUTPUT.rglob("*"):
            if file.is_file():
                arcname = f"SuDanModMerger/{file.relative_to(BUILD_OUTPUT)}"
                zf.write(file, arcname)

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"  压缩完成: {size_mb:.1f} MB")
    return zip_path


def _github_release(version: str, notes: str, zip_path: Path, dry_run: bool) -> None:
    """通过 gh CLI 创建 GitHub Release"""
    tag = f"v{version}"
    title = f"V{version}"

    if not dry_run:
        # 检查 release 是否已存在
        env = os.environ.copy()
        if PROXY:
            env.setdefault("HTTPS_PROXY", PROXY)
            env.setdefault("HTTP_PROXY", PROXY)
        result = subprocess.run(
            [GH_CMD, "release", "view", tag], capture_output=True, text=True, cwd=str(ROOT), env=env
        )
        if result.returncode == 0:
            print(f"  GitHub Release {tag} 已存在，跳过")
            return

    _run(
        [
            GH_CMD, "release", "create", tag,
            str(zip_path),
            "--title", title,
            "--notes", notes,
        ],
        dry_run=dry_run,
    )
    print("  GitHub Release 创建完成")


def _gitee_release(version: str, notes: str, zip_path: Path, dry_run: bool) -> None:
    """通过 Gitee REST API 创建 Release 并上传附件"""
    token = os.environ.get("GITEE_TOKEN", "")
    tag = f"v{version}"
    title = f"V{version}"
    api_base = f"https://gitee.com/api/v5/repos/{GITEE_OWNER}/{GITEE_REPO}"

    print(f"  → 创建 Gitee Release {tag}")
    if dry_run:
        print(f"  → 上传附件 {zip_path.name} 到 Gitee Release")
        return

    # 配置代理
    if PROXY:
        opener = build_opener(ProxyHandler({"https": PROXY, "http": PROXY}))
        install_opener(opener)

    # 创建 release
    create_data = json.dumps({
        "access_token": token,
        "tag_name": tag,
        "name": title,
        "body": notes,
        "target_commitish": "master",
    }).encode("utf-8")

    req = Request(
        f"{api_base}/releases",
        data=create_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req) as resp:
            release_info = json.loads(resp.read())
            release_id = release_info["id"]
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        if "already_exists" in body or e.code == 422:
            print(f"  Gitee Release {tag} 已存在，跳过")
            return
        print(f"错误: Gitee 创建 Release 失败: {e.code} {body}")
        sys.exit(1)

    # 上传附件（multipart/form-data）
    print(f"  → 上传附件 {zip_path.name}")
    boundary = "----ReleaseUploadBoundary"
    file_data = zip_path.read_bytes()
    file_name = zip_path.name

    body_parts = []
    # access_token 字段
    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(b'Content-Disposition: form-data; name="access_token"\r\n\r\n')
    body_parts.append(f"{token}\r\n".encode())
    # file 字段
    body_parts.append(f"--{boundary}\r\n".encode())
    body_parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode()
    )
    body_parts.append(b"Content-Type: application/zip\r\n\r\n")
    body_parts.append(file_data)
    body_parts.append(f"\r\n--{boundary}--\r\n".encode())

    upload_body = b"".join(body_parts)

    req = Request(
        f"{api_base}/releases/{release_id}/attach_files",
        data=upload_body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    try:
        with urlopen(req) as resp:
            resp.read()
    except HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        print(f"警告: Gitee 上传附件失败: {e.code} {body_text}")
        print("  Release 已创建但附件上传失败，请手动上传")
        return

    print("  Gitee Release 创建完成")


# ── 主流程 ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="一键发布脚本")
    parser.add_argument("--dry-run", action="store_true", help="仅打印步骤，不执行")
    parser.add_argument("--notes", type=str, default="", help="Release Notes 内容")
    args = parser.parse_args()

    dry_run: bool = args.dry_run
    version = _read_version()
    tag = f"v{version}"

    print(f"{'[DRY RUN] ' if dry_run else ''}发布 V{version}")
    print("=" * 50)

    # 1. 检查前置条件
    if not dry_run:
        print("\n[1/6] 检查前置条件...")
        _check_prerequisites()
        print("  全部通过")
    else:
        print("\n[1/6] 检查前置条件... (跳过)")

    # 2. 创建 tag
    print(f"\n[2/6] 创建标签 {tag}...")
    _create_tag(version, dry_run)

    # 3. 推送
    print("\n[3/6] 推送到 GitHub 和 Gitee...")
    _git_push(version, dry_run)

    # 4. 打包
    print("\n[4/6] 编译并打包...")
    _build(dry_run)

    # 5. 压缩
    print("\n[5/6] 创建压缩包...")
    zip_path = _make_zip(version, dry_run)

    # 6. 创建 Release
    notes: str = args.notes
    if not notes and not dry_run:
        print("\n请输入 Release Notes（输入空行结束）:")
        lines: list[str] = []
        while True:
            line = input()
            if line == "":
                break
            lines.append(line)
        notes = "\n".join(lines)

    if not notes:
        notes = f"V{version} 发布"

    print("\n[6/6] 创建 Release...")
    _github_release(version, notes, zip_path, dry_run)
    _gitee_release(version, notes, zip_path, dry_run)

    print("\n" + "=" * 50)
    print(f"{'[DRY RUN] ' if dry_run else ''}发布 V{version} 完成！")
    print(f"  GitHub: https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tag/{tag}")
    print(f"  Gitee:  https://gitee.com/{GITEE_OWNER}/{GITEE_REPO}/releases/tag/{tag}")


if __name__ == "__main__":
    main()
