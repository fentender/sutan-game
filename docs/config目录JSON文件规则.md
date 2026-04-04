# 苏丹的游戏 - config 目录 JSON 文件规则

> 本文档通过脚本自动分析 `Sultan's Game_Data/StreamingAssets/config/` 目录下所有 JSON 文件的结构，并手动注释关键字段含义。
>
> **格式说明**：游戏 JSON 不是标准 JSON，支持 `//` 行注释和尾随逗号，需预处理后解析。  
> **图标资源**：`resource`/`resource` 等路径是相对于游戏 Resources 目录的路径字符串。

---

## 一、config 目录下的独立文件

### 1. cards.json — 卡牌定义表

- **结构**：`id_dict`，顶层 key 为卡牌 ID 字符串（如 `"2000001"`），共 ~1292 个条目
- **大小**：~952 KB

```yaml
id              : int          卡牌数字 ID
name            : string       卡牌名称
title           : string       卡牌称号/副标题
text            : string       卡牌描述文本
tips            : string       提示文本（通常为空）
type            : string       卡牌类型，如 "char"(角色)、"item"(物品) 等
rare            : int          稀有度 1~4
resource        : string|array 卡面图片资源路径，单张 string，多张 array[string]
tag             : object       标签字典（动态key，约216种）
  {标签名}      : int          标签数值（如 "体魄": 3，"魅力": 2）
card_favour     : string       好感度关联（通常为空）
card_vanishing  : int          消逝状态（0=不消逝）
vanish          : object       消逝配置（可选）
  over          : int          触发消逝的结局 ID
  event_on      : int|array    消逝时触发的事件 ID
  delay         : object       延迟消逝
    id          : int          仪式/事件 ID
    round       : int          延迟回合数
    event_on    : int          触发的事件
equips          : array[string] 装备槽位，如 ["武器","服装","饰品"]
is_only         : int          是否唯一（0/1）
pops            : array        卡牌弹出效果列表（通常为空数组，实际为空）
post_rite       : array[object] 附属仪式列表，每个元素：
  guid          : string       UUID
  condition     : object       触发条件（见条件表达式语法）
  result        : object       效果（见效果表达式语法）
  result_text   : string       结果文本
destroy_resources: array[string] 卡牌被消灭时需要销毁的额外资源路径列表
sfx             : string       音效资源名
```
---

### 2. tag.json — 标签/属性定义表

- **结构**：`id_dict`，顶层 key 为标签英文代码（如 `"physique"`），共 ~442 个条目

```yaml
id                 : int    标签数字 ID（3000xxx）
name               : string 标签中文名称
code               : string 标签英文代码
type               : string 标签类型，如 "buff"
text               : string 标签描述文本
tips               : string 提示文本
resource           : string 图标资源名
can_add            : int    是否可添加（0/1）
can_visible        : int    是否可见（0/1）
can_inherit        : int    是否可继承（0/1）
can_nagative_and_zero : int 是否允许负数和零（0/1）
fail_tag           : array[string]  互斥标签列表（拥有这些标签时无法添加本标签）
tag_vanishing      : int    标签消逝计时
tag_sfx            : string 标签音效
tag_rank           : int    标签排序权重（越大越靠前）
attributes         : object 附加属性（可选，如 "吸附指定": int）
```
---

### 3. over.json — 结局定义表

- **结构**：`id_dict`，顶层 key 为结局 ID（如 `"0"`, `"100"` 等），共 ~159 个条目

```yaml
name             : string  结局名称
sub_name         : string  结局副标题（可选）
text             : string  结局描述文本
text_extra       : array[object] 条件文本列表，每个元素：
  condition      : object  条件（动态key，见条件语法）
  result_text    : string  条件满足时显示的额外文本
bg               : string  背景图资源路径
icon             : string  图标资源路径
title            : string  标题图资源路径
open_after_story : int     是否解锁后日谈（0/1，可选）
success          : int     是否算胜利结局（可选）
manual_prompt    : bool    是否手动触发提示（可选）
```
---

### 4. quest.json — 任务/成就表

- **结构**：`id_dict`，顶层 key 为任务 ID（`"3300xxx"`），共 ~161 个条目

```yaml
id            : int    任务数字 ID
name          : string 任务名称
text          : string 任务描述
favour_text   : string 完成时的感言文本
upgrade_point : int    完成奖励的升级点数
pre           : int    前置任务 ID（0 表示无前置）
target        : array[object] 目标条件列表，每个元素：
  text        : string 目标描述文本
  show_counter: string 显示的计数器名（可选，如 "global_counter.7220001"）
  condition   : object 完成条件（动态key，见条件语法）
icon          : string 图标资源路径
```
---

### 5. upgrade.json — 升级商店

- **结构**：`id_dict`，顶层 key 为升级项 ID（`"3300xxx"`），共 ~50 个条目

```yaml
id           : int    升级项数字 ID
name         : string 名称
text         : string 描述文本
cost         : int    花费的升级点数
condition    : object 解锁前提条件（动态key），例如：
  unlock_upgrade    : int  需要先解锁的升级 ID
  {counter_id}>=    : int  计数器条件
icon         : string 图标类型，如 "gain"
link_card    : int    关联卡牌 ID（点击图标弹出该卡牌详情）
effect       : object 购买后的效果（动态key），例如：
  counter+{ID}      : int     增加计数器
  global_counter={ID}: int    设置全局计数器
  sudan_card        : array[int]  苏丹卡池（卡牌 ID 列表）
  g.card            : array       赠送卡牌，格式 [卡牌ID(int), "追随者+1"(string)]
  {cardID}+{标签}   : int     给指定卡添加标签
  主角+{标签}       : int     给主角添加标签
incompatible : int    互斥的升级项 ID（0 表示无）
```
---

### 6. gallery_cards.json — 图鉴卡牌数据

- **结构**：`id_dict`，顶层 key 为卡牌 ID，共 ~1066 个条目

```yaml
id        : int    卡牌 ID
is_show   : int    是否默认在图鉴显示
show_type : string 展示分类，如 "char"
sort      : int    排序权重
resources : array  图片资源列表，每个元素：
  pic_res : string 图片路径
  rare    : int    稀有度（1~4）
plots     : array  剧情条目列表，每个元素：
  guid        : string  UUID
  title       : string  剧情标题
  data        : array   剧情内容列表，每个元素：
    guid        : string UUID
    plot_title  : string 情节标题
    plot_text   : string 情节文本
```
---

### 7. gallery_cg.json — CG 画廊配置

- **结构**：`id_dict`，顶层 key 为 CG 编号，共 ~15 个条目

```yaml
over_id      : array[int] 关联的结局 ID 列表（触发对应结局后解锁）
icon         : string     选中状态图标资源
icon_deselect: string     未选中状态图标资源
icon_lock    : string     锁定状态图标资源
big_resource : string     大图资源路径
title        : string     CG 标题
```
---

### 8. credits.json — 制作人员名单

- **结构**：顶层包含 `developers`（对象）和 `contributors`（数组）

**developers** 每个成员：
```yaml
name : string 显示名
job  : string 职位
```
**contributors** 每个元素包含 `title`、`type`、`group` 数组，`group` 里有 `title`、`members` 数组。

---

### 9. ui.json — UI 文本本地化表

- **结构**：`id_dict`，顶层 key 为文本 ID（如 `"GAME_TITLE"`），共 ~417 个条目

```yaml
zhCN    : string UI 文本（简体中文，支持富文本标签）
comment : string 策划注释
```
---

### 10. variable.json — 全局系统变量

- **结构**：`object`（扁平 key-value），共 ~92 个配置项

主要字段分类：

```yaml
configVersion        : int    配置版本号（时间戳）
default_language_code: string 默认语言代码
support_language     : object 支持的语言列表 {代码: 显示文本}
language_code_map    : object 语言代码映射（Unity/Steam -> 内部代码）
support_resolution   : object 支持的分辨率 {"1920x1080": [1920,1080], ...}
support_fullScreen   : array[string]  支持的全屏模式，如 ["ExclusiveFullScreen","Windowed"]
support_resolution   : object         支持的分辨率 {"1920x1080": [1920,1080], ...}，值为 array[int]
card_state_icon      : object         卡牌状态图标 {状态名: 图标路径}
auto_classify        : bool           是否自动分类卡牌
all_auto_classify_tags  : array[string]  自动分类的标签列表，如 ["char","army","coin"]
auto_classify_bag_tags  : array[array]   背包自动分类分组，每组为 array[string]，如 [["char","sudan"],["consumable"]]
gallery_card_show_items : array[string]  图鉴展示分类，如 ["item","sudan","monster"]
gallery_card_type       : array[string]  图鉴卡牌类型，如 ["char","other_char","weapon_equip"]
settle_card_bad_state   : array[string]  结算时视为负面的卡牌状态，如 ["cursed","hurt","incapacitate"]
show_note_type          : array[array]   显示的笔记类型分组，每组为 array[int]
special_counters        : array[int]     特殊计数器 ID 列表
contact                 : array[object]  联系方式列表，每个元素：
  id   : string  群号/ID
  url  : string  链接
  full : bool    是否已满
harmonious_replacer  : object 和谐版资源替换映射 {原路径: 替换路径}
fade_in_transition_time : object 各场景淡入时间（单位：秒）
  Armageddon/Default/DrawSudanCard/MainGame/... : float|int
fade_out_transition_time: object 各场景淡出时间
begin_guide_sprites  : object 引导动画图片帧
  Arrow/MouseDrag/MouseLeftClick/... : array[string]  帧图片资源名列表
ui_size              : object 响应式 UI 尺寸 {xs/sm/md/lg/xl/xxl: float}
support_font_size    : object 字体尺寸等级映射
text_link            : object 外部链接 {名称: URL}
hover_pop_show_delay : float  悬停弹出延迟（秒）
target_fps           : int    目标帧率
music_transition_time: int    音乐过渡时间
result_text_play_rate: float  结果文本播放速率
CARD_TAG_*_FORMAT    : string 卡牌标签显示格式字符串
RITE_*               : string 仪式界面相关格式字符串
MAIN_UI_*            : string 主界面相关格式字符串
```
---

### 11. textstyle.json — 文本样式表

- **结构**：`id_dict`，顶层 key 为样式名（`"@CARD_TITLE"` 等前缀），共 ~80 个条目

```yaml
font            : string    字体名（如 "CardTitle SDF"）
material        : string    材质名（可选）
size            : int       固定字号（可选）
css_size        : object    响应式字号
  xs/sm/md/lg/xl/xxl : int 各尺寸等级的字号值
enableAutoSize  : bool      是否自动调整大小（可选）
sizeRange       : array[int]  自动大小范围 [min, max]（可选）
characterSpacing: int       字间距（可选）
lineSpacing     : float     行间距（可选）
isRightToLeftText: bool     是否从右到左排列（可选）
```
---

### 12. imagestyle.json — UI 图片位置/尺寸

- **结构**：`id_dict`，顶层 key 为 UI 元素名，共 4 个条目

```yaml
posX   : float|int X 坐标
posY   : float|int Y 坐标
width  : float|int 宽度
height : float|int 高度
```
---

### 13. mobile_help.json — 移动端帮助幻灯片

- **结构**：`id_dict`，顶层 key 为页面名（`"Main"/"Rite"/"Card"/"Bag"`），共 4 个条目

```yaml
type   : string 帮助页类型
slides : array[object]  幻灯片列表，每个元素：
  res  : string 图片资源名
  text : string 说明文本（通常为空）
```
---

### 14. rite_template_mappings.json — 仪式模板映射表

- **结构**：`id_dict`，顶层 key 为映射 ID（`"8001xxx"` 或 `"0"`），共 ~453 个条目

```yaml
id          : int    映射 ID
tips        : string 策划备注（如 "宫廷-权力的游戏"）
template_id : int    关联的仪式模板 ID（8000xxx）
slot_open   : array  开放的卡槽列表（如 ["s1","s2","s3",...]）
```
---

### 15. over_music_config.json — 结局音乐配置

- **结构**：`id_dict`，顶层 key 为结局 ID，共 ~150 个条目

```yaml
clip       : string 音频文件名（如 "over_game_dead"）
start      : int    开始播放位置（秒）
loop_start : int    循环起点（秒）
loop_end   : int    循环终点（秒），-1 表示末尾
```
---

### 16. sfx_config.json — 游戏内 BGM 配置

- **结构**：顶层分为两类：
  - **场景组**（`"main_game_loop"`, `"settle_loop"` 等）：key 为等级数字（`"0"~"7"`）
  - **仪式专用**（key 为仪式 ID 字符串，如 `"5006068"`）

场景组内每个等级条目：
```yaml
clip              : string     音频文件名
start             : float|int  开始播放位置（秒）
loop_start        : float|int  循环起点（秒）
loop_end          : float|int  循环终点（秒）
play_in_rite_create: bool      是否在仪式创建时播放（可选，仅仪式专用）
play_instant       : bool      是否立即播放（可选，仅仪式专用）
```
---

### 17. sfx_npc_role_dub.json — NPC 配音映射

- **结构**：`object`，key 为卡牌 ID，value 为配音文件名数组，共 ~584 个条目

```yaml
{卡牌ID}: array[string]  该卡牌的配音音效列表，如 ["mg001","mg002","mg003"]
```
---

### 18. sfx_settle_card_new.json — 获得新卡音效

- **结构**：`object`，key 为卡牌 ID 或特殊编号，value 为音效名，共 9 个条目

```yaml
"0"      : string  通用音效（非角色卡默认）
"1"      : string  正面音效（角色卡默认）
{卡牌ID} : string  特定卡牌的音效（覆盖默认）
```
---

## 二、config 子目录

### 1. after_story/ — 后日谈

- **文件数**：66 个，文件名为对应卡牌 ID（`2000001.json` 等）

```yaml
id    : int    卡牌 ID
name  : string 角色名
prior : array[object]  优先显示的后日谈（结构同 extra 元素）
extra : array[object]  额外后日谈列表，每个元素：
  key         : string  唯一标识（如 "2000001_extra_1"）
  sort        : int     排序权重
  pic         : string  图片资源路径（可选）
  condition   : object  显示条件（见条件语法）
  result_title: string  标题（可选）
  result_text : string  后日谈文本
```
---

### 2. dt/ — 对话树（Dialog Tree）

- **文件数**：9 个（`DT1.json` ~ `DT9.json`）

```yaml
dialog_tree_id : string 对话树 ID
first_word_id  : string 起始节点 ID
description    : string 描述
Item           : array[object]  对话节点列表（内部对话树格式）
```
---

### 3. event/ — 事件

- **文件数**：1863 个，文件名为事件 ID（`5300000.json` 等）

```yaml
id            : int    事件 ID
text          : string 策划备注
is_replay     : int    是否可重复触发（0/1）
auto_start    : bool   是否本局开始时自动启动
auto_start_init: array[object] 自动启动时要同步开启的其他事件配置（可选）
start_trigger : bool   启动后是否立即校验条件
on            : object 触发时机
  round_begin_ba : int|array[int]  回合开始时触发，int 表示任意回合，array 表示指定回合号列表
  rite_end       : int        仪式结束时触发
  card_clean     : int        卡牌清除时触发
  rite_can_fill  : int        仪式可放入卡牌时触发
  counter        : int        计数器变化时触发
condition     : object  触发条件（动态key，约70种，见条件语法）
settlement    : array[object]  触发后的结算列表，每个元素：
  tips_resource : string  提示资源路径（空则不弹提示框）
  tips_text     : string  提示文本（空则不弹）
  action        : object  执行动作（动态key，约55种，见动作语法）
start_trigger : bool    是否启动时立即触发检查
```
---

### 4. init/ — 初始化配置（游戏模式）

- **文件数**：2 个（`0.json` 和 `1.json`，对应不同游戏难度/模式）

```yaml
id                         : int    模式 ID
name                       : string 模式名称
cards                      : array[int]     额外可加入牌库的卡牌 ID 列表（通常为空）
default_cards              : array[int]     默认初始手牌卡牌 ID 列表（如 [2000024,2000517,2000518]）
default_rite               : array[int]     默认开放的仪式 ID 列表（通常为空）
sudan_pool                 : array[int]     苏丹卡池，卡牌 ID 列表（可重复，代表权重）
sudan_pool_desc            : array[string]  苏丹卡池描述文本列表
sudan_life_time            : int    苏丹卡生命周期（天数）
sudan_redraw_count         : int    苏丹卡重抽次数
sudan_redraw_times_per_round: int   每回合重抽次数
sudan_redraw_times_recovery_round: int 重抽次数恢复回合
sudan_shuffle              : bool   是否打乱苏丹卡顺序
sudan_card_desc            : object 苏丹卡文本描述配置 {卡牌ID: array[string]}（描述文本行）
sudan_box_show_after_wizard: bool   向导结束后是否显示苏丹盒
gold_dice_count            : int    金色骰子数量
single_dice_face_weight    : array[int]  骰子面权重列表（每面的权重，如 [1,1,2,2,3,3]）
difficulty                 : array[object]  难度等级配置列表，每个元素：
  name                     : string 难度名
  title                    : string 难度标题
  desc                     : string 难度描述
  gold_dice_count          : int    金色骰子数量
  single_dice_face_weight  : array[int]  骰子面权重
  sudan_life_time          : int    苏丹卡生命周期
  sudan_redraw_times_per_round: int 每回合重抽次数
  back_to_prev_round_count : int    可回溯回合次数
back_to_prev_round_count   : int    默认可回溯回合次数
card_equips                : object 初始装备配置 {卡牌ID: array[string]（装备槽位名）}
wizard                     : string 关联的向导 ID
think_id                   : int    "俺寻思"功能配置 ID
think_show_settlement      : bool   是否显示结算
show_deadline              : bool   是否显示倒计时
show_helpbtn               : bool   是否显示帮助按钮
show_ithink                : bool   是否显示"俺寻思"
show_prestige              : bool   是否显示声望
show_story                 : bool   是否显示故事
```
---

### 5. loot/ — 掉落表

- **文件数**：193 个，文件名为掉落表 ID（`6000004.json` 等）

```yaml
id     : int    掉落表 ID
name   : string 掉落表名称（策划备注）
repeat : int    可触发次数
type   : int    掉落类型（2=普通权重, 3=维新/必出一个, 99=全部掉落）
item   : array[object]  掉落物品列表，每个元素：
  id        : string  卡牌 ID
  type      : string  类型，固定为 "card"
  num       : string  数量
  weight    : int     权重（type=2 时有意义）
  condition : object  掉落前提条件（动态key，见条件语法，可选）
```
---

### 6. rite/ — 仪式

- **文件数**：1495 个，文件名为仪式 ID（`5000001.json` 等）

```yaml
id               : int    仪式 ID
name             : string 仪式名称
text             : string 仪式描述
tips             : string 提示文本
type             : string 仪式类型（可选）
mapping_id       : int    关联模板映射 ID（对应 rite_template_mappings.json）
once_new         : int    是否仅新建时触发（0/1）
round_number     : int    持续回合数
waiting_round    : int    等待回合数
waiting_round_end_action: array[object]  等待结束后的动作列表，每个元素结构同 settlement 元素（含 condition/result/result_title/result_text/action）
method_settlement: string 结算方式
auto_begin       : int    是否自动开始（0/1）
auto_result      : int    是否自动结算（0/1）
location         : string 地点，格式 "地点名:编号"（如 "自宅:1"）
icon             : string 图标资源名
tag_tips         : array[string]  关联属性提示（如 ["智慧","社交"]）
tag_tips_up      : object 增强版属性提示
  tips           : array[string]  属性名列表
  type           : string 提示类型
tips_text        : array[string]  详细提示文本列表
open_conditions  : array[object]  开启条件列表，每个元素：
  condition      : object 条件表达式
  tips           : string 条件未满足时的提示文本
random_text      : object 随机文本（旧版）
  r1             : string 随机文本内容
random_text_up   : object 随机文本（新版，支持多个）
  r1/r2/...      : object 随机文本条目
    text         : string 文本内容
    type         : string 类型（如 "normal_result"）
    type_tips    : string 类型提示
    low_target   : int    低目标骰点数
    low_target_tips: string 低目标提示
random_effect    : object 随机效果（可选）
cards_slot       : object 卡槽约束配置（可选）
  s{N}           : object 第 N 个卡槽的约束
    guid         : string UUID
    text         : string 说明文本
    is_key       : int    是否为关键槽位（0/1）
    is_empty     : int    是否初始为空（0/1）
    is_enemy     : int    是否为敌方槽位（0/1）
    open_adsorb  : int    是否可吸附（0/1）
    condition    : object 放入卡牌的条件（见条件语法）
    pops         : array[object]  槽位弹出效果列表，每个元素：
      condition  : object 触发条件（见条件语法）
      action     : object 动作（见动作语法）
settlement_prior : array[object]  优先结算列表（先于普通结算检查），每个元素：
  guid           : string UUID（用于合并匹配）
  condition      : object 触发条件（见条件语法）
  result_title   : string 结果标题
  result_text    : string 结果描述文本
  result         : object 效果（动态key，见效果语法）
  action         : object 后续动作（动态key，见动作语法）
settlement       : array[object]  普通结算列表（结构同 settlement_prior 元素）
settlement_extre : array[object]  额外结算列表（结构同 settlement_prior 元素）
```
---

### 7. rite_template/ — 仪式背景模板

- **文件数**：251 个，文件名为模板 ID（`8000001.json` 等）

```yaml
id               : int        模板 ID
name             : string     模板名称
tips             : string     策划备注
bg               : string     背景图资源名
fg               : string|null 前景图资源名（null 表示无）
fg_in_slot_index : int        前景图插入的卡槽索引（可选）
nomal_slot_bg    : string     默认卡槽背景
bg_pos           : object     背景位置
  x              : int        X 坐标
  y              : int        Y 坐标
title_pos        : object     标题位置
  x              : int        X 坐标
  y              : int        Y 坐标
title_bg_hide    : bool       是否隐藏标题背景（可选）
title_help_btn_hide: bool     是否隐藏帮助按钮（可选）
slots            : object     卡槽定义（s1~s17）
  s{N}           : object     第 N 个卡槽
    is_hide_bg          : bool       是否隐藏卡槽背景
    is_set_card_hide_bg : bool       放入卡牌后是否隐藏背景
    slot_bg             : string|null 自定义卡槽背景（null 表示使用默认）
    pos                 : object     位置坐标
      x                 : int
      y                 : int
    scale               : object     缩放比例
      x                 : float|int
      y                 : float|int
    rotation_z          : float|int  Z 轴旋转角度
```
---

### 8. wizard/ — 开局向导

- **文件数**：2 个（`wizard.json`, `wizard_sudan.json`）

```yaml
id                              : string 向导 ID
name                            : string 向导名称
text                            : string 向导描述文本
options                         : array[object]  向导选项列表，每个元素：
  name     : string  选项显示名
  text     : string  选项描述文本
  op       : string  操作类型（如 "redraw" 重抽，"draw" 抽卡，"back" 返回）
  options  : array[object]  子选项列表（同父结构）
prompt_draw_sudan_start         : string 抽苏丹卡开始提示
prompt_draw_sudan_start_first   : string 首次抽苏丹卡提示
prompt_draw_sudan_end_with_times   : string 有重抽次数时的结束提示
prompt_draw_sudan_end_without_times: string 无重抽次数时的结束提示
```
---

## 三、通用 DSL 语法

### 条件表达式（condition 字段）

`condition` 是一个动态 key 字典，key 本身就是条件操作符（可能含点号），value 通常为 `int`（表示目标值）。

#### 逻辑组合

| key   | value 类型 | 含义 |
|-------|-----------|------|
| `all` | object    | 所有子条件均满足（AND） |
| `any` | object    | 任一子条件满足（OR） |

#### 卡牌/物品检查

| key 模式 | 含义 |
|---------|------|
| `have.{卡牌名}` | 拥有指定名称的卡牌 |
| `have.{卡牌ID}` | 拥有指定 ID 的卡牌 |
| `have.{卡牌ID}.{标签}` | 拥有该卡牌且有指定标签 |
| `!have.{xxx}` | 不拥有（前缀 `!` 表示否定） |
| `table_have.{xxx}` | 桌面（仪式中）有该卡牌 |
| `table_have.{ID}.{标签}` | 桌面卡牌有指定标签 |
| `hand_have.{xxx}` | 手牌中有该卡牌 |

#### 卡槽检查

| key 模式 | 含义 |
|---------|------|
| `is` | 当前槽位有卡 |
| `!is` | 当前槽位为空 |
| `s{N}` | 第 N 号槽位有卡 |
| `s{N}.is` | 第 N 号槽位的卡是指定 ID |
| `s{N}.{标签}` | 第 N 号槽位的卡有指定标签 |
| `!s{N}` | 第 N 号槽位为空 |
| `type` | 卡牌类型匹配 |
| `is_rite` | 正在进行仪式 |
| `!is_rite` | 不在仪式中 |

#### 计数器/属性检查

| key 模式 | 含义 |
|---------|------|
| `counter.{ID}>=` | 计数器 ≥ 值 |
| `counter.{ID}<` | 计数器 < 值 |
| `counter.{ID}=` | 计数器 = 值 |
| `global_counter.{ID}>=` | 全局计数器 ≥ 值 |
| `{标签名}` | 有该标签（且值 ≥ 1） |
| `{标签名}>=` | 标签值 ≥ 指定值 |
| `{标签名}<` | 标签值 < 指定值 |
| `f:{表达式}>=` | 属性组合运算 ≥（如 `f:智慧+社交>=` ）|
| `parent.{标签}` | 父卡牌有指定标签 |
| `self.{标签}` | 自身有指定标签 |
| `rare=` / `rare>=` | 稀有度比较 |
| `cost.{标签}` | 消耗代价（值为 int 或 array） |

---

### 效果表达式（result 字段）

`result` 也是动态 key 字典，表示仪式结算效果。

| key 模式 | value 类型 | 含义 |
|---------|-----------|------|
| `counter+{ID}` | int | 增加计数器 |
| `counter-{ID}` | int | 减少计数器 |
| `counter={ID}` | int | 设置计数器为值 |
| `global_counter+{ID}` | int | 增加全局计数器 |
| `global_counter={ID}` | int | 设置全局计数器 |
| `s{N}+{标签}` | int | 给第 N 槽卡牌添加标签 |
| `s{N}-{标签}` | int | 移除第 N 槽卡牌的标签 |
| `s{N}={标签}` | int | 设置第 N 槽卡牌的标签值 |
| `clean.s{N}` | int | 清除第 N 槽 |
| `clean.rite` | int | 结束/清除仪式 |
| `card` | int\|array | 给予卡牌 |
| `coin` | int | 给予金币 |
| `金币` | int | 金币变化 |
| `choose` | object | 让玩家选择 |
| `total.{ID}+{标签}` | int | 全局给指定卡添加标签 |
| `table.{ID}-{标签}` | int | 移除桌面卡牌的标签 |
| `no_show` | object | 无提示执行子效果 |
| `prompt` | object | 弹出提示（见动作语法的 prompt） |

---

### 动作语法（action 字段）

`action` 出现在 `settlement[].action`、`waiting_round_end_action[].action` 等处，表示触发后的动作。

| key | value 类型 | 含义 |
|-----|-----------|------|
| `rite` | int | 触发/开启指定仪式 |
| `event_on` | int\|array[int] | 开启事件（单个或多个） |
| `event_off` | int | 关闭事件 |
| `over` | int | 触发结局 |
| `card` | int\|array[int] | 给予卡牌（单张 int 或多张 array） |
| `coin` | int | 给予金币 |
| `counter+{ID}` | int | 增加计数器 |
| `global_counter={ID}` | int | 设置全局计数器 |
| `prompt` | string\|object | 文本提示框（简写用 string，完整用 object） |
| `confirm` | string\|object | 确认/取消对话框 |
| `success` | object | confirm 确认后执行的后续动作（递归 action 结构） |
| `failed` | object | confirm 取消后执行的后续动作（递归 action 结构） |
| `option` | object | 多选项对话框（见下方结构） |
| `case:{N}` | object | option 中第 N 个选项被选中后的动作（递归 action 结构） |
| `case:{tag}` | object | option 中 tag 匹配的选项被选中后的动作（递归 action 结构） |
| `case:def` | object | option 中默认情况（兜底）的动作 |
| `slide` | string\|array[string] | 幻灯片展示图片，单图 string，多图 array |
| `clean.rite` | int | 结束/清除仪式 |
| `delay` | object | 延迟执行配置 |
| `no_prompt` | object | 无提示执行子动作（递归 action 结构） |
| `pop.{仪式ID}_{编号}.{槽位}` | string | 槽位弹出关联卡牌动作 |
| `begin_guide` | object | 触发新手引导 |
| `clean.s{N}` | int | 清除第 N 号槽位 |
| `total.{ID}+{标签}` | int | 全局给指定卡添加标签 |

**prompt 对象结构**：
```yaml
id   : string          提示 ID（唯一标识）
text : string          提示文本（支持富文本）
icon : string|array    图标资源路径，array 时为左中右三图 [左图, 中图, 右图]
```

**confirm 对象结构**：
```yaml
id           : string        对话框 ID
text         : string        对话框提示文本
icon         : array[string] 图标列表，格式同 prompt.icon
confirm_text : string        确认按钮文本
cancel_text  : string        取消按钮文本
confirm_icon : string        确认按钮图标（可选）
cancel_icon  : string        取消按钮图标（可选）
```

**option 对象结构**：
```yaml
id    : string         选项组 ID（唯一标识）
text  : string         选项组主文本
icon  : string|array   图标（同 prompt.icon）
items : array[object]  选项列表，每个元素：
  text : string   选项文本
  icon : string   选项图标（可选）
  tag  : string   选项标签（用于 case:{tag} 匹配，如 "op1"/"op2"）
```

> **option / case 协作说明**：`option` 定义多选项弹窗，玩家选择后匹配对应的 `case:{tag}` 动作。
> 可以用选项编号（`case:1`/`case:2`...）或选项 `tag`（`case:op1`/`case:op2`...）匹配，
> `case:def` 作为兜底（未匹配到时执行）。`case:` 的 value 是递归的 action 结构，
> 可以嵌套 `event_on`/`event_off`/`rite`/`prompt`/`card`/`coin` 等任意动作。

**delay 对象结构**：

```yaml
id    : int   仪式/事件 ID
round : int   延迟回合数
```
