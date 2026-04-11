"""编译 C 加速模块

用法: python setup.py build_ext --inplace

编译 _fast_json C 扩展（JSON 文本清洗和辅助函数的 C 加速版本）。
需要 C 编译器（Windows: Visual Studio Build Tools, Linux/Mac: gcc/clang）。
"""
from setuptools import setup, Extension

setup(
    name="sudan-game-extensions",
    ext_modules=[
        Extension(
            "src.accel._fast_json",
            sources=["src/accel/_fast_json.c"],
        ),
    ],
)
