# 总体架构概览

## 项目简介

「苏丹的游戏」Mod 合并管理器，解决多个 Mod 修改同一 JSON 文件时无法同时启用的问题。以游戏本体文件为基准，按用户指定的优先级逐层执行 JSON 内容级深度合并，最终生成合成 Mod 部署到 Workshop 目录。

## 整体数据流

```
mod_scanner 扫描
    |
    v
JsonStore 批量加载（本体 + 全部 Mod JSON）
    |
    v
ModDelta 差异计算（每个 Mod 相对本体的字段级 delta）
    |
    v
conflict 冲突分析（字段覆盖链、数组潜在冲突检测）
    |
    v
merger 深度合并（逐 Mod 将 delta 应用到全状态 DiffDict）
    |
    v
deployer 部署（输出合成 Mod 至 Workshop 目录）
```

合并操作通过 GUI 的 `MergeWorker`（QThread）异步执行，避免阻塞主线程。

## 目录结构

```
src/
  main.py                    # 应用入口
  config.py                  # 路径常量、用户配置读写
  core/                      # 核心业务逻辑
    infra/                   #   基础设施层（类型、诊断、性能）
      types.py               #     类型别名、delta 数据结构
      diagnostics.py         #     全局诊断信息收集器
      profiler.py            #     可选性能评估模块
    json/                    #   JSON 处理层
      parser.py              #     文本清洗、格式化、写出
      store.py               #     全局 JSON 资源管理器单例
      classify.py            #     JSON 文件三分类
    schema/                  #   Schema 规则层
      loader.py              #     schema 加载与字段定义查询
      generator.py           #     从本体自动生成 schema
      dsl.py                 #     游戏 DSL key 模式识别
    merge/                   #   合并引擎层
      delta.py               #     全局 Delta 缓存管理器
      merger.py              #     核心合并算法（apply_delta / merge_all_files）
      array_match.py         #     数组元素匹配策略
      cache.py               #     合并结果缓存 + 逐步可视化数据
      formatter.py           #     Diff 格式化（行级对齐文本 + 高亮）
      rules.py               #     SMART 模式删除判定规则
    mod/                     #   Mod 管理层
      scanner.py             #     Mod 扫描与元数据读取
      conflict.py            #     冲突检测与覆盖链报告
      overlap.py             #     本体重叠检测
      deployer.py            #     合成 Mod 部署
      id_remap.py            #     ID 冲突检测与重分配
      overrides.py           #     用户覆盖目录管理
    platform/                #   平台集成层
      steam.py               #     Steam ACF/VDF 解析
      history.py             #     历史版本管理与 CAS 路径解析
      updater.py             #     在线更新检查
  gui/                       # GUI 层（PySide6）
    app.py                   #   主窗口，串联所有逻辑
    workers.py               #   后台工作线程（MergeWorker 等）
    widgets/                 #   可复用控件
      code_editor.py         #     代码编辑器控件
    panels/                  #   主窗口面板
      mod_list.py            #     Mod 列表面板
      mod_detail.py          #     Mod 详情面板
      override.py            #     覆盖链面板
      log.py                 #     日志面板
    dialogs/                 #   弹窗对话框
      diff.py                #     Diff 对比对话框
      manual.py              #     手动编辑对话框
      json_fix.py            #     JSON 修复对话框
      deletion.py            #     删除确认对话框
      setup_wizard.py        #     首次配置向导
  accel/                     # C 扩展加速模块
    _fast_json.pyd           #   JSON 解析加速（注释剥离、尾逗号等）
```

## 各层简要说明

### core 层

| 子模块     | 职责                                                         |
| ---------- | ------------------------------------------------------------ |
| `infra`    | 零依赖基础设施：类型定义、诊断信息收集、性能评估             |
| `json`     | JSON 文本清洗、解析、缓存、分类                              |
| `schema`   | Schema 规则加载、自动生成、DSL key 识别                      |
| `merge`    | Delta 计算与缓存、合并算法、数组匹配、格式化、删除规则       |
| `mod`      | Mod 扫描、冲突检测、本体重叠检测、部署、ID 重分配、覆盖管理  |
| `platform` | Steam 本地数据读取、历史版本管理、在线更新检查               |

### gui 层

| 子模块    | 职责                                                     |
| --------- | -------------------------------------------------------- |
| `app`     | 主窗口 `MainWindow`，串联扫描、分析、合并、部署流程      |
| `workers` | QThread 工作线程，异步执行耗时操作                       |
| `widgets` | 可复用 UI 控件（代码编辑器等）                           |
| `panels`  | 主窗口中嵌入的各功能面板                                 |
| `dialogs` | 独立弹窗（Diff 对比、手动编辑、JSON 修复、首次配置向导） |

## 层级依赖关系

```
infra（零依赖）
  ^
  |
json（依赖 infra）
  ^
  |
schema（依赖 infra, json）
  ^
  |
merge（依赖 infra, json, schema）
  ^
  |
mod（依赖 infra, json, merge, schema, platform）
  |
  +--- platform（依赖 infra）

gui（依赖 core 全部子模块 + config）
```

核心原则：
- **infra** 是最底层，不依赖任何其他 core 子模块
- **json** 仅依赖 infra
- **schema** 依赖 infra 和 json
- **merge** 依赖 infra、json、schema
- **mod** 位于最上层，可调用所有核心子模块
- **platform** 仅依赖 infra，是独立的辅助层
- **gui** 位于最外层，组装并驱动所有核心逻辑
