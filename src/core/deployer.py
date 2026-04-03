"""
部署模块 - 生成合成 Mod 并部署到 workshop 目录
"""
import shutil
import json
from datetime import datetime
from pathlib import Path

from ..config import MERGED_OUTPUT_PATH, SYNTHETIC_MOD_ID


def generate_info_json(mod_names: list[str], output_path: Path):
    """生成合成 Mod 的 Info.json"""
    info = {
        "name": "合并Mod - 自动生成",
        "description": f"由 Mod 合并管理器自动生成。\n包含以下 mod 的合并内容：\n" +
                       "\n".join(f"  - {name}" for name in mod_names),
        "tags": ["Merged"],
        "version": datetime.now().strftime("%Y%m%d.%H%M%S")
    }
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "Info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=4),
        encoding='utf-8'
    )


def copy_resources(
    mod_paths: list[tuple[str, Path]],
    output_path: Path
):
    """
    复制非 JSON 资源文件（图片等），按优先级顺序覆盖。

    参数:
        mod_paths: [(mod_name, mod_root_path), ...] 按优先级排序
        output_path: 输出目录
    """
    for _, mod_path in mod_paths:
        for f in mod_path.rglob("*"):
            if f.is_file() and f.suffix.lower() != '.json' and f.stem.lower() != 'preview':
                rel = f.relative_to(mod_path)
                # 跳过 Info.json
                if str(rel) == "Info.json":
                    continue
                dest = output_path / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)


def deploy_to_workshop(
    merged_output: Path,
    workshop_path: Path,
    mod_names: list[str]
) -> Path:
    """
    将合并结果部署为合成 Mod。

    返回合成 Mod 的路径。
    """
    target = workshop_path / SYNTHETIC_MOD_ID
    # 清理旧的合成 Mod
    if target.exists():
        shutil.rmtree(target)

    # 复制合并输出
    shutil.copytree(merged_output, target)

    # 生成 Info.json
    generate_info_json(mod_names, target)

    return target


def clean_synthetic_mod(workshop_path: Path) -> bool:
    """清理合成 Mod 目录，返回是否成功"""
    target = workshop_path / SYNTHETIC_MOD_ID
    if target.exists():
        shutil.rmtree(target)
        return True
    return False
