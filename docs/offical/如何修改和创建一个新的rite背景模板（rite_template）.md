# 如何修改和创建一个新的rite背景模板（rite_template）
### 创建一个新的背景模板
- 在mod文件夹的配置地址：\config\rite_template中，新建一个json配置
    - 文件名为8000开头的7位数，需要和模板id一致。mod作者也可根据自己需要调整
    - 建议：可以复制本体中已有的模板json配置，根据需要调整对应的参数
    - 对应的资源地址：
        - mage/rite/template/bg/ 背景图资源地址
        - image/rite/template/fg/  前景图资源地址
        - image/rite/template/slot_bg/ 槽位背景图资源地址
    - 前景和背景的尺寸必须是一致的
        - 尺寸参考：3692x2132
    - 槽位图（槽+槽的边框）的参考尺寸：272x520，槽本身的尺寸为：200x424
    - 模板表结构说明
```json
{
    "id": 8000001, //模板id
    "name": "", //mod作者自己看的备注
    "tips": "",//mod作者自己看的备注
    "bg": "nomal_rite_bg", //模板的层级分为2层，这是背景图的资源名，层级比卡槽低
    "fg": null, //这是前景的资源名，层级比卡槽高，如果没有就填null
    "nomal_slot_bg": "",//弃用参数
    "bg_pos": { //背景图（和前景图作为一个整体）的坐标
        "x": 0,
        "y": 0
    },
    "title_pos": { //rite界面文本框的坐标
        "x": 1585,
        "y": 0
    },
    "slots": { //rite的槽位配置
        "s1": { //槽位1配置，需要说明：这里的s1仅作为模板槽位的名称不是rite的s1,mod作者取个f1也行。
            "is_hide_bg": false,//是否隐藏的边框，只保留槽本身
            "is_set_card_hide_bg": false, //废弃参数
            "slot_bg": null, //可自定义每个槽的槽位图
            "pos": { //槽的坐标
                "x": 766,
                "y": -578
            },  //可以对槽进行比例缩放
            "scale": {
                "x": 1,
                "y": 1
            },
            "rotation_z": 0 //可以对槽进行旋转
        },
        "s2": {
            "is_hide_bg": false,
            "is_set_card_hide_bg": false,
            "slot_bg": null,
            "pos": {
                "x": 1068,
                "y": -578
            },
            "scale": {
                "x": 1,
                "y": 1
            },
            "rotation_z": 0
        }
    }
}
```
### 如何使用创建好的模板
- 在mod文件夹的配置地址：\config\rite_template_mapping.json文件
    - 建议：可以复制本体中已有的json配置，根据需要调整调整配置
    - 新建的模板被rite_template_mapping.json引用，对模板做特化调整，然后rite再引用rite_template_mapping配置的id即可
    - 注意：模板配置的槽位数量不能比仪式本身的槽位需求少
    ![图片](https://docimg6.docs.qq.com/image/AgAABS6GRkdE6R4cyONA-6vJ8TZky5Vr.png?w=575&h=197)
表结构说明
    ```json
"8001002": { //被rite引用的id
        "id": 8001002,//被rite引用的id
        "tips": "自宅-白天通用", //备注
        "template_id": 8000001, //你创建的模板id
        "slot_open": [ //在这里可以针对rite需要特化模板槽位和rite的对应关系
            "s3",//模板名称为s3的槽位和rite中s1对应
            "s2",//模板名称为s2的槽位和rite中s2对应
            "s1",//模板名称为s1的槽位和rite中s3对应
            "s4",//模板名称为s4的槽位和rite中s4对应，以此类推
        ]
    },
```