# 性能基线记录

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
