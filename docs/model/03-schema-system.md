# 03 — Schema 规则体系

负责合并规则的定义、加载和自动生成。Schema 规则决定每个字段的合并策略（replace / merge / append / smart_match / coerce），是智能合并的核心依据。

## 涉及文件

| 文件 | 职责 |
|------|------|
| `src/core/schema/loader.py` | 从 `schemas/` 目录加载规则，提供字段定义查询 |
| `src/core/schema/generator.py` | 从游戏本体 config 自动生成 schema 规则文件 |
| `src/core/schema/dsl.py` | 识别游戏 DSL key 模式（counter/have/slot 等） |

## 模块依赖

```
01 数据类型 --------+
07 诊断与性能 ------+--> loader.py
02 JSON 处理管线 ---+       |
dsl.py -------------+       |
                             |
01 数据类型 --------+       |
07 诊断与性能 ------+       |
02 JSON 处理管线 ---+--> generator.py
dsl.py -------------+
```

---

## Schema 加载与查询（loader.py）

### load_schemas(schema_dir) -> dict[str, dict]

扫描 `schemas/` 目录下所有 `*.schema.json` 文件，返回 `{pattern: schema_dict}` 映射。

同时加载 `_global.schema.json` 中的全局模板（`__templates__`）和 DSL 规则（`_dsl_rules`）。

### resolve_schema(rel_path, schemas) -> dict | None

根据文件相对路径匹配 schema，两级匹配：

1. **精确匹配**：如 `cards.json`
2. **目录匹配**：如 `rite/5000001.json` 匹配 `rite/`

### get_field_def(schema, field_path) -> dict | None

在 schema 树中递归查找字段定义，支持复杂导航逻辑：

| 场景 | 处理方式 |
|------|---------|
| `_entry` / `_fields` | 直接取对应的 dict |
| `__fields__` 节点 | 进入 `__fields__` 子结构查找 |
| `__use_template__` | 解析全局命名模板后继续导航 |
| `__template__` | 回退到同类子结构模板继续导航 |
| `__element__` | 进入数组元素字段定义 |
| DSL 模式 key | 调用 `classify_dsl_key()` 匹配后返回 DSL 规则 |

查询结果通过 `(schema_id, field_path_tuple)` 缓存。

### 辅助函数

| 函数                                                 | 说明                                                  |
|------------------------------------------------------|-------------------------------------------------------|
| `get_schema_root_key(schema)`                        | 根据文件类型确定根级 key（`_entry` 或 `_fields`）     |
| `check_type_match(schema_type, value)`               | 检查实际值的类型是否匹配 schema 定义                  |
| `is_known_field(schema, field_path, field_def, key)` | 判断 key 是否为当前 schema 位置的已知字段             |

`is_known_field` 封装了合并引擎所需的全部 schema 查询逻辑，以下情况返回 `True`（已知）：

- schema 信息不足（无 schema / 无 field_def）
- dictionary 顶层（key 为任意实体 ID）
- key 存在于 field_def 的已知字段集合中
- key 匹配 DSL 模式（`classify_dsl_key`）

`SCHEMA_META_KEYS` 常量定义了 schema 节点自身的保留 key（`__type__`、`__merge__`、`__fields__` 等），用于从 field_def 中区分元数据和实际字段定义。

---

## Schema 自动生成（generator.py）

### generate_all(config_dir, output_dir, progress_callback)

从游戏本体 config 目录自动生成所有 schema 规则文件。采用**两遍处理**：

#### Pass 1 — 收集字段信息

1. 遍历根目录 JSON 文件和子目录，调用 `collect_field_info()` 递归分析
2. 按规范字段名（`_canonical_field_name`）聚合全局字段信息到 `_global_field_info`
3. 自动发现命名模板：在多个不同路径中复用（paths >= 2）且有子 key 的字段注册为模板
4. 构建 DSL 规则：从模板字段中聚合每个 DSL 组的类型信息

#### Pass 2 — 构建 schema

1. 利用 Pass 1 的字段信息和模板注册表，为每个文件/目录构建 schema
2. 输出 `_global.schema.json`（全局模板 + DSL 规则）和各文件的 `*.schema.json`

### 命名模板系统

通过 `_canonical_field_name()` 将等价字段映射到规范名：

- 精确映射：`any`/`all` -> `condition`，`result`/`choose`/`success`/`failed` -> `action`
- 正则映射：`case:opN` -> `action`，`sN` -> `slot`，纯数字 -> `music_entry`

### 合并策略推断

`infer_merge_strategy()` 根据字段名、类型、结构信息推断合并策略：

| 类型 | 推断策略 |
|------|---------|
| 标量 | `replace` |
| `object` | `merge` |
| `array<object>` | 有 match key -> `smart_match`；无 -> `append` |
| 其他 array | `append` |
| 联合类型含 array | `append` 或 `smart_match` |
| 其余联合类型 | `coerce` |

### ensure_schemas(config_dir, schema_dir)

启动时自动检查 `schemas/` 是否已初始化，为空则执行生成。

---

## DSL Key 模式识别（dsl.py）

### classify_dsl_key(key) -> str | None

将单个 JSON key 匹配到 DSL 模式组名，未匹配返回 None。

游戏的条件（condition）和命令（action/result）对象中，key 分为固定 key（schema 明确定义）和 DSL key（参数化表达式），本模块识别后者。

### DSL 模式列表

| 组名 | 匹配示例 |
|------|---------|
| `counter` | `counter+7000721`, `global_counter=100>` |
| `table` | `table_have.xxx`, `!table.yyy` |
| `total` | `total.xxx` |
| `have` | `have.主角.苏丹`, `!hand_have.xxx` |
| `cost` | `cost.xxx` |
| `slot` | `s1`, `!s3.prop`, `s2+op` |
| `focus` | `focus.1` |
| `loot_dsl` | `loot.xxx`（含点号的 loot 操作） |
| `clean` | `clean.xxx` |
| `sudan_pool` | `sudan_pool.xxx` |
| `pop` | `pop.xxx`, `hand_pop.xxx`, `rite_pop.xxx` |
| `case` | `case:op1`, `case:op2` |
| `formula` | `f1:xxx`, `r:xxx` |
| `change_card` | `change_card_xxx.yyy` |
| `rare` | `rare>=5`, `rare<3` |
| `entity_op` | `2000082.uprare`, `妻子+晋升` |
| `attr_cmp` | `智慧>=`, `魔力<`, `round<=` |
| `negated` | `!金币`, `!怪物` |
| `tag_check` | `主角`, `贵族`, `追随者` |

共 18 种模式，按优先级顺序匹配。DSL key 在 schema 中不单独定义，而是通过全局 DSL 规则统一处理。

---

## 字段合并策略一览

Schema 定义的 `__merge__` 字段值，供 [差异计算](04-diff-engine.md) 和 [合并引擎](05-merge-engine.md) 使用：

| 策略 | 说明 |
|------|------|
| `replace` | 直接用新值替换旧值 |
| `merge` | 递归合并 dict 字段 |
| `coerce` | 类型兼容转换后替换 |
| `append` | 数组追加新元素 |
| `smart_match` | 按 match key 匹配数组元素后逐元素合并 |
