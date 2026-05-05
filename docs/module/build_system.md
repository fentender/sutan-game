# 构建系统模块

## 概述

C++/Python 混合构建系统，基于 **scikit-build-core + CMake + nanobind** 工具链。负责编译 C++ 加速层 `sultan_core` 和现有 C 扩展 `_fast_json`，并通过 `pip install` 统一安装。

## 产出模块

| 模块 | 语言 | import 路径 | 说明 |
|------|------|-------------|------|
| `sultan_core` | C++（nanobind） | `import sultan_core` | C++ 加速层入口，后续阶段逐步添加子模块 |
| `_fast_json` | C（Python C API） | `from src.accel import _fast_json` | 已有的 JSON 文本清洗加速（阶段 4 将被 sultan_core 替代） |

## 文件结构

```
project-root/
├── CMakeLists.txt              # 顶层 CMake：Python 查找、nanobind、yyjson、构建目标
├── pyproject.toml              # scikit-build-core 构建后端 + 项目元数据
├── .gitmodules                 # yyjson submodule 声明
├── extern/
│   └── yyjson/                 # git submodule，锁定 0.12.0
├── csrc/
│   ├── CMakeLists.txt          # nanobind_add_module(sultan_core)
│   └── bindings.cpp            # NB_MODULE 骨架（当前仅 __version__）
└── .github/workflows/
    └── build.yml               # CI：Windows + Python 3.12/3.13 矩阵
```

## CMake 构建目标

顶层 `CMakeLists.txt` 定义以下构建链：

```
find_package(Python)
find_package(nanobind)
    │
    ├─ add_subdirectory(extern/yyjson)     → yyjson 静态库（当前未链接，后续阶段使用）
    │
    ├─ add_subdirectory(csrc)              → sultan_core.pyd（nanobind 模块）
    │     └─ NB_STATIC 静态链接 nanobind runtime
    │
    └─ python_add_library(_fast_json)      → _fast_json.pyd（纯 C 扩展）
```

MSVC 编译时启用 `/utf-8`，确保 C++ 源码中的中文字符串正确编码。

## CI

`.github/workflows/build.yml`：

- **触发条件**：push 到 master 且修改了 `csrc/`、`extern/`、`CMakeLists.txt`、`pyproject.toml`、`_fast_json.c`
- **矩阵**：Windows + Python 3.12 / 3.13
- **步骤**：checkout（含 submodule）→ pip install → 验证两个模块 import

## 扩展指南

后续阶段在 `csrc/` 下添加新模块时：

1. 在 `csrc/` 下创建 `.cpp` 源文件
2. 在 `csrc/CMakeLists.txt` 的 `nanobind_add_module` 中添加源文件
3. 需要链接 yyjson 时，在 `csrc/CMakeLists.txt` 中添加：
   ```cmake
   target_link_libraries(sultan_core PRIVATE yyjson)
   ```
4. `bindings.cpp` 中通过 `m.def_submodule("xxx")` 注册子模块