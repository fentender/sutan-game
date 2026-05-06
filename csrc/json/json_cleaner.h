#pragma once
#include <string>

namespace sultan {

// 修复对象内相邻键值对之间缺失的逗号
std::string fix_missing_commas(const std::string& text);

// 压缩字符串外的连续逗号（,,, → ,）
std::string strip_duplicate_commas(const std::string& text);

// 统一清洗入口：fix_missing_commas → strip_duplicate_commas
// 注释和尾随逗号由 yyjson flag 原生处理，此处不含
std::string clean_text(const std::string& text);

}  // namespace sultan
