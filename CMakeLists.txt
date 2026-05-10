cmake_minimum_required(VERSION 3.17)
project(sultan_core LANGUAGES C CXX)

# Python
find_package(Python 3.10
    REQUIRED COMPONENTS Interpreter Development.Module)

# nanobind（通过 pip 安装，scikit-build-core 设置 prefix path）
find_package(nanobind CONFIG REQUIRED)

# MSVC: 源码和执行字符集均为 UTF-8
if(MSVC)
    add_compile_options(/utf-8)
endif()

# yyjson（git submodule）
set(YYJSON_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(YYJSON_BUILD_MISC OFF CACHE BOOL "" FORCE)
add_subdirectory(extern/yyjson EXCLUDE_FROM_ALL)

# rapidfuzz-cpp（header-only）
add_subdirectory(extern/rapidfuzz-cpp EXCLUDE_FROM_ALL)

# C++ 加速层
add_subdirectory(csrc)

# C++ 单元测试（Catch2）
option(BUILD_TESTS "Build C++ unit tests" OFF)
if(BUILD_TESTS)
    enable_testing()
    add_subdirectory(extern/Catch2)
    add_subdirectory(tests/cpp)
endif()

# 现有 _fast_json C 扩展
python_add_library(_fast_json MODULE WITH_SOABI
    src/accel/_fast_json.c
)
install(TARGETS _fast_json LIBRARY DESTINATION src/accel)
