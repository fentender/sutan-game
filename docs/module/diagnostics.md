# 诊断模块

## 概述

C++ 层诊断信息管理器，供所有 C++ 模块使用。提供 buffer 存储和回调通知两种消息传递方式。Python 层只拉取信息，不产生信息。

## 类型体系

| 类型 | 说明 |
|------|------|
| `DiagLevel` | 枚举：Info / Warn / Error |
| `DiagMessage` | 结构：level + category + message |
| `DiagManager` | 诊断管理器：emit / snapshot / 回调管理 |
| `diag_manager()` | 全局单例（Meyer's singleton，线程安全） |

## 消息传递机制

### buffer 模式（默认）

C++ 模块调用 `emit(level, category, msg)` → 消息进入 buffer → Python 通过 `snapshot()` 批量拉取并清空。

### 回调模式（实时通知）

C++ 模块调用 `emit(level, category, msg, notify=true)` → 消息通过回调直接传给 Python，**不进入 buffer**。

若 `notify=true` 但未注册回调，消息回退进 buffer。

```
emit(notify=false)                → buffer（无论是否有回调）
emit(notify=true) + 有回调        → 回调接收，不进 buffer
emit(notify=true) + 无回调        → 回退进 buffer
```

## 文件结构

```
csrc/
├── diag.h              # DiagLevel / DiagMessage / DiagManager 声明
└── diag.cpp            # 实现
tests/
├── cpp/test_diag.cpp   # C++ 单元测试（8 case）
└── python/test_diag.py # Python 集成测试（9 case）
```

## C++ API

```cpp
#include "diag.h"
using namespace sultan;

auto& mgr = diag_manager();

// 产生诊断信息（默认进 buffer）
mgr.info("parse", "跳过 BOM");
mgr.warn("merge", "数组大小不匹配");
mgr.error("load", "文件不存在");

// 关键错误实时通知 Python（notify=true）
mgr.error("load", "文件不存在", true);
```

## Python API（sultan_core.diag）

Python 只读，不暴露 emit/info/warn/error：

```python
from sultan_core import diag

mgr = diag.get_manager()

# 拉取 buffer 中的消息（拉取后清空）
msgs = mgr.snapshot()             # 全部
msgs = mgr.snapshot(["parse"])    # 按类别

# 注册回调（接收 notify=true 的实时消息）
def on_diag(level: diag.Level, category: str, msg: str) -> None:
    print(f"[{level}] [{category}] {msg}")

mgr.set_callback(on_diag)
mgr.clear_callback()

# Message 属性
for m in msgs:
    m.level     # diag.Level.INFO / WARN / ERROR
    m.category  # str
    m.message   # str
```

## 线程安全

- DiagManager 内部通过 `std::mutex` 保护所有操作
- 多线程并发 emit 安全
- 回调在锁内调用（回调应轻量，避免长时间持锁）
- nanobind 自动处理 GIL（C++ 调用 Python callable 时获取 GIL）

## 与 Python 层诊断的关系

Python 层 `diag` 单例（`src/core/infra/diagnostics.py`）继续负责 Python 侧诊断。C++ 诊断模块仅供 C++ 层内部使用。两者独立，互不干扰。
