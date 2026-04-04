# 如何修改和创建一个新的地图（game）
### 可修改的内容说明
- 修改和创建新的游戏桌面和地图
- 修改和创建新的特化rite图标
- 修改和创建新的装备槽位和图标
- 修改和创建信的卡牌材质
- 对应的资源需得是.png格式
### 支持修改的关联配置game.json
- 配置地址：\config\game.json
### game表的数据结构说明
```json
{
    //  初始化配置id, 需要确保mod文件夹里\config\init里有对应id的init文件。id中0和1被游戏本体所使用。
    "init": 3,
    //  地图配置，配置和表现的对照关系详见下图示意
    "map": {
        //  桌面图片，图片格式png
        "desktop": "map/desktop",
        //  地图图片，图片格式png
        "map": "map/map1",
        "overwrite":"false",//默认为false，意为着"locations"的配置覆盖游戏本体可用的事件坐标，但如果game.json引用的是init 1则无法覆盖只能新建；配置为true，意为与本体事件坐标合并。
        "locations": {//  新增游戏事件可用的事件坐标
            "position-1": {//  key，区域标识，唯一。mod作者可根据自身需求命名
                //  地点名称
                "name": "商业区", //区域的名称，实际被rite（事件）引用名称
                //  地点图标
                "icon": "map/pos-1", //代表区域的小图标，格式png
                //  图标的坐标，[0,0]代表场景的中心，mod作者根据需要调整对应区域的图标位置
                "x": 0,
                "y": 0,
                "sub_locations": [//对应区域的实际事件坐标
                    {"x": 50, "y": 50},     //  1号位置，在rite配置中引用
                    {"x": 150, "y": 150}    //  2号位置
                    //  ...
                ]
            }
            //  ...
        }
    },
    // 特化的rite图标配置
    "rite_icon": {
        "rite_new_icon.png": {//  rite图标名称为rite_new_icon.png
            //  一般特化的rite图标尺寸各异，所以需要配置图片偏移值来避免遮挡
            "x": -100.0,
            "y": -12.5
        }
        //  ...
    },
    //  与特化rite图标配套的选中效果，在本体里表现为围绕rite图标的光圈  
    "rite_outline_icon": {
        //  选中效果的资源名称为rite_new_icon.png，资源名需要和对照的rite图标名称一致。
        "rite_new_icon.png": {
            //  图片偏移值
            "x": -100.0,
            "y": -12.5
        }
        //  ...
    },
    //  运行玩家自定义装备类型和图标
    "equip_icon": {
        "sth": {//  图标类型标识, 通常对应特定tag的code
            "equip": "equip/sth_equip", //装备时的图标状态
            "unequip": "equip/sth_unequip" //未装备时的图标状态
        }
    },
    //  卡牌材质
    "card_materials": {
        "chars": [ //对角色卡的4中品级的材质进行修改
            {
                //  主图
                "main": "material/stone",
                //  颜色
                "color": [1.0, 1.0, 1.0, 1.0],
                //  金属材质, 不填则后续参数都无效
                "metallic": "material/stone_metallic",
                //  光滑度
                "metallic_smooth": 0.549,
                //  凹凸贴图
                "bump": "material/stone_bump",
                //  凹凸贴图缩放
                "bump_scale": 0.2,
                //  自发光贴图
                "emission": "material/stone_emission",
                //  自发光颜色
                "emission_color": [0.1, 0.3, 0.5]
            },
            {
                "main": "material/copper",
            },
            {
                "main": "material/silver",
            },
            {
                "main": "material/gold",
            }
        ],
"items": [//对物品卡的4中品级的材质进行修改
            {
                "main": "material/stone",
            },
            {
                "main": "material/copper",
            },
            {
                "main": "material/silver",
            },
            {
                "main": "material/gold",
            }
        ],
      "sudans": [//对苏丹卡的4中品级的材质进行修改
          {
              "main": "material/stone",
          },
          {
              "main": "material/copper",
          },
          {
              "main": "material/silver",
          },
          {
              "main": "material/gold",
          }
        ]    
}
```
### 修改和创建新的游戏桌面和地图
- "map"相关的资源地址\image\map，如果没有的话创建一个
![图片](https://docimg10.docs.qq.com/image/AgAABS6GRkc5uDSvqmVBOapBgRll5G8l.png?w=1114&h=425)

- 在game.json配置中"desktop":中配置的资源和本体的对照关系如图
    - 推荐尺寸：4096x2048
![图片](https://docimg5.docs.qq.com/image/AgAABS6GRkfgWKXBwtVPbLUDzygboSKq.png?w=1288&h=716)
![图片](https://docimg10.docs.qq.com/image/AgAABS6GRkcPxzvYq4tDjK_dtO3rGdh2.png?w=1292&h=726)
- 在game.json配置中"map":中配置的资源和本体的对照关系如下图
    - 推荐尺寸：3600x3000
![图片](https://docimg1.docs.qq.com/image/AgAABS6GRkevZzkcZxREhIMo8PrV_QJl.png?w=1286&h=722)
![图片](https://docimg7.docs.qq.com/image/AgAABS6GRke3SLjbquBC3Z5EExnlTMiB.png?w=1281&h=715)
- 在game.json配置中"locations":中"icon"的资源和本体的对照关系如下图
    - 推荐尺寸：400x300
![图片](https://docimg10.docs.qq.com/image/AgAABS6GRkexu-aPaq9JaZ5jOr_2mLuk.png?w=1267&h=717)
![图片](https://docimg2.docs.qq.com/image/AgAABS6GRkei46IOTcxBMqDoj0y3c8Ia.png?w=1168&h=662)
### 修改和创建新的特化rite图标
- 资源地址
    - mod扩展仪式图标, 特指image/rite/xxx.png
    - mod扩展仪式图标外框, 特指image/rite_outline/xxx.png
- 配置特化图标的偏移坐标，需要基于图片自身的原点(0,0)，图片自身原点如下图所示
    - 作为参考，示意图未作偏移时放置的位置和基于图片自身原点配置了偏移(47.6, -50.95)时的位置如下图所示
![图片](https://docimg6.docs.qq.com/image/AgAABS6GRkcsGfLmu9JOqZ8fVUV6Fulq.png?w=406&h=337)
![图片](https://docimg6.docs.qq.com/image/AgAABS6GRkevl9q0R3hHUYpJ8D4mepYx.png?w=747&h=709)
### 修改和创建新的装备槽位和图标
- 资源的mod地址\image\equip，没有的话创建一个，图标显示的位置如下图所示
    - 图片尺寸要求：50x50
![图片](https://docimg8.docs.qq.com/image/AgAABS6GRkd14XD6h8dDT7pl0CR7jOQY.png?w=939&h=386)
### 修改和创建新的卡牌材质
- 相关的资源地址\image\material，如果没有的话创建一个对应的文件夹
- 推荐材质规格：512x512 or 1024x1024
示意图：
![图片](https://docimg3.docs.qq.com/image/AgAABS6GRkf_VSNwb4lCfIE4Yq8ZSDIW.png?w=1036&h=644)