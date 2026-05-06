#include "json_cleaner.h"

namespace sultan {

// ── 辅助函数 ──

static bool is_ws(char c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

static size_t skip_ws(const char* text, size_t pos, size_t n) {
    while (pos < n && is_ws(text[pos]))
        pos++;
    return pos;
}

// 跳过完整引号字符串（含转义），i 指向开引号
static size_t skip_string(const char* text, size_t i, size_t n) {
    i++;  // 跳过开引号
    while (i < n) {
        if (text[i] == '\\') {
            i += 2;
        } else if (text[i] == '"') {
            return i + 1;
        } else {
            i++;
        }
    }
    return i;
}

// 检查 pos 是否为 "key": 模式
static bool is_key_start(const char* text, size_t pos, size_t n) {
    if (pos >= n || text[pos] != '"')
        return false;
    size_t k = pos + 1;
    while (k < n) {
        if (text[k] == '\\') {
            k += 2;
        } else if (text[k] == '"') {
            k++;
            break;
        } else {
            k++;
        }
    }
    if (k > n) return false;
    // 跳过闭合引号后的空白
    while (k < n && is_ws(text[k]))
        k++;
    return k < n && text[k] == ':';
}

// 如果 pos 之后下一个非空白是 key 开头，向 out 插入逗号
static void try_insert_comma(const char* text, size_t pos, size_t n,
                             std::string& out) {
    size_t j = skip_ws(text, pos, n);
    if (j < n && is_key_start(text, j, n))
        out += ',';
}

// 将完整引号字符串从 text[i] 复制到 out，返回 text 中的新位置
static size_t copy_string(const char* text, size_t i, size_t n,
                          std::string& out) {
    out += text[i++];  // 开引号
    while (i < n) {
        char c = text[i];
        if (c == '\\') {
            out += c; i++;
            if (i < n) { out += text[i]; i++; }
        } else if (c == '"') {
            out += c; i++;
            break;
        } else {
            out += c; i++;
        }
    }
    return i;
}

// 检查 pos 处是否为标识符（字母或数字）
static bool is_ident_char(char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
           (c >= '0' && c <= '9');
}

// 尝试匹配关键字（true/false/null），成功则输出到 out 并前进 i，返回 true
static bool try_match_keyword(const char* s, size_t& i, size_t n,
                              const char* keyword, size_t len,
                              std::string& out) {
    if (i + len > n) return false;
    for (size_t k = 0; k < len; ++k) {
        if (s[i + k] != keyword[k]) return false;
    }
    if (i + len < n && is_ident_char(s[i + len])) return false;
    out.append(keyword, len);
    i += len;
    return true;
}

// ── 公共接口 ──

std::string fix_missing_commas(const std::string& text) {
    const char* s = text.data();
    size_t n = text.size();

    std::string out;
    out.reserve(n + n / 8);

    size_t i = 0;
    while (i < n) {
        char ch = s[i];

        if (ch == '"') {
            i = copy_string(s, i, n, out);
            try_insert_comma(s, i, n, out);

        } else if (ch == ']' || ch == '}') {
            out += ch; i++;
            try_insert_comma(s, i, n, out);

        } else if ((ch >= '0' && ch <= '9') ||
                   (ch == '-' && i + 1 < n && s[i + 1] >= '0' && s[i + 1] <= '9')) {
            out += ch; i++;
            while (i < n && ((s[i] >= '0' && s[i] <= '9') ||
                             s[i] == '.' || s[i] == 'e' || s[i] == 'E' ||
                             s[i] == '+' || s[i] == '-')) {
                out += s[i]; i++;
            }
            try_insert_comma(s, i, n, out);

        } else if (try_match_keyword(s, i, n, "true", 4, out) ||
                   try_match_keyword(s, i, n, "false", 5, out) ||
                   try_match_keyword(s, i, n, "null", 4, out)) {
            try_insert_comma(s, i, n, out);

        } else {
            out += ch; i++;
        }
    }

    return out;
}

std::string strip_duplicate_commas(const std::string& text) {
    const char* s = text.data();
    size_t n = text.size();

    std::string out;
    out.reserve(n);

    size_t i = 0;
    while (i < n) {
        char ch = s[i];

        if (ch == '"') {
            // 字符串内原样复制
            i = copy_string(s, i, n, out);

        } else if (ch == ',') {
            // 输出一个逗号，跳过后续的空白+逗号混合序列
            out += ',';
            i++;
            while (i < n) {
                if (is_ws(s[i])) {
                    out += s[i]; i++;
                } else if (s[i] == ',') {
                    i++;  // 跳过多余逗号
                } else {
                    break;
                }
            }

        } else {
            out += ch; i++;
        }
    }

    return out;
}

std::string clean_text(const std::string& text) {
    return strip_duplicate_commas(fix_missing_commas(text));
}

}  // namespace sultan
