# JSON 合并策略

本文档描述「苏丹的游戏」Mod 合并管理器的核心合并算法，帮助理解多个 Mod 修改同一 JSON 文件时，工具如何将它们合并为一份最终数据。

---

## 整体流程

```
扫描所有 Mod 的 config/ 目录
        ↓
收集每个 JSON 文件被哪些 Mod 修改
        ↓
逐文件处理：加载游戏本体数据 → 按优先级逐层合并各 Mod 数据 → 输出合并结果
```

合并以游戏本体文件为 **base**，按优先级从低到高依次叠加每个 Mod 的修改。列表中越靠下的 Mod 优先级越高，同一字段以最后一个 Mod 的值为准。

---

## 非标准 JSON 兼容

游戏的 JSON 文件不是严格的标准 JSON，加载时会自动处理以下格式：

| 格式问题 | 处理方式 | 是否报告警告 |
|---------|---------|------------|
| `//` 行注释 | 逐行剥离，保留字符串内的 `//`（如 URL） | 否（游戏常态） |
| 尾随逗号（`},]` 前的逗号） | 正则移除 | 否（游戏常态） |
| UTF-8 BOM 头 | 移除 BOM 字节 | **是**（异常格式） |

示例：
```json
{
    "name": "http://example.com",  // URL 内的 // 会保留
    "value": 123,  // 这个注释会被移除
    "array": [1, 2, 3,],  // 尾随逗号会被移除
}
```

---

## 文件分类

合并前，工具会根据 JSON 数据结构自动分类，不同分类使用不同的合并方式。分类由 `classify_json` 函数完成：

### 1. entity（实体型）

**判定条件**：顶层包含 `id` 字段

**典型文件**：`rite/*.json`、`event/*.json`

**合并方式**：整体递归深度合并

```json
// 示例：rite/5000019.json
{
    "id": 5000019,      // ← 有 id 字段，判定为 entity
    "name": "圣训六十篇",
    "settlement": [...]
}
```

### 2. dictionary（字典型）

**判定条件**：前 5 个 key 的 value 都是 dict，且其中至少有一个包含 `id` 字段

**典型文件**：`cards.json`、`upgrade.json`、`tag.json`

**合并方式**：按顶层 key 逐个深度合并，key 不存在则新增

```json
// 示例：cards.json
{
    "2000001": {"id": 2000001, "name": "..."},  // ← value 是 dict 且含 id
    "2000002": {"id": 2000002, "name": "..."}
}
```

### 3. config（配置型）

**判定条件**：不满足以上两种条件的其他情况

**典型文件**：`over_music_config.json` 等

**合并方式**：整体递归深度合并

---

## 深度合并算法

`deep_merge(base, override)` 是核心递归合并函数，规则如下：

| base 类型 | override 类型 | 合并行为 |
|-----------|-------------|---------|
| dict | dict | 递归合并每个 key |
| list | list（且 key 属于智能合并字段） | 数组智能匹配合并 |
| list | 标量 | 将标量追加到数组（去重） |
| 标量 | list | 将标量插入数组首位（去重） |
| 其他 | 其他 | override 直接替换 base |

**智能合并字段**：`settlement`、`settlement_prior`、`settlement_extre`

这三个字段名对应的数组会使用智能匹配合并，而非简单替换。其他数组字段仍为直接替换。

### 合并示例

```
base:     {"name": "A", "hp": 100, "tags": {"combat": true}}
override: {"hp": 200, "tags": {"magic": true}, "new_field": 1}

结果:     {"name": "A", "hp": 200, "tags": {"combat": true, "magic": true}, "new_field": 1}
```

- `name`：override 中没有，保留 base 值
- `hp`：标量，override 替换 base
- `tags`：dict + dict，递归合并
- `new_field`：base 中没有，新增

---

## 数组智能匹配

对于 `settlement`、`settlement_prior`、`settlement_extre` 字段中的数组，工具会自动识别数组风格并选择对应的匹配策略，将 Mod 中的每个条目与 base 数组中的条目配对。

**匹配成功**：原地递归合并该条目
**匹配失败**：将条目追加到数组末尾

合并过程保持 base 数组的原始顺序不变。

### Rite 风格（四级匹配）

**自动识别条件**：数组元素中包含 `condition` 或 `result_title` 字段

按以下优先级依次尝试，第一个命中即返回：

| 优先级 | 匹配方式 | 说明 |
|-------|---------|------|
| 1 | **guid 精确匹配** | 如 `"guid": "rite_5000019_settlement_1"` |
| 2 | **槽位引用匹配** | condition 中 `s1.is`、`s2.is` 等键值完全相同 |
| 3 | **condition 序列化匹配** | 整个 condition 对象的 JSON 序列化结果相同 |
| 4 | **结果文本匹配** | `result_title` + `result_text` 组合相同 |

#### 示例

游戏本体（base）：
```json
{
    "id": 5000019,
    "name": "圣训六十篇",
    "settlement": [
        {
            "guid": "rite_5000019_settlement_1",
            "condition": {"s2.is": "2000021"},
            "result_title": "成功",
            "result_text": "学习到了社交技巧"
        }
    ]
}
```

Mod A 修改（优先级低）：
```json
{
    "id": 5000019,
    "settlement": [
        {
            "guid": "rite_5000019_settlement_1",
            "result_text": "学习到了高级社交技巧"
        }
    ]
}
```

Mod B 新增（优先级高）：
```json
{
    "id": 5000019,
    "settlement": [
        {
            "guid": "rite_5000019_settlement_2",
            "condition": {"s1.is": "2000022"},
            "result_text": "获得新物品"
        }
    ]
}
```

合并结果：
```json
{
    "id": 5000019,
    "name": "圣训六十篇",
    "settlement": [
        {
            "guid": "rite_5000019_settlement_1",
            "condition": {"s2.is": "2000021"},
            "result_title": "成功",
            "result_text": "学习到了高级社交技巧"   // ← Mod A 通过 guid 匹配，原地合并
        },
        {
            "guid": "rite_5000019_settlement_2",
            "condition": {"s1.is": "2000022"},
            "result_text": "获得新物品"              // ← Mod B 无匹配，追加到末尾
        }
    ]
}
```

### Event 风格（二级匹配）

**自动识别条件**：数组元素中包含 `action` 字段且不包含 `condition` 字段

| 优先级 | 匹配方式 | 说明 |
|-------|---------|------|
| 1 | **action 关键指令匹配** | 提取 action 中的关键标识集合，完全相同即匹配 |
| 2 | **action 序列化匹配** | 整个 action 对象的 JSON 序列化结果相同 |

**关键指令提取规则**（从 action 中提取）：

| 字段 | 提取格式 | 示例 |
|------|---------|------|
| `rite` | `rite:{值}` | `rite:5000508` |
| `event_on` | `event_on:{值}`（可多个） | `event_on:5300001` |
| `prompt.id` | `prompt:{id}` | `prompt:100` |
| `option.id` | `option:{id}` | `option:200` |
| `confirm.id` | `confirm:{id}` | `confirm:300` |

#### 示例

游戏本体（base）：
```json
{
    "id": 5300002,
    "settlement": [
        {
            "tips_text": "触发仪式",
            "action": {"rite": 5000508}
        }
    ]
}
```

Mod 修改：
```json
{
    "id": 5300002,
    "settlement": [
        {
            "tips_text": "触发改良仪式",
            "action": {"rite": 5000508}
        }
    ]
}
```

合并时，两个条目的 action 关键指令都是 `{"rite:5000508"}`，匹配成功，原地合并 → `tips_text` 被替换为 "触发改良仪式"。

---

## 特殊文件处理

### 整文件替换（WHOLE_FILE_REPLACE）

当前仅 **sfx_config.json** 属于此类。该文件不做深度合并，直接使用最后一个（优先级最高的）Mod 的完整数据替换。

如果多个 Mod 同时修改了此文件，会产生警告。

### tag.json name 校验

合并 `tag.json` 时，工具会额外验证：如果 Mod 覆盖了一个已有的 tag，其 `name` 字段必须与本体中该 tag 的 `name` 一致。不一致时会发出警告，因为这可能导致游戏显示出错。

```
示例警告：tag.json: Mod [ModA] 的 tag [tag_001] name="新名字" 与本体 name="原名字" 不一致，可能导致游戏出错
```

---

## 优先级规则

- Mod 列表中越靠下的 Mod 优先级越高
- 合并按优先级从低到高依次执行，后一个 Mod 的修改会覆盖前一个
- 对于标量字段，最后一个 Mod 的值生效
- 对于对象字段，递归合并（每个 Mod 的新增字段都保留）
- 对于智能合并数组，每个 Mod 的条目都会参与匹配和合并

```
优先级: Mod_A(低) → Mod_B(中) → Mod_C(高)

合并顺序: 本体 base → 叠加 Mod_A → 叠加 Mod_B → 叠加 Mod_C
```

---

## 警告和错误收集

合并过程中产生的警告会收集到全局列表中，最终显示在 GUI 的错误日志面板：

| 警告来源 | 触发条件 |
|---------|---------|
| JSON 解析 | 文件包含 UTF-8 BOM 头 |
| Mod 扫描 | Info.json 解析失败 |
| 合并过程 | 多个 Mod 修改整文件替换类文件 |
| 合并过程 | tag.json 的 name 字段不一致 |
