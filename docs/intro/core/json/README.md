# JSON 处理层 (json)

`src/core/json/` 负责游戏 JSON 文件的解析、缓存和分类。游戏本体的 JSON 不是标准 JSON，包含 `//` 注释、尾随逗号、缺失逗号、同名重复键等非标准格式，本层统一处理这些问题。

## 文件列表

| 文件          | 职责                                        |
| ------------- | ------------------------------------------- |
| `parser.py`   | JSON 文本清洗（去注释、修逗号）、格式化输出  |
| `store.py`    | 全局 JSON 资源管理器单例（批量加载 + 缓存）  |
| `classify.py` | JSON 文件三分类 + 类型字符串工具             |

## 依赖关系

```
infra/types.py  ----+
infra/profiler.py --+--> parser.py --+
accel/_fast_json ---+               |
                                    +--> store.py
infra/diagnostics.py ---------------+
infra/types.py -----+--> classify.py
```

---

## parser.py — JSON 文本清洗与格式化

### 文本清洗函数

清洗流水线按固定顺序执行，`clean_json_text()` 封装了完整流程：

| 函数                       | 说明                                               |
| -------------------------- | -------------------------------------------------- |
| `strip_js_comments(text)`  | 剥离 `//` 注释，保留字符串内的 `//`                 |
| `strip_trailing_commas(text)` | 去除 `}` 或 `]` 前的尾随逗号                     |
| `fix_missing_commas(text)` | 修复相邻键值对之间缺失的逗号                        |
| `strip_duplicate_commas(text)` | 压缩连续逗号（如 `,,`）为单个逗号              |
| `clean_json_text(text)`    | 统一清洗入口：注释 -> 尾逗号 -> 缺逗号 -> 连续逗号 |

以上核心函数通过 `src/accel/_fast_json` C 扩展加速，纯 Python 回退已移除。

### DupList 处理

游戏 JSON 中同一对象内可出现同名键（如两个 `"type"` 键）。解析时使用 `_pairs_hook`（来自 C 扩展），将同名键的多个值收集为 `DupList`。

### 格式化与输出

| 函数                      | 说明                                                  |
| ------------------------- | ----------------------------------------------------- |
| `format_json(data)`       | 格式化 JSON 文本（diff 面板用），保留重复键，key 排序  |
| `dump_json(data, path)`   | 写入 JSON 文件，保留重复键，自动创建父目录              |
| `_serialize(obj, ...)`    | 自定义序列化器，DupList 值展开为重复键                  |
| `reset_dir_cache()`       | 清空目录创建缓存（删除输出目录后调用）                  |

无 DupList 时自动使用标准 `json.dumps`（性能更优），有 DupList 时走自定义 `_serialize`。

---

## store.py — 全局 JSON 资源管理器单例

### JsonStore 概述

全局单例，启动时扫描本体 + 所有 Mod 的 config 目录，批量加载所有 JSON 文件。其他模块通过 `get_base()` / `get_mod()` 获取只读数据，无需关心文件读取和错误处理。

```python
store = JsonStore.instance()
store.init(game_config_path, mod_configs)
```

### 初始化与加载

| 方法                      | 说明                                                    |
| ------------------------- | ------------------------------------------------------- |
| `init(game_config_path, mod_configs)` | 批量加载本体 + 所有 Mod JSON，多线程并行      |
| `parse_file(file_path)`   | 静态方法，无缓存解析单个文件（供初始化前的模块使用）      |

加载过程中的 JSON 解析失败记录到 `_failures` 列表 + `diag.error`，不中断其他文件。

### 分级解析策略

`_parse_progressive` 按需逐步清洗，成功即停：

1. 直接解析原始文本（仅无 `//` 时尝试）
2. 仅去 `//` 注释
3. 去注释 + 去尾随逗号
4. 完整清洗（注释 + 尾逗号 + 缺逗号 + 连续逗号）

### 缓存机制

文件级缓存，key 为 `(路径, mtime)` 元组。文件修改时间未变则命中缓存，文件更新后自动失效。

### 数据访问

| 方法                        | 说明                                        |
| --------------------------- | ------------------------------------------- |
| `get_base(rel_path)`        | 获取本体文件数据（不存在返回 `{}`）          |
| `get_mod(mod_id, rel_path)` | 获取 Mod 文件数据（不存在则 KeyError）        |
| `has_base(rel_path)`        | 本体是否有该文件                             |
| `has_mod(mod_id, rel_path)` | Mod 是否有该文件                             |
| `mod_files(mod_id)`         | 获取 Mod 的所有已加载文件列表                |
| `all_rel_paths()`           | 所有出现过的 rel_path（base + Mod 并集）     |
| `mods_for_file(rel_path)`   | 哪些 Mod 修改了该文件                        |

### Override 管理

Store 同时管理用户手动覆写的 delta 文件：

| 方法                             | 说明                                            |
| -------------------------------- | ----------------------------------------------- |
| `load_overrides(dir, mod_ids)`   | 扫描并加载所有启用 Mod 的 override delta         |
| `get_override(mod_id, rel_path)` | 获取 override delta（不存在返回 None）            |
| `set_override(mod_id, rel_path, delta)` | 保存 override（内存 + 磁盘 + 清合并缓存）|
| `remove_override(mod_id, rel_path)` | 删除 override                                |

### 错误管理

| 方法                   | 说明                                    |
| ---------------------- | --------------------------------------- |
| `take_failures()`      | 取出并清空所有 ParseFailure              |
| `set_ignored_failures()` | 保存用户忽略的解析失败（合并时整文件复制）|
| `reload(paths)`        | 用户修复文件后重新加载，返回仍失败的列表  |

---

## classify.py — JSON 文件分类

### classify_json(data)

将 JSON 文件内容分为三类，决定后续合并方式：

| 分类         | 判定条件                               | 典型文件               |
| ------------ | -------------------------------------- | ---------------------- |
| `dictionary` | 顶层 key 是 ID，value 是含 `id` 的 dict | `cards.json`           |
| `entity`     | 顶层 dict 含 `id` 字段                 | `rite/*.json`          |
| `config`     | 以上都不满足                            | `sfx_config.json` 等   |

### get_type_str(value)

获取 Python 值的类型字符串（`null` / `bool` / `int` / `float` / `string` / `array` / `object`）。DupList 返回首个元素的类型（语义上每个元素等价于原始字段值）。
