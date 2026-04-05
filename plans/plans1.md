# 一、问题

1.  core/json_parser.py 使用的全局警告日志当前只使用列表，没有全局单例类管理，不便维护。其它文件也有类似情况，缺失全局管理类
2. 缺失mod全局管理类
3. 很多地方兜底太多，按理说应该直接让他报错，让错误暴露，例如：analyze_all_overrides的if base_file.exists()。以及@SuDanGame/scripts/generate_schemas.py#96-97 、@SuDanGame/scripts/generate_schemas.py#274 、@SuDanGame/scripts/generate_schemas.py#240-241 
4. @SuDanGame/src/core/conflict.py#58-60 没有考虑key在本体而不在mod的情况，diff应该找出增加、删除和修改。至于是否使用删除是更上层调用的任务
5. 存疑：@SuDanGame/src/core/conflict.py#57 使用符号"."连接key，但是json文件中有key内部包含"."符号，确认是否会冲突
6. @SuDanGame/src/core/conflict.py#64-68 这种数组比较太粗暴了吧？应该原子化比较，考虑里面的每个元素，要和dict一样设计某种key表示数组中的每个元素。对于base和mod都是数组比较差异，分成两种情况。若数组元素为基本类型，则mod里每个元素都要和base里逐个比较来判断差异（可以用算法优化速度）。若数组元素为array直接报错，若为dict则检查其中是否有"guid"key，有的话基于此判断。否则按顺序对应比较差异，并递归基于dict的差异比较。
并且这里没有考虑这些情况：（1）. 字典：数组，需要直接报错（2）.字典：普通数据，需要直接报错（3）.数组：普通数据。把普通数据当作含有1个元素的数组处理，然后按之前的数组间规则比较差异。最后的比较差异基本单位一定是基本元素类型而不是Object，因此FieldOverride的basevalue类型也要不为object
7. @SuDanGame/scripts/generate_schemas.py 是首次使用程序功能时需要进行的初始化功能，但现在要求用户自己执行不合理。应该改造成程序启动时检查是否已经初始化，若没有则执行该脚本进行初始化
8. 为什么用object不用dict，@SuDanGame/scripts/generate_schemas.py#66 
9. @SuDanGame/scripts/generate_schemas.py#78 若有多个类型，也要把多个类型写上去。并且这里没有考虑dict。这些也要看看是否要更新@SuDanGame/src/core/schema_loader.py#184-197 
10. 为什么有最大深度限制？没必要。@SuDanGame/scripts/generate_schemas.py#126 
11. 采样数不要限制，并且当前似乎没有用到这个key，@SuDanGame/scripts/generate_schemas.py#122-124 。这个@SuDanGame/scripts/generate_schemas.py 里面很多都限制了采样数，都去掉
12. 可能出现两个array<float>@SuDanGame/scripts/generate_schemas.py#159。
13. 不能仅看list就默认这种行为，必须检查里面元素是否是一个普通类型和一个数组类型，否则报错@SuDanGame/scripts/generate_schemas.py#179 
14. 对应dynamic_key之中的字段直接返回None，也就是说使用默认规则。@SuDanGame/src/core/schema_loader.py#127-128 直接替换replace @SuDanGame/src/core/merger.py#445-446 
15. @SuDanGame/src/core/schema_loader.py#165-181 应该和generate_schemas使用同一个函数。
16. 现在的dynamic_key这个东西还是太奇怪了，你可以尝试一下先把所有频率低被判定属于dynamic_key的key和出现次数打印出来，看看是否有什么规律可以统一一下
17. @SuDanGame/src/core/merger.py#509-518，@SuDanGame/src/core/merger.py#481-504  没有考虑删除的情况。@SuDanGame/src/core/merger.py#470 应该作用全局的是否删除
18. 现在代码基本都是面向过程，很少使用类来控制，没有好的架构。现在需要重新设计一下
19. 当前的diff_dialog是只读，现在我希望能够让用户在diff_dialog的右边文本编辑器进行修改并进行保存，并且需要每次重新打开应用时这个修改都是一样的，也就是得持久化，并在GUI中加一个按键重置回默认的修改文本，（并且文本编辑器希望能用ctrl+z撤销，这个如果困难就暂时不实现）。目前的想法是将每个mod的修改内容持久化保存到项目一个文件夹中，而不是像现在这样每次重新计算。这样用户可以通过修改此内容实现。