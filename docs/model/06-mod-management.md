# 06 — Mod 管理

位于 core 最上层，编排 Mod 扫描、冲突检测、本体重叠分析、ID 重分配、部署和用户覆盖管理的完整生命周期。

## 涉及文件

| 文件 | 职责 |
|------|------|
| `src/core/mod/scanner.py` | Mod 扫描与元数据读取 |
| `src/core/mod/conflict.py` | 冲突检测与覆盖链报告 |
| `src/core/mod/overlap.py` | 本体重叠检测（判断 Mod 是否修改本体内容） |
| `src/core/mod/deployer.py` | 合成 Mod 生成与部署 |
| `src/core/mod/id_remap.py` | ID 冲突检测与重分配 |
| `src/core/mod/overrides.py` | 用户覆盖目录管理 |

## 模块依赖

```
scanner.py   <-- 08 平台集成(steam.py 更新时间)
conflict.py  <-- 04 差异计算(delta 复用)
overlap.py   <-- 02 JSON 管线(store, classify)
deployer.py  <-- id_remap.py
id_remap.py  <-- 02 JSON 管线(store 数据读写)
overrides.py    (无 core 依赖，纯文件操作)
```

---

## Mod 扫描（scanner.py）

### ModInfo 数据类

单个 Mod 的元数据：

| 字段 | 类型 | 说明 |
|------|------|------|
| `mod_id` | `str` | Mod 目录名（通常是 Steam Workshop ID） |
| `name` | `str` | Mod 名称（来自 Info.json） |
| `description` | `str` | 描述 |
| `tags` | `list[str]` | 标签列表 |
| `version` | `str` | 版本号 |
| `path` | `Path` | Mod 根目录路径 |
| `preview_path` | `str \| None` | 预览图路径 |
| `config_files` | `list[str]` | 配置文件列表（相对于 config/） |
| `resource_files` | `list[str]` | 非配置资源文件列表 |
| `update_time` | `int \| None` | Steam 最后更新时间（Unix 时间戳） |
| `has_base_overlap` | `bool \| None` | 是否修改了本体内容 |

### 扫描函数

| 函数 | 说明 |
|------|------|
| `scan_all_mods(workshop_path, exclude_ids)` | 扫描 workshop 目录下所有 Mod，注入更新时间 |
| `scan_single_mod(mod_path)` | 扫描单个 Mod 目录（读 Info.json + 收集文件列表） |
| `scan_config_files(mod_path)` | 扫描 config 目录，分离配置文件和资源文件 |
| `collect_mod_files(mod_configs)` | 按相对路径聚合所有 Mod 的 JSON 文件 |

---

## 冲突检测（conflict.py）

### 数据结构

#### DeletionRecord

| 字段 | 类型 | 说明 |
|------|------|------|
| `field_path` | `str` | 被删除的字段路径 |
| `base_value` | `object` | 被删除的原始值 |
| `mod_name` | `str` | 执行删除的 Mod 名称 |

#### FieldOverride

| 字段 | 类型 | 说明 |
|------|------|------|
| `field_path` | `str` | 字段路径 |
| `mod_values` | `list[tuple[str, object]]` | 各 Mod 的修改值 `[(mod_name, value)]` |
| `final_value` | `object` | 最终值（最后一个 Mod） |
| `is_conflict` | `bool`（属性） | 多个 Mod 修改了同一字段且值不同 |
| `is_array_touched` | `bool` | 该字段属于被多 Mod 触碰的数组 |

#### FileOverrideInfo

| 字段 | 类型 | 说明 |
|------|------|------|
| `rel_path` | `str` | 文件相对路径 |
| `mod_chain` | `list[str]` | 参与覆盖的 Mod 列表（按优先级） |
| `field_overrides` | `list[FieldOverride]` | 字段级覆盖详情 |
| `new_entries` | `list[tuple]` | 新增/删除条目 |
| `deletions` | `list[DeletionRecord]` | 删除记录 |
| `array_warnings` | `list[str]` | 被多 Mod 修改的数组路径列表 |

### 分析函数

| 函数 | 说明 |
|------|------|
| `analyze_file_overrides(...)` | 分析单个文件的覆盖情况，复用 [ModDelta](04-diff-engine.md) 缓存 |
| `analyze_all_overrides(mod_configs, ...)` | 分析所有文件的覆盖情况 |

冲突检测通过 `flatten_delta()` 展平为字段级列表进行比较。排序规则：冲突项在前，数组合并项次之，普通项在后。

---

## 本体重叠检测（overlap.py）

### compute_base_overlap(store, mod_id) -> bool

判断 Mod 是否修改了游戏本体已有的内容：

- **True**（高风险）：Mod 覆盖了本体内容，可能产生冲突
- **False**（低风险）：Mod 纯增量，只添加新内容

判定逻辑：
- 本体无同名文件 → 纯新增，跳过
- dictionary 类型：检查 key 集合是否有交集
- entity/config 类型：文件存在于本体即视为修改

### compute_all_overlaps(store, mod_ids) -> dict[str, bool]

批量计算所有 Mod 的重叠状态。

---

## 部署（deployer.py）

### deploy_to_workshop(merged_output, workshop_path, mod_names, synthetic_id)

使用**原子部署**策略：先复制到同级临时目录，成功后再删旧目录并重命名。确保部署失败时不会破坏旧的合成 Mod。

合成 Mod 的 ID 固定为 `0000000001`。

### 其他函数

| 函数 | 说明 |
|------|------|
| `generate_info_json(names, path)` | 生成合成 Mod 的 Info.json（含来源 Mod 列表） |
| `copy_resources(mod_paths, ...)` | 复制非 JSON 资源文件，支持 ID 重命名 |
| `clean_synthetic_mod(workshop)` | 清理合成 Mod 目录 |
| `scan_synthetic_mods(workshop)` | 扫描所有带 synthetic 标记的合成 Mod |

---

## ID 重分配（id_remap.py）

### RemapTable

单个 Mod 的 ID 替换映射表：

| 字段 | 类型 | 说明 |
|------|------|------|
| `cards` | `dict[str, str]` | 卡牌 ID |
| `tag_codes` | `dict[str, str]` | Tag code |
| `tag_ids` | `dict[int, int]` | Tag 数字 ID |
| `rite` / `event` / `loot` | `dict[str, str]` | 文件名即 ID 类型 |
| `over` | `dict[str, str]` | over ID |
| `rite_template` / `rite_template_mappings` | `dict[str, str]` | 仪式模板 |

### 核心流程

1. **detect_conflicts** — 检测所有类型的 ID 冲突（tag 特殊：相同 code 但 name 一致不算冲突）
2. **allocate_new_ids** — 为冲突 ID 分配新值（优先级最高的 Mod 保留原 ID）
3. **apply_remap_to_store** — 递归替换 store 中的 ID（整数值、字符串嵌入 ID、DSL 表达式、顶层 key、文件名）
4. **remap_mod_configs** — 主入口，串联完整流程

各类型的分配起始值（ID_ALLOC_START）：cards 从 2900000，rite 从 5090000，event 从 5390000 等。

---

## Override 目录管理（overrides.py）

### invalidate_stale_overrides(overrides_dir, old_ids, new_ids) -> list[str]

Mod 排序或启用状态变化时，删除失效的 override 目录。

从两个有序 mod_id 列表中找到第一个差异位置，差异点及之后的所有 Mod 都视为失效（其覆盖结果建立在已变化的合并链之上）。

返回被删除的 mod_id 列表。
