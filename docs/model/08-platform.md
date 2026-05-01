# 08 — 平台集成

独立辅助层，为其他模块提供 Steam 本地数据读取、历史版本管理和在线更新检查能力。仅依赖 [01 数据类型](01-data-types.md)，不依赖其他 core 模块。

## 涉及文件

| 文件 | 职责 |
|------|------|
| `src/core/platform/steam.py` | Steam ACF/VDF 解析、游戏和 Mod 更新时间读取 |
| `src/core/platform/history.py` | 历史版本目录管理、CAS 布局路径解析 |
| `src/core/platform/updater.py` | 在线更新检查（GitHub / Gitee Releases API） |

## 模块依赖

```
steam.py    (无 core 依赖)
history.py  (无 core 依赖)
updater.py  <-- 07 诊断(diagnostics) + config.py(APP_VERSION)
```

---

## Steam 本地数据读取（steam.py）

### VDF/ACF 解析

`_parse_vdf(text)` 是简易的递归解析器，将 Valve 的 VDF/ACF 文本格式解析为嵌套字典。支持引号字符串键值和花括号嵌套结构。

### 路径工具

| 函数 | 说明 |
|------|------|
| `get_steamapps_from_workshop(workshop_path)` | 从 Workshop 路径反推 steamapps 路径 |

Workshop 路径结构为 `.../steamapps/workshop/content/3117820`，反推三级即得 `steamapps`。

### 时间戳读取

| 函数 | 说明 |
|------|------|
| `get_game_update_time(steamapps, app_id)` | 读取 `appmanifest_*.acf` 的 `LastUpdated` |
| `get_mod_update_times(steamapps, app_id)` | 读取 `appworkshop_*.acf`，返回 `{mod_id: timeupdated}` |

两者均解析 ACF 文件获取 Unix 时间戳。失败时分别返回 None 和空字典，不抛异常。

被 [06 Mod 管理](06-mod-management.md) 的 `scan_all_mods` 调用，注入 Mod 更新时间。

### 版本常量

| 常量 | 说明 |
|------|------|
| `MAJOR_UPDATE_TS` | 最近一次大版本更新日期对应的 UTC Unix 时间戳 |

`utc_timestamp(date_str)` 工具函数将 `YYYY-MM-DD` 格式转为 UTC 零点 Unix 时间戳。

---

## 历史版本管理（history.py）

### parse_history_versions(history_dir) -> list[tuple[int, Path]]

扫描历史版本目录，返回 `[(unix_ts, path)]` 按时间升序排列。

兼容两种目录布局：

| 布局 | 目录结构 |
|------|---------|
| 原始布局 | `history_dir/config_YYYY.MM.DD/` |
| CAS 布局 | `history_dir/versions/config_YYYY.MM.DD/` |

优先尝试 CAS 布局（检查 `versions/` 子目录是否存在）。

### find_base_version(mod_update_time, versions) -> Path | None

找到**早于等于** `mod_update_time` 的最近一个历史版本目录。用于 [04 差异计算](04-diff-engine.md) 的 ADAPTIVE 模式确定 Mod 对应的游戏版本基准。

无匹配时返回 None（Mod 更新时间早于所有历史版本）。

### PathResolver — CAS 路径解析器

内容寻址存储（CAS）布局将文件去重为 blob，通过 manifest 映射 rel_path -> hash：

```
history_config_pack/
  versions/
    config_YYYY.MM.DD/
      .manifest.json          # {rel_path: hash}
  blobs/
    ab/
      cdef1234.json           # hash 前 2 位为子目录
```

#### PathResolver 类

| 方法 | 说明 |
|------|------|
| `__init__(history_dir)` | 检测是否为 CAS 布局（blobs/ + versions/ 存在） |
| `resolve(base_dir, rel_path)` | 返回文件真实路径；非 CAS 时原样拼接 |

manifest 按版本名缓存，避免重复加载。

### 全局解析器

| 函数 | 说明 |
|------|------|
| `set_resolver(r)` | 设置全局 PathResolver（app 启动时调用） |
| `resolve_path(base_dir, rel_path)` | 统一路径解析入口（delta 计算时调用） |

不初始化 resolver 时走原路径拼接，兼容非 CAS 环境。

---

## 在线更新检查（updater.py）

### check_for_update(timeout=8) -> dict | None

检查是否有新版本可用，返回更新信息字典或 None。

依次尝试配置的更新源（GitHub 优先，Gitee 备用），第一个成功响应即决定结果。全部失败返回 None。

### 更新源配置

更新源定义在 `config.py` 的 `UPDATE_SOURCES` 列表中：

| 源 | API 地址 |
|----|---------|
| GitHub | `api.github.com/repos/.../releases/latest` |
| Gitee | `gitee.com/api/v5/repos/.../releases/latest` |

### 版本比较

`_parse_version(v)` 将版本字符串解析为整数元组（如 `v1.3.4` -> `(1, 3, 4)`），使用元组比较判断新旧。

### 返回结构

有新版本时返回：

| 字段 | 说明 |
|------|------|
| `tag_name` | 版本号标签 |
| `name` | 发布名称 |
| `body` | 发布说明 |
| `download_url` | 下载页面 URL |
