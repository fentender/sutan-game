# 性能基线记录

## 阶段 1 性能基线（C++ 核心重构后）

- 测试数据集：44 个 Mod，9345 个 JSON 文件（3900 本体 + 5445 mod）
- Schema 加载时间：0.007s（27 个 schema）
- JSON 解析时间：0.373s（全量 9345 文件，C++ 批量解析，164 失败）
- DataManager.init 完整流程：0.365s（3900 base + 5281 mod 文件）
- Mod 扫描时间：0.374s（44 个 Mod）
- 冲突分析时间：0.455s（3853 个文件）
- apply_delta 真实数据（C++）：1.387s（4225 次，平均 0.328ms/次）
- Delta 初始化时间（SMART）：1.310s（5281 个 delta）
- Delta 初始化时间（ADAPTIVE）：1.408s（5281 个 delta）
- ID 冲突检测 + 重分配：0.087s（10 个 mod 需重分配，102 条消息）
- 完整管线：冲突分析 0.404s + 合并 6.770s = 总 7.174s（3853 个文件）
- ModManager.merge_all_files（缓存路径）：6.986s（3853 个文件）
- MergeCache 首次计算：4.707s（cards.json, 27 mods, 774 field overrides）
- MergeCache 缓存命中：0.040ms/次
- 重叠检测：0.014s（44 mod，30 个有重叠）
- 资源文件复制：2.006s（483 个文件）
- deploy_to_workshop：5.657s（3854 个文件）
- DiffDialog 构造（含 precompute + tab 0）：6.143s
- DiffDialog 后续 tab 切换（26 tab）：总 10.764s / 平均 0.414s / 最慢 1.002s

### Profiler Top 热点（Python 侧）

| 函数 | 调用次数 | 总耗时 | 平均 | 最慢 |
|------|---------|--------|------|------|
| scanner.scan_all_mods | 14 | 15.577s | 1.113s | 1.387s |
| scanner.scan_config_files | 616 | 14.933s | 0.024s | 0.351s |
| delta._process_file_group | 42,383 | 13.155s | 0.000s | 0.131s |
| ModManager._compute_merge_file | 7,708 | 11.102s | 0.001s | 4.707s |
| DiffDialog._load_tab | 27 | 11.067s | 0.410s | 1.002s |
| DataManager.init | 12 | 10.193s | 0.849s | 1.113s |
| merger.merge_all_files | 1 | 6.770s | 6.770s | 6.770s |
| deployer.deploy_to_workshop | 1 | 5.657s | 5.657s | 5.657s |
| DiffDialog._precompute_merge_states | 1 | 5.632s | 5.632s | 5.632s |
| deployer.copy_resources | 1 | 2.006s | 2.006s | 2.006s |
| conflict.analyze_all_overrides | 4 | 1.763s | 0.441s | 0.539s |
| diff._apply_extra_selections | 54 | 1.742s | 0.032s | 0.332s |
| merger.merge_file | 3,853 | 0.682s | 0.000s | 0.039s |
| schema.load_schemas | 1 | 0.007s | 0.007s | 0.007s |

### Profiler Top 热点（C++ 侧）

| 函数 | 调用次数 | 总耗时 | 平均 | 最慢 |
|------|---------|--------|------|------|
| recursive_delta | 1,427,712 | 18223.3ms | 12.8μs | 38245.5μs |
| compute_delta | 58,091 | 8751.7ms | 150.7μs | 38246.5μs |
| state_from_doc | 47,467 | 4915.3ms | 103.6μs | 28108.6μs |
| match_heuristic | 41,789 | 4501.3ms | 107.7μs | 32238.4μs |
| match_phase2_levenshtein | 37,466 | 2067.9ms | 55.2μs | 26149.7μs |
| match_phase1_keys | 37,466 | 2066.8ms | 55.2μs | 18446.5μs |
| apply_delta | 63,433 | 689.0ms | 10.9μs | 3012.8μs |
| match_phase3_similarity | 37,466 | 219.2ms | 5.8μs | 2751.5μs |

### 阶段 0 → 1 对比

| 指标 | 阶段 0 | 阶段 1 | 变化 |
|------|--------|--------|------|
| JSON 解析（9345 文件） | 48.750s | 0.373s | **-99.2%** ⬇ |
| DataManager.init 完整流程 | — | 0.365s | 新增 |
| Mod 扫描 | 1.245s | 0.374s | **-70.0%** ⬇ |
| Delta 初始化（SMART） | 2.375s | 1.310s | **-44.8%** ⬇ |
| Delta 初始化（ADAPTIVE） | — | 1.408s | 新增 |
| ID 冲突检测 + 重分配 | — | 0.087s | 新增 |
| 冲突分析 | 0.401s | 0.455s | +13.5% ⬆ |
| apply_delta 真实数据 | 3.358s (4295×) | 1.387s (4225×) | **-58.7%** ⬇ |
| 完整管线合并 | 4.528s | 6.770s | +49.5% ⬆ |
| ModManager.merge_all_files | — | 6.986s | 新增 |
| MergeCache 首次 | 2.503s | 4.707s | +88.1% ⬆ |
| MergeCache 缓存命中 | ~0.000s | 0.040ms/次 | — |
| 重叠检测 | 0.004s | 0.014s | — |
| 资源文件复制 | — | 2.006s（483 文件） | 新增 |
| deploy_to_workshop | — | 5.657s（3854 文件） | 新增 |
| DiffDialog 构造 | — | 6.143s | — |
| DiffDialog tab 切换 | — | 10.764s / 0.414s avg | — |
| 热点 delta._process_file_group | 265.4s | 13.155s | **-95.0%** ⬇ |
| 热点 apply_delta (C++) | 213.2s | 0.689s | **-99.7%** ⬇ |
| 热点 compute_delta (C++) | 44.2s | 8.752s | **-80.2%** ⬇ |

**核心收益**：JSON 解析、delta 计算、apply_delta 三大热点全部下沉 C++ 后，Python 侧 profiler 总耗时从 ~770s 降至 ~100s。当前瓶颈已从计算逻辑转移到：文件 I/O（scan_config_files 14.9s）、delta 计算（13.2s）、GUI 渲染（DiffDialog 11s）和合并缓存计算（11.1s）。

---

## 阶段 0 性能基线（改造前基准）

- 测试数据集：44 个 Mod，9344 个 JSON 文件（3900 本体 + 5444 mod）
- JSON 解析时间：48.750s（全量 9344 文件）
- Mod 扫描时间：1.245s（44 个 Mod）
- Schema 加载时间：0.004s（27 个 schema）
- Delta 初始化时间：2.375s（5351 个 delta）
- 冲突分析时间：0.401s（3905 个文件）/ 完整管线 0.860s
- 合并总时间：4.528s（3905 个文件）/ 完整管线 2.944s
- apply_dict_delta 真实数据：3.358s（4295 次，平均 0.782ms/次）
- 大对象合并（1000-key ×10）：0.184s
- Delta 缓存命中：168.2 ns/次
- MergeCache 首次计算：2.503s（cards.json, 27 mods, 774 field overrides）
- MergeCache 缓存命中：~0.000s/次
- ID 线性查找（10000 已占用 ×1000）：0.936s
- 重叠检测：0.004s（44 mod，31 个有重叠）
- DiffDict 格式化（4层×10 ×100）：4.043s
- smart_allow_deletion（×100000）：0.016s（162.4 ns/次）

### Profiler Top 热点

| 函数 | 调用次数 | 总耗时 | 平均 |
|------|---------|--------|------|
| delta._process_file_group | 35,145 | 265.4s | 0.008s |
| merger.apply_dict_delta | 157,715 | 213.2s | 0.001s |
| store.parse_file | 14,285 | 123.4s | 0.009s |
| store.load_cached | 84,096 | 90.9s | 0.001s |
| delta.compute_delta | 48,160 | 44.2s | 0.001s |
| merger.apply_field_delta | 386,830 | 10.9s | 0.000s |
| merger.merge_all_files | 2 | 7.5s | 3.736s |
| DataManager.init | 9 | 6.5s | 0.722s |
| parser.clean_json_text | 14,285 | 5.0s | 0.000s |
| parser.dump_json | 7,810 | 4.9s | 0.001s |
