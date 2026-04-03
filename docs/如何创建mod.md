# 如何创建mod文件
写在最前面：
所有上传的mod文件数据最好仅包含自己修改过的文件或id，这样可以大幅度减少与其他mod之间的冲突
#### Mod文件地址
- mod根目录位于【我的文档\DoubleCross\SultansGame\Mod】
- 任意mod都需要是一个文件夹, 其中包含
    Info.json 模组说明文件(必须
    preview.jpg 模组预览图片(可选, 可以是jpg, png
    config 配置文件目录(可选
    bgm 背景音乐目录(可选
    image 图片资源目录(可选
![图片](https://docimg8.docs.qq.com/image/AgAABS6GRkcNTer82XlCsbUZ55Qqa3kY.png?w=899&h=410)
#### Mod文件说明
##### info.json 说明
数据结构
```json
{
    "name": "命运商店不花钱", //是mod的显示名称, 最大128字节
    "description": "命运商店的价格调成0", //是mod的描述内容, 最大8000字节
    "tags": ["Utilities","Numerical Tuning"], 
    //当前mod的标签，方便玩家在steam中检索
    //似乎只能使用英文字母才会生效
    "version": "1.0.0" //保留，但暂时不使用
}
```
准备的几个标签，方便大家取用
Alternative Storyline 剧情
Numerical Tuning 数值修改
Balance 难度调整
Utilities 便利功能
Original Creation 原创内容
Appearance 立绘修改
Characters 新增角色
Equipment 装备
Romantic Content 浪漫内容
##### Preview.jpg 说明
Preview是用于显示工坊物品的预览图, 可以用jpg或png, 但要求文件大小不超过1MB
如下图
![图片](https://docimg8.docs.qq.com/image/AgAABS6GRkekt1sxM2ZD76PHYdsnlIqd.png?w=544&h=274)
##### config 目录说明
Config目录是存放mod配置文件的目录, 可改写以下文件, 均为可选, 不配置则无效果
如下图
![图片](https://docimg5.docs.qq.com/image/AgAABS6GRkcwxlsD5npDk6OvPP_BGWqs.png?w=696&h=455)
- after_story 后日谈角色配置, 每个卡牌id一个json文件, 按需添加
- event 幕后事件配置, 每个事件id一个json文件, 按需添加, event_id越小, 初始化的触发时机越早
- loot 掉落配置, 每个掉落id一个json文件
- rite 事件配置, 每个事件id一个json文件
- cards.json 卡牌配置, 可包含必要的修改/新增卡牌内容
- over.json 结局配置, 可包含必要的修改/新增结局内容
- quest.json 一千零一夜配置, 可包含必要的修改/新增内容
- tag.json 标签配置, 可包含必要的修改/新增内容, 特别说明, 用于覆盖的tag, 要求tag的name和原tag的name一样, 否则会出错
- upgrade.json 命运商店配置, 可包含必要的修改/新增内容
- over_music_config.json 结局音乐配置, 可包含必要的修改/新增内容
    - 数据结构
```json
{
  "1000": {// 结局(over)id
    "clip": "over_game_1000", // 音频名称
    "start": 0, // 音频开始播放的位置（单位秒）
    "loop_start": 0, // 音频循环播放开始的位置（单位秒）
    "loop_end": -1 // 音频循环播放结束的位置（单位秒），配置-1表示音频的最后一秒
  },
}
```
- sfx_config.json 部分bgm音乐配置
除sfx_config.json外, 都是覆盖+新增ID模式, 即对应目录下的id内容会尝试覆盖现有的id内容, 如果id不存在则追加。
###### sfx_config.json 特别说明
- json文件内可配置main_game_loop，main_game_loop_difficulty，settle_loop，settle_loop_difficulty，armageddon_music_loop
    - 分别对应不同情景下bgm的配置
    - main_game_loop，main_game_loop_difficulty，settle_loop，settle_loop_difficulty仅支持修改覆盖，不支持新增
    - armageddon_music_loop支持修改和新增
- main_game_loop
    - 用来设置梅姬（简单）和诗人（普通）难度的主界面背景音乐
    - id7~1代表当前处刑日天数的背景音乐
    - 如下图所示
        - 5代表处刑日剩余5天时的背景音乐, 使用 main_game_level2, 最初从start开始播放, 播放到loop_end时跳回到loop_start继续循环播放,
        - 特别的, 当loop_end为-1时表明播放结束循环, 下同
![图片](https://docimg8.docs.qq.com/image/AgAABS6GRkfWdaBqiqBOdrmJytET06uP.png?w=513&h=460)
- main_game_loop_difficulty
    - 用来设置女术士（困难）难度的主界面背景音乐配置
    - 配置方式同上
- settle_loop
    - 用来设置梅姬和诗人难度点击下一天结算时的背景音乐配置
    - 配置方式同上
- settle_loop_difficulty
    - 用来设置女术士难度的主界面背景音乐配置
    - 配置方式同上
- armageddon_music_loop
    - 特殊仪式结束后的背景音乐, 当对应仪式结束后播放
```json
{
	"armageddon_music_loop": {
        "5910000":{ //仪式id
            "clip": "bgm_5910000",
            "start": 0,
            "loop_start": 0,
            "loop_end": -1
        },
    }
```
- Mod作者替换该文件内容时, 请参考现有配置完整复制后, 调整每个时期的clip, start, loop_start, loop_end, 以匹配音轨的具体播放时长和内容, 可只替换单个时机, 例如
    - 该修改只调整第三个难度默认的背景音乐, 剩余3天和剩余1天时的背景音乐, 音轨资源替换成了main_game_new
![图片](https://docimg1.docs.qq.com/image/AgAABS6GRkfIwT6YKl5EI5QsdFE1Thdv.png?w=342&h=291)
##### bgm 目录说明
- Bgm目录存放用于替换的背景音乐资源, 可以直接放置支持的同名音乐文件替换游戏内的现有资源， 支持.wav， .mp3，.ogg音乐文件。 文件名即为配置实际使用的音轨名称，忽略文件夹路径， 所以请勿在多个目录下放置同名的音频文件
- 以下为游戏当前用到的音乐名称
    maker_team 开发组背景音乐
    start_game 启动界面背景音乐
    drawcard 女术士抽卡背景音乐
    tutorial_main_game 新手引导主游戏背景音乐
    tutorial_draw_card 新手引导抽卡背景音乐
    tutorial_settlement 新手引导结算背景音乐
其他可修改的音乐文件名称可以在sfx_config.json和over_music_config.json中查询
目前暂不支持卡牌的音效配置调整
##### image 目录说明
- image目录存放用于替换的图片资源，均要求使用png格式的图片资源
![图片](https://docimg1.docs.qq.com/image/AgAABS6GRkelyEE6t_ROuJdAyyJYDD8T.png?w=653&h=292)
- head 用于卡牌头像资源, 需求 92x92 像素, 以卡牌id为文件名
![图片](https://docimg7.docs.qq.com/image/AgAABS6GRkfYDBwM75xKsaLXJWwlG-Bh.png?w=445&h=167)
- rite 用于仪式地点图标资源, 需求156x174 像素, 可根据需要命名, 在仪式文件中修改icon为对应的资源名称即可
- tag 用于tag图标资源, 需求150x150像素, 可根据需要命名, 在tag文件中修改resource
为对应的资源名称即可
- cards用于卡牌卡面资源, 需求472x1028像素, 可根据需要命名, 在cards文件中修改resource为对应的资源路径+资源名称即可，如下图红框所示
![图片](https://docimg9.docs.qq.com/image/AgAABS6GRketNVBVQJdFVoohJaXUqXJY.png?w=1078&h=531)
- 如果需要其他资源也可以按对应目录创建后修改替换