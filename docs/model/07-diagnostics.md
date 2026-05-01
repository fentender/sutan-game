# 07 — 诊断与性能

横切关注点模块，提供全局诊断信息收集和可选性能监测能力。替代 Python `logging` 模块，禁止在项目中使用 `logging`。

## 涉及文件

| 文件 | 职责 |
|------|------|
| `src/core/infra/diagnostics.py` | 线程安全的全局诊断信息收集器 + MergeContext |
| `src/core/infra/profiler.py` | 可选的性能评估模块（装饰器 + 上下文管理器） |

## 模块依赖

零依赖。被所有其他模块引用。

---

## Diagnostics 单例（diagnostics.py）

替代 Python `logging` 模块，线程安全地收集所有诊断消息。

```python
diag = Diagnostics()  # 模块级单例
```

| 方法 | 说明 |
|------|------|
| `diag.info(category, msg)` | 追加信息级别消息 |
| `diag.warn(category, msg)` | 追加警告级别消息 |
| `diag.error(category, msg)` | 追加错误级别消息 |
| `diag.snapshot(*categories)` | 返回指定类别的消息并清空（不传参则返回全部） |

### 四个标准类别

各类别由不同模块写入，[GUI LogPanel](09-gui.md) 统一读取展示：

| 类别 | 来源模块 | 内容 |
|------|---------|------|
| `parse` | [02 JSON 管线](02-json-pipeline.md) `store.py` | BOM 修正、编码异常 |
| `scan` | [06 Mod 管理](06-mod-management.md) `scanner.py` | Mod 扫描时的 Info.json 解析失败 |
| `merge` | [05 合并引擎](05-merge-engine.md) `merger.py` 等 | 类型不匹配、未知字段、整文件替换警告 |
| `schema` | [03 Schema](03-schema-system.md) `loader.py` 等 | schema 加载、生成过程中的信息与警告 |

---

## MergeContext 线程局部上下文（diagnostics.py）

线程本地变量，由 [合并引擎](05-merge-engine.md) 设置，供 `apply_delta` 内部的警告生成读取当前 Mod 信息。

```python
merge_ctx = MergeContext()  # 模块级线程局部实例
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `mod_name` | `str` | 当前正在处理的 Mod 名称 |
| `mod_id` | `str` | 当前 Mod ID |
| `rel_path` | `str` | 当前文件相对路径 |
| `source_file` | `str` | Mod 源文件绝对路径 |

### 设计动机

`apply_delta()` 是一个递归函数，调用层次很深。当合并过程中发现类型不匹配或未知字段等问题时，需要输出包含"哪个 Mod、哪个文件"的警告消息。但 `apply_delta` 的签名不包含 mod 信息——把这些参数逐层传递会污染每一级递归调用的签名。

`MergeContext` 继承 `threading.local`，提供线程本地的隐式上下文，避免侵入函数签名。调用者在进入合并循环前设置上下文，`apply_delta` 内部的警告生成函数直接读取。

### 用法示例

**写入端** — 在 `merge_file()` 的 mod 循环中设置上下文（`merge/merger.py`）：

```python
from src.core.infra import merge_ctx

for step, (mod_id, mod_name, delta, source_file) in enumerate(mod_data_list, 1):
    merge_ctx.mod_name = mod_name
    merge_ctx.mod_id = mod_id
    merge_ctx.rel_path = rel_path
    merge_ctx.source_file = source_file

    apply_delta(current, delta, schema, fp, version=step)
```

**读取端** — `_build_warn_msg()` 从上下文拼接警告前缀（`merge/merger.py`）：

```python
from src.core.infra import merge_ctx

def _build_warn_msg(field_path: list[str] | None, msg: str) -> str:
    parts: list[str] = []
    if merge_ctx.mod_name:
        parts.append(f"[{merge_ctx.mod_name}]")
    if merge_ctx.source_file:
        parts.append(merge_ctx.source_file)
    elif merge_ctx.rel_path:
        parts.append(merge_ctx.rel_path)
    if field_path:
        parts.append(".".join(field_path))
    prefix = " > ".join(parts)
    return f"{prefix}: {msg}" if prefix else msg
```

**输出效果**：`[超级武器Mod] > D:/mods/12345/config/cards.json > attack.damage: 类型不匹配，期望 int 实际为 str`

### 生命周期

1. **初始化** — 模块加载时创建全局单例 `merge_ctx`，所有字段为空字符串
2. **设置** — `merge_file()` 或 `MergeCache._compute_file()` 在每个 mod 循环迭代前写入四个字段
3. **读取** — `apply_delta()` 递归深处若触发警告，`_build_warn_msg()` 读取当前上下文生成带定位信息的消息
4. **覆盖** — 下一个 mod 迭代时字段被覆写；`threading.local` 保证各线程互不干扰

### 使用位置

| 文件 | 操作 | 说明 |
|------|------|------|
| `merge/merger.py` `merge_file()` | 写入 | 合并主流程中逐 mod 设置上下文 |
| `merge/cache.py` `_compute_file()` | 写入 | 缓存重算流程中逐 mod 设置上下文 |
| `merge/merger.py` `_build_warn_msg()` | 读取 | 拼接带 mod 名称和文件路径的警告消息 |

---

## 性能评估（profiler.py）

全局开关控制，默认关闭。关闭时 `@profile` 和 `profile_block` 零开销（直接透传）。

### 接口

| 接口 | 说明 |
|------|------|
| `enable()` / `disable()` | 全局开关 |
| `@profile` | 装饰器，记录函数执行时间 |
| `profile_block(name)` | 上下文管理器，记录代码块执行时间 |
| `get_report(top_n=20)` | 生成性能报告（按总耗时降序排列） |
| `reset()` | 清空所有统计数据 |

通过 `user_config.json` 的 `enable_profiler` 字段控制启动时是否自动开启。

### 统计信息

每个被监控的函数/代码块记录：调用次数、总耗时、最小耗时、最大耗时。
