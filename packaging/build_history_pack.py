"""把 history_config/ 转换为 CAS 格式的 history_config_pack/

按 SHA-256 内容寻址去重。打包前运行，可显著减小 PyInstaller 产物体积。
"""
import hashlib
import json
import shutil
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("cp"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "history_config"
DST = ROOT / "history_config_pack"


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build(clean: bool = True) -> None:
    """构建 CAS 格式的 history_config_pack。

    参数:
        clean: 是否先清空目标目录（默认 True，保证干净构建）
    """
    if not SRC.exists():
        raise RuntimeError(f"源目录不存在: {SRC}")

    if clean and DST.exists():
        shutil.rmtree(DST)

    blobs_dir = DST / "blobs"
    versions_dir = DST / "versions"
    blobs_dir.mkdir(parents=True, exist_ok=True)
    versions_dir.mkdir(parents=True, exist_ok=True)

    total_files = 0
    unique_blobs = 0
    total_bytes = 0
    blob_bytes = 0

    for ver_dir in sorted(SRC.iterdir()):
        if not ver_dir.is_dir() or not ver_dir.name.startswith("config_"):
            continue

        manifest: dict[str, str] = {}
        ver_out = versions_dir / ver_dir.name
        ver_out.mkdir(exist_ok=True)

        for f in ver_dir.rglob("*"):
            if not f.is_file():
                continue
            data = f.read_bytes()
            h = _sha256(data)
            blob_path = blobs_dir / h[:2] / f"{h[2:]}.json"

            if blob_path.exists():
                # 内容校验：防御 SHA-256 冲突。
                # 概率天文级低，但静默损坏后果严重——一旦命中应立即抛错。
                existing = blob_path.read_bytes()
                if existing != data:
                    raise RuntimeError(
                        f"SHA-256 冲突: {h}\n"
                        f"  已存在 blob 与新内容不同\n"
                        f"  当前文件: {f}\n"
                        f"  版本: {ver_dir.name}"
                    )
            else:
                blob_path.parent.mkdir(exist_ok=True)
                blob_path.write_bytes(data)
                unique_blobs += 1
                blob_bytes += len(data)

            rel = f.relative_to(ver_dir).as_posix()
            manifest[rel] = h
            total_files += 1
            total_bytes += len(data)

        (ver_out / ".manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    print(f"[CAS] 源文件 {total_files} 个 / {total_bytes / 1024 / 1024:.2f} MB")
    print(f"[CAS] 独立 blob {unique_blobs} 个 / {blob_bytes / 1024 / 1024:.2f} MB")
    if total_bytes:
        ratio = blob_bytes / total_bytes * 100
        print(f"[CAS] 去重后占比 {ratio:.1f}%")


if __name__ == "__main__":
    build()
