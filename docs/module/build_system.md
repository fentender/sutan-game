# 构建系统模块

## 概述

C++/Python 混合构建系统，基于 **scikit-build-core + CMake + nanobind** 工具链。负责编译 C++ 加速层 `sultan_core` 和现有 C 扩展 `_fast_json`，并通过 `pip install` 统一安装。

## 产出模块

| 模块 | 语言 | import 路径 | 说明 |
|------|------|-------------|------|
| `sultan_core` | C++（nanobind） | `import sultan_core` | C++ 加速层入口，含 `diag` 等子模块 |
| `_fast_json` | C（Python C API） | `from src.accel import _fast_json` | 已有的 JSON 文本清洗加速（阶段 4 将被 sultan_core 替代） |

## 文件结构

```
project-root/
├── CMakeLists.txt              # 顶层 CMake：Python 查找、nanobind、yyjson、构建目标、测试开关
├── pyproject.toml              # scikit-build-core 构建后端 + 项目元数据
├── .gitmodules                 # yyjson + Catch2 submodule 声明
├── extern/
│   ├── yyjson/                 # git submodule，锁定 0.12.0
│   └── Catch2/                 # git submodule，锁定 v3.8.0（C++ 测试框架）
├── csrc/
│   ├── CMakeLists.txt          # sultan_core_lib 静态库 + sultan_core nanobind 模块
│   ├── bindings.cpp            # NB_MODULE 入口 + 子模块绑定注册
│   ├── diag.h                  # DiagManager 头文件
│   ├── diag.cpp                # DiagManager 实现
│   ├── resource_loader.h       # ResourceLoader 头文件
│   ├── resource_loader.cpp     # ResourceLoader 实现
│   ├── json/                   # Json 模块
│   │   ├── json_cleaner.h      # 文本清洗函数声明
│   │   ├── json_cleaner.cpp    # fix_missing_commas / strip_duplicate_commas / clean_text
│   │   ├── json_doc.h          # JsonDoc 类声明（yyjson RAII 封装）
│   │   ├── json_doc.cpp        # 解析 / 序列化实现
│   │   ├── json_val.h          # JsonVal / JsonType（不可变值引用）
│   │   ├── json_val.cpp        # JsonVal 实现（yyjson 读取 API 封装）
│   │   ├── mut_val.h           # MutVal（可变值引用）
│   │   ├── mut_val.cpp         # MutVal 实现（yyjson 可变值 API 封装）
│   │   ├── mut_doc.h           # MutDoc（可变文档所有者）
│   │   └── mut_doc.cpp         # MutDoc 实现（可变文档生命周期管理）
│   ├── field_ops/              # JSON 字段操作模块
│   │   ├── field_ops.h         # 批量提取 / 替换 API 声明
│   │   └── field_ops.cpp       # 提取 / 替换实现
│   ├── state/                  # State 模块
│   │   ├── change_kind.h       # ChangeKind / MergeMode 枚举 + 位运算
│   │   ├── state_node.h        # StateBase 基类 + 三个派生类
│   │   ├── state_node.cpp      # clone / serialize_scalar / is_modified
│   │   ├── json_state.h        # JsonState 类（工厂 + 转换 + 格式化）
│   │   ├── json_state.cpp      # from_doc / to_doc / clone 实现
│   │   ├── state_formatter.h   # FormatResult + format_state 声明
│   │   └── state_formatter.cpp # 递归格式化实现
│   └── delta/                  # Delta 模块
│       ├── similarity.h        # Levenshtein 距离 + ratio
│       ├── similarity.cpp
│       ├── delta_node.h        # DeltaType 枚举 + DeltaBase + 三个派生类
│       ├── delta_node.cpp      # clone + 工厂 + 序列化/反序列化
│       ├── delta_rules.h       # SMART 模式删除判定
│       ├── delta_rules.cpp
│       ├── array_match.h       # ArrayMatching + match_by_heuristic
│       ├── array_match.cpp     # 四阶段数组匹配算法
│       ├── compute_delta.h     # compute_delta
│       ├── compute_delta.cpp   # 递归 delta 计算
│       ├── apply_delta.h       # apply_dict/array/field_delta
│       └── apply_delta.cpp     # delta 应用 + 类型归一化
├── tests/
│   ├── __init__.py
│   ├── __main__.py             # python -m tests 入口
│   ├── python/                 # Python 测试
│   │   ├── test_runner.py      # 自定义测试运行器
│   │   ├── test_diag.py        # 诊断模块 Python 集成测试
│   │   ├── test_core.py        # 核心功能测试
│   │   ├── ...                 # 其他测试模块
│   │   └── fixtures/           # 测试数据
│   └── cpp/                    # C++ 单元测试
│       ├── CMakeLists.txt      # Catch2 测试目标
│       ├── test_diag.cpp       # 诊断模块 C++ 单元测试
│       ├── test_resource_loader.cpp  # 资源加载模块 C++ 单元测试
│       ├── test_json.cpp       # Json 模块 C++ 单元测试
│       ├── test_field_ops.cpp  # 字段操作模块 C++ 单元测试
│       ├── test_state.cpp     # State 模块 C++ 单元测试
│       ├── test_mut_val.cpp   # MutVal/MutDoc 单元测试
│       └── test_delta.cpp    # Delta 模块 C++ 单元测试
└── .github/workflows/
    └── build.yml               # CI：构建 + C++ 测试 + Python 测试
```

## 构建流程

### 开发模式

```bash
git submodule update --init --recursive   # 拉取 yyjson + Catch2
pip install -e . -v                       # editable 安装，自动触发 CMake 构建
```

editable 模式下，修改 C++ 源码后再次 import 会自动重新编译（由 `[tool.scikit-build.editable] rebuild = true` 控制）。

### C++ 单元测试

```bash
# 获取 nanobind cmake 路径
NB_DIR=$(python -c "import nanobind; print(nanobind.cmake_dir())")

# 配置 + 构建 + 运行
cmake -B build_test -DBUILD_TESTS=ON -Dnanobind_DIR="$NB_DIR"
cmake --build build_test --target sultan_tests --config Release
ctest --test-dir build_test -C Release --output-on-failure
```

`BUILD_TESTS=OFF`（默认）时不编译 Catch2 和测试，不影响正常构建速度。

## CMake 构建目标

顶层 `CMakeLists.txt` 定义以下构建链：

```
find_package(Python)
find_package(nanobind)
    │
    ├─ add_subdirectory(extern/yyjson)     → yyjson 静态库（sultan_core_lib 链接）
    │
    ├─ add_subdirectory(csrc)
    │     ├─ sultan_core_lib（静态库）     → C++ 业务逻辑（diag / resource_loader / json 等模块），链接 yyjson
    │     └─ sultan_core（nanobind 模块）  → 链接 sultan_core_lib，暴露 Python API
    │
    ├─ if(BUILD_TESTS)
    │     ├─ add_subdirectory(extern/Catch2)  → Catch2 测试框架
    │     └─ add_subdirectory(tests/cpp)      → sultan_tests 可执行文件
    │           └─ 链接 sultan_core_lib + Catch2WithMain
    │
    └─ python_add_library(_fast_json)      → _fast_json.pyd（纯 C 扩展）
```

`sultan_core_lib` 静态库使 C++ 业务逻辑可被 nanobind 模块和 C++ 测试共享，无需重复编译。

MSVC 编译时启用 `/utf-8`，确保 C++ 源码中的中文字符串正确编码。

## 测试

### C++ 单元测试

- 框架：Catch2 v3（git submodule `extern/Catch2`，锁定 v3.8.0）
- 位置：`tests/cpp/`
- 构建：`cmake -DBUILD_TESTS=ON` 开启
- 运行：`ctest` 或直接执行 `sultan_tests.exe`
- 测试发现：`catch_discover_tests()` 自动注册到 CTest

### Python 测试

- 框架：自定义测试运行器（`tests/python/test_runner.py`）
- 位置：`tests/python/`
- 运行：`python -m tests`（`--func` 仅功能 / `--perf` 仅性能）

## CI

`.github/workflows/build.yml`：

- **触发条件**：push 到 master 且修改了 `csrc/`、`extern/`、`CMakeLists.txt`、`pyproject.toml`、`_fast_json.c`、`tests/`
- **矩阵**：Windows + Python 3.12 / 3.13
- **步骤**：checkout（含 submodule）→ pip install → 验证模块 import → C++ 单元测试 → Python 功能测试

## 扩展指南

后续阶段在 `csrc/` 下添加新模块时：

1. 在 `csrc/` 下创建 `.h` / `.cpp` 源文件
2. 在 `csrc/CMakeLists.txt` 的 `sultan_core_lib` 中添加 `.cpp` 文件
3. 需要链接 yyjson 时，在 `csrc/CMakeLists.txt` 中添加：
   ```cmake
   target_link_libraries(sultan_core_lib PRIVATE yyjson)
   ```
4. `bindings.cpp` 中通过 `m.def_submodule("xxx")` 注册子模块
5. 在 `tests/cpp/` 下创建对应的 C++ 测试文件，添加到 `tests/cpp/CMakeLists.txt`
6. 在 `tests/python/` 下创建 Python 集成测试，注册到 `test_runner.py`
