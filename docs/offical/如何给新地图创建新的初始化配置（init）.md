# 如何给新地图创建新的初始化配置（init）
### 创建一个非0和1的init配置
- 在mod文件夹的配置地址：\config\init中，新建一个json配置，如下图所示
![图片](https://docimg5.docs.qq.com/image/AgAABS6GRkfYH63D5RdMYLnveahjzEck.png?w=989&h=229)
- json配置文件名和init的id名需要一致，0和1已被游戏本体占用，无法被修改和使用
    - 0代表苏丹玩苏丹卡的初始化配置，1代表玩家正式开始游戏的初始化配置
    - mod作者如果创建了一个非0和1的init配置，并且在game.json中被引用，那么就会自动顶替掉游戏本体init1的初始化配置（如果没在game.json中引用就无用）
- 建议：玩家可以复制本体1的json配置，基于本体1的配置结构，根据自身需求修改配置。
    - 如果mod作者完全不需要继承1的任何配置，那么只用保留init的基础参数就可以了，如下图所示
```json
{
    "id": 3,
    "name": "测试游戏",
	"show_deadline": false, //关闭死亡倒计时UI
    "show_story": false, //关闭一千零一夜UI
    "show_ithink": false, //关闭俺寻思UI
    "show_prestige": false, //关闭声望UI
    "sudan_box_show_after_wizard": true, //关闭牌盒UI
    "sudan_pool": [
    ]
}
```
- init表结构的简单说明
```json
{
    "id": 1, //init的id
    "name": "test", //给mod作者自己看的备注
    "wizard": "wizard",//wizard负责提供女术士的配置, 指向wizard目录里的对应文件名
    "show_deadline": true, //是否显示死亡倒计时UI
    "show_story": true, //是否显示一千零一夜UI
    "show_ithink": true, //是否显示俺寻思UI
    "show_prestige": true, //是否显示声望UI
    "disable_auto_gen_sudan_card": true, //true关闭自动发牌，默认是false
    "sudan_box_show_after_wizard": true, //true关闭牌盒UI（自动发牌关闭才会生效），默认是false
    "cards": [//无用参数
    ],
    "single_dice_face_weight": [ //无用参数
        100,100,100,100,100,100
    ],
    "gold_dice_count": 0,//无用参数
    "back_to_prev_round_count": 9999,//无用参数
    "sudan_life_time": 7,//无用参数
    "difficulty": [ //难度设置
        {
            "name": "梅姬",
            "title": "简单",
            "bg":"diff/1", //可以替换当前难度的插图
            "desc": "梅姬.....",//难度描述
            "single_dice_face_weight": [100,100,100,100,300,300],//骰子1~6的权重分布
            "sudan_redraw_times_per_round": 3, //默认重投次数
            "gold_dice_count": 3,//默认金骰子数量
            "back_to_prev_round_count": 9999, //默认返回上一回合的次数
            "sudan_life_time": 7 //苏丹卡可以持有的天数
        },
        {
            "name": "哈桑",
            "title": "普通"
            "bg":"diff/2", //可以替换当前难度的插图
            "desc": "哈桑.......",
            "single_dice_face_weight": [100,100,100,100,200,200],
            "sudan_redraw_times_per_round": 1,
            "gold_dice_count": 2,
            "back_to_prev_round_count": 10,
            "sudan_life_time": 7
        },
        {
            "name": "女术士",
            "title": "困难",
            "bg":"diff/3", //可以替换当前难度的插图
            "desc": "女术.......",
            "single_dice_face_weight": [150,150,150,150,200,200],
            "sudan_redraw_times_per_round": 1,
            "gold_dice_count": 1,
            "back_to_prev_round_count": 0,
            "sudan_life_time": 5
        },
    ],
    //  俺寻思关联的rite的id, 不配置或不存在就不显示
    "think_id": 5000002,
    //  俺寻思是否显示结算界面
    "think_show_settlement": false,
    //  苏丹卡池, 可以重复id
    "sudan_pool": [
        2010001,
        2010001,
		//.....
    ],
    "sudan_card_desc":{ //抽到对应苏丹卡女术士的描述
        "2010001":[
            "一张岩石杀戮卡！.....",
        ],
        "2010002":[
            "一张青铜杀戮卡！....",
        ],
		//......
    },
    "sudan_pool_desc":[//无用参数
    ],
    //  是否洗牌, 洗牌会打乱苏丹卡池顺序, 不洗牌就不打乱
    "sudan_shuffle": true,
    //  苏丹卡洗回重发张数
    "sudan_redraw_count": 1,
    //  苏丹卡每回合可洗回次数
    "sudan_redraw_times_per_round": 1,
    "sudan_redraw_times_recovery_round":7,
    "default_cards":[//默认生成的卡牌
        2000001,
        2000005,
        2000006,
		//......
    ],
    "card_equips": { //给默认生成的卡牌添加默认装备
        "2000024": [
            2000529
        ],
        "2000328": [
            2000098
        ],
        "2000061": [
            2000330
        ],
        "2000791":[
            2000529
        ]
    },
    "default_rite": [
        //5000001,  //治理家业
        //5001001,  //权力的游戏
        //5001501,  //浴场里的消息
        //5002001,  //医馆
        //5001001  //权力的游戏
    ]
}
```
### 创建初始化激活的event事件
- 举例：如果mod作者希望event事件在init 3 初始化的时候激活，那么可以如下配置
```json
{
    "id": 5310001,
    "text": "开端",
    "is_replay": 0, 
    "auto_start_init": [3], //代表这个event将在init 3的
    "start_trigger": true, 
    "on":{   
        "round_begin_ba": 1
    },
    "condition":{  
    },
    "settlement": [
	    "tips_resource":"", 
	        "tips_text":"",
	        "action":{
	                "clean.rite":1, //移除所有仪式
	                "event_off":1, //关闭所有已激活的event
	                "table.clean.char|主角=0":0, 
                	//移除所有初始化生成的没有主角tag的char类型的卡牌
	                "table.clean.item":0, //移除所有初始化生成的物品卡
	                "total.sudan+冻结":1, //冻结苏丹卡，苏丹卡不再倒计时
	                "sudan_pool.sudan+冻结":1, //冻结排盒子里的苏丹卡
	                "enable_auto_gen_sudan_card": false, //禁用自动生成抽卡
                	"close_box":1,//关闭牌盒UI
	        }
        ]
    }
```
### 创建初始化的女术士配置
- 在mod文件夹的配置地址：\config\wizard中，新建一个json配置，配置被init引用
- json文件名需要与id一致
简单的表结构说明
```json
{
    "id": "WIZARD", //id，与文件名一致
    "prompt_draw_sudan_start": "<size=85%><i>（她将那只木匣捧到了你的面前。）</i></size>\n请点击牌盒，抽取您的下一张卡牌吧，阁下。", //每次抽卡时女术士的通用文本
    "prompt_draw_sudan_start_first": "阁下，您对苏丹卡应该不陌生吧，如果需要的话，我可以单独为您讲解。那么现在......\n\n<size=85%><i>（她将那只木匣捧到了你的面前。）</i></size>\n抽取你的第一张苏丹卡吧。", //第一次抽卡时女术士的文本
    "prompt_draw_sudan_end_with_times": "{0}如果你感到不满意的话，今天你还有{1}次更换的机会。", //抽完卡后女术士的文本
    "prompt_draw_sudan_end_without_times": "{0}",
    "name": "女术士",
    "text": "尊贵的玩家……有什么我可以效劳的吗？", //打开卡盒时女术士的文本
    "options": [ //打开卡盒后显示的选项
        {
            "name": "交换手上的苏丹卡", //选项文本
            "text": "这7日你有[sudan_redraw_total_left_times]次机会可以把手上的一张苏丹卡还给我，重新再抽一张随机的苏丹卡，但，苏丹的倒计时不会改变哦，请千万注意嘻嘻嘻……请将苏丹卡放回这个宝盒吧。", //选项描述
            "op": "redraw", //点击当前选项的附带动作，
					        //"redraw" 苏丹卡重抽
							//"draw" 额外抽苏丹卡
							//"back" 返回上一级
							//"show_left_sudan_card" 显示剩余苏丹卡
							//"how_long_alive" 存活多久了
							//"close" 关闭女术士界面
            "options": [ //次级菜单
                {
                    "name":"返回",
                    "op":"back"
                }
            ]
        },
        {
            "name": "疯狂到底, 再来一张苏丹卡",
            "text": "请点击牌盒，抽取您的卡牌吧，阁下。",
            "op": "draw",
            "options": [
                {
                    "name":"返回",
                    "op":"back"
                }
            ]
        },
        {
            "name": "和她聊聊",
            "text": "尊贵的玩家……有什么我可以效劳的吗？",
            "options": [
                {
                    "condition":{  //条件
                    },
                    "name":"你是谁",               //次级选项名称
                    "dt":"DT5",             //从dt文件夹中获取文本，可实现对话式的文本，结构说明在下方有说明
                    "action":{               //点击对应选项执行的动作
                    }
                },
                {
                    "condition":{  //条件
                    },
                    "name":"游戏的诀窍",               //次级选项名称
                    "dt":"DT6",
                    "action":{               //点击对应选项执行的动作
                    }
                },
                {
                    "name":"没什么想问的",
                    "op":"back"
                }
            ]
        },
        {
            "name":"进度显示",
            "text":"我的时间永远充裕，你有什么想问的么",
            "op": "show_left_sudan_card",
          "options": [
					{
                        "name":"返回",
                        "op":"back"
                    }
            ]
        },
        {
            "name":"离开",
            "text":"谢谢阁下，再见。",
            "op":"close"
        }
    ]
}
```
- 关于dt的结构说，在mod文件夹的配置地址：\config\dt中
    - dt文件名和dt的id一致
```json
{
    "dialog_tree_id" : "DT5", //dt的id
    "first_word_id" : "S1", //第一句话从哪个"word_id"的配置开始播放
    "description" : "魔法师交谈1-你是谁",//给作者自己看的
    "Item" : [//根据需求配置对话
        {
            "word_id" : "S1", //句子的编号
            "word" : "我是帮助苏丹们——所有有资格玩这个游戏的王者管理游戏的主持人。现在，我也是您谦卑的仆人，衷心期待您也能获得足够的消遣。",//句子文本
            "jump_type" : "3", //3代表对话结束，0代表跳转到"direct_id"配置的句子中
            "direct_id" : "",
            "Option" : [
            ],
            "action" : {}
        },
        {
            "word_id" : "S2",
            "word" : "游戏现在还在开发与测试阶段，可能无法获得【大胜】，但您仍然可以尽可能的消耗苏丹卡来探索隐藏的故事。",
            "jump_type" : "0", 
            "direct_id" : "S3",
            "Option" : [
            ],
            "action" : {}
        },
        {
            "word_id" : "S3",
            "word" : "在某些特殊的故事线中，您不需要完成所有苏丹卡，也可以获得某种【不错的结局】。",
            "jump_type" : "3",
            "direct_id" : "",
            "Option" : [
            ],
            "action" : {}
        }
        ]
    }
```
### 如何修改难度界面的插图
- "难度插图"相关的资源地址\image\diff，如果没有的话创建一个
- 在init配置中添加对应的插图配置，插图资源格式为png
```json
"difficulty": [ //难度设置
        {
            "name": "梅姬",
            "title": "简单",
            "bg":"diff/1", //可以替换当前难度的插图
            "desc": "梅姬.....",
            "single_dice_face_weight": [100,100,100,100,300,300],
            "sudan_redraw_times_per_round": 3,
            "gold_dice_count": 3,
            "back_to_prev_round_count": 9999, 
            "sudan_life_time": 7 
        },
        {
            "name": "哈桑",
            "title": "普通"
            "bg":"diff/2", //可以替换当前难度的插图
            "desc": "哈桑.......",
            "single_dice_face_weight": [100,100,100,100,200,200],
            "sudan_redraw_times_per_round": 1,
            "gold_dice_count": 2,
            "back_to_prev_round_count": 10,
            "sudan_life_time": 7
        },
        {
            "name": "女术士",
            "title": "困难",
            "bg":"diff/3", //可以替换当前难度的插图
            "desc": "女术.......",
            "single_dice_face_weight": [150,150,150,150,200,200],
            "sudan_redraw_times_per_round": 1,
            "gold_dice_count": 1,
            "back_to_prev_round_count": 0,
            "sudan_life_time": 5
        },
    ],
```
显示尺寸4096x2152
插图参考布局：需要留有空白处，否则文字会被遮挡，大约2：1的比例
![图片](https://docimg5.docs.qq.com/image/AgAABS6GRkcR_TAVlPpLiK_UxeMPtQnf.png?w=990&h=540)