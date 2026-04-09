"""
DSL key 模式定义 — 游戏 JSON 中条件/命令字段的 key 匹配规则。

条件（condition）和命令（action/result）对象的 key 分两类：
1. 固定 key：schema 中通过 fields 明确定义（如 event_on, type, is）
2. DSL key：可用正则描述的参数化表达式（如 counter+7000721, have.主角.苏丹）

本模块提供 classify_dsl_key() 函数，用于识别 DSL key。
"""
import re

# DSL key 模式：(组名, 编译后的正则表达式)
DSL_KEY_PATTERNS = [
    # counter / global_counter 操作与比较
    ("counter",        re.compile(r"^(?:global_)?counter[+\-=.]\d+[<>=]*$")),
    # table 系列（包含 table_have，支持 ! 前缀）
    ("table",          re.compile(r"^!?table(?:_have)?\..*$")),
    # total 操作
    ("total",          re.compile(r"^total\..*$")),
    # have 检查（含否定、含 hand_have / sudan_pool_have / rite_have）
    ("have",           re.compile(r"^!?(?:hand_|sudan_pool_|rite_)?have\..*$")),
    # cost 检查
    ("cost",           re.compile(r"^cost\..*$")),
    # slot 系列：sN、!sN、sN.prop、sN+op
    ("slot",           re.compile(r"^!?s\d+(?:[+\-=~.].*)?$")),
    # focus 操作
    ("focus",          re.compile(r"^focus\.\d+$")),
    # loot 操作（含点号的，裸 loot 是固定 key）
    ("loot_dsl",       re.compile(r"^loot\..*$")),
    # clean 操作（含点号的）
    ("clean",          re.compile(r"^clean\..*$")),
    # sudan_pool 操作
    ("sudan_pool",     re.compile(r"^sudan_pool\..*$")),
    # 弹窗类：pop / hand_pop / think_pop / rite_pop
    ("pop",            re.compile(r"^(?:pop|hand_pop\w*|think_pop\w*|rite_pop)\..*$")),
    # case 分支
    ("case",           re.compile(r"^case:op\d+$")),
    # 公式 / 骰子
    ("formula",        re.compile(r"^[fr]\d*:.*$")),
    # change_card 操作
    ("change_card",    re.compile(r"^change_card_\w+\..*$")),
    # rare 检查
    ("rare",           re.compile(r"^rare[<>=]+\d*$")),
    # 实体操作：ID/名字[.+-=]属性（如 2000082.uprare、妻子+晋升、copy.s3、rebirth.s1）
    ("entity_op",      re.compile(r"^[\u4e00-\u9fff\w]+[+\-=.][\u4e00-\u9fff\w.|]+$")),
    # 属性比较：属性名+比较符（如 智慧>=、魔力<、round<=）
    ("attr_cmp",       re.compile(r"^!?[\u4e00-\u9fff\w]+[<>=]+\d*$")),
    # 否定检查：!词（如 !金币、!怪物、!rite、!雨林！雨林！）
    ("negated",        re.compile(r"^!.+$")),
    # 标签检查：裸中文词（tag.json 中的标签名，如 主角、贵族、追随者、雨林！雨林！）
    ("tag_check",      re.compile(r"^[\u4e00-\u9fff\uff00-\uffef][\u4e00-\u9fff\uff00-\uffef\w]*\d*$")),
]


def classify_dsl_key(key):
    """将单个 key 匹配到 DSL 模式组名，未匹配返回 None"""
    for group_name, pattern in DSL_KEY_PATTERNS:
        if pattern.match(key):
            return group_name
    return None
