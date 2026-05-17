#include "json_cleaner.h"

#include <stdexcept>
#include <string>

namespace sultan {
namespace {

// ═══════════════════════════════════════════════════════════
// UTF-8 引号族匹配
// ═══════════════════════════════════════════════════════════

// " U+201C = E2 80 9C, " U+201D = E2 80 9D, ＂ U+FF02 = EF BC 82
static size_t matchDoubleQuoteLike(const char* s, size_t i, size_t n) {
    if (i >= n) return 0;
    if (s[i] == '"') return 1;
    if (i + 2 < n) {
        auto a = static_cast<unsigned char>(s[i]);
        auto b = static_cast<unsigned char>(s[i + 1]);
        auto c = static_cast<unsigned char>(s[i + 2]);
        if (a == 0xE2 && b == 0x80 && (c == 0x9C || c == 0x9D)) return 3;
        if (a == 0xEF && b == 0xBC && c == 0x82) return 3;
    }
    return 0;
}

static bool isAsciiDoubleQuote(const char* s, size_t i, size_t n) {
    return i < n && s[i] == '"';
}

// ' U+2018 = E2 80 98, ' U+2019 = E2 80 99, ` U+0060, ´ U+00B4 = C2 B4
static size_t matchSingleQuoteLike(const char* s, size_t i, size_t n) {
    if (i >= n) return 0;
    if (s[i] == '\'' || s[i] == '`') return 1;
    if (i + 1 < n) {
        auto a = static_cast<unsigned char>(s[i]);
        auto b = static_cast<unsigned char>(s[i + 1]);
        if (a == 0xC2 && b == 0xB4) return 2;
    }
    if (i + 2 < n) {
        auto a = static_cast<unsigned char>(s[i]);
        auto b = static_cast<unsigned char>(s[i + 1]);
        auto c = static_cast<unsigned char>(s[i + 2]);
        if (a == 0xE2 && b == 0x80 && (c == 0x98 || c == 0x99)) return 3;
    }
    return 0;
}

static bool isAsciiSingleQuote(const char* s, size_t i, size_t n) {
    return i < n && s[i] == '\'';
}

static size_t matchQuote(const char* s, size_t i, size_t n) {
    size_t dl = matchDoubleQuoteLike(s, i, n);
    if (dl > 0) return dl;
    return matchSingleQuoteLike(s, i, n);
}

// TS: isEndQuote 选择策略
enum class EndQuoteMode {
    AsciiDouble,     // 只有 ASCII " 能关闭
    AsciiSingle,     // 只有 ASCII ' 能关闭
    DoubleQuoteLike, // 任何 double-quote-like 能关闭
    SingleQuoteLike  // 任何 single-quote-like 能关闭
};

static EndQuoteMode determineEndQuoteMode(const char* s, size_t i, size_t n) {
    if (isAsciiDoubleQuote(s, i, n)) return EndQuoteMode::AsciiDouble;
    if (isAsciiSingleQuote(s, i, n)) return EndQuoteMode::AsciiSingle;
    if (matchSingleQuoteLike(s, i, n) > 0) return EndQuoteMode::SingleQuoteLike;
    return EndQuoteMode::DoubleQuoteLike;
}

static size_t matchEndQuote(EndQuoteMode mode, const char* s, size_t i, size_t n) {
    switch (mode) {
    case EndQuoteMode::AsciiDouble:
        return isAsciiDoubleQuote(s, i, n) ? 1 : 0;
    case EndQuoteMode::AsciiSingle:
        return isAsciiSingleQuote(s, i, n) ? 1 : 0;
    case EndQuoteMode::DoubleQuoteLike:
        return matchDoubleQuoteLike(s, i, n);
    case EndQuoteMode::SingleQuoteLike:
        return matchSingleQuoteLike(s, i, n);
    }
    return 0;
}

// ═══════════════════════════════════════════════════════════
// 字符分类 — 严格对照 TS stringUtils.js
// ═══════════════════════════════════════════════════════════

static bool isWhitespace(char c) {
    return c == ' ' || c == '\n' || c == '\t' || c == '\r';
}

static size_t matchSpecialWhitespace(const char* s, size_t i, size_t n) {
    if (i >= n) return 0;
    if (i + 2 < n) {
        auto a = static_cast<unsigned char>(s[i]);
        auto b = static_cast<unsigned char>(s[i + 1]);
        auto c = static_cast<unsigned char>(s[i + 2]);
        if (a == 0xE3 && b == 0x80 && c == 0x80) return 3;
        if (a == 0xE2 && b == 0x80 && c >= 0x80 && c <= 0x8B) return 3;
        if (a == 0xE2 && b == 0x80 && (c == 0xA8 || c == 0xA9)) return 3;
        if (a == 0xEF && b == 0xBB && c == 0xBF) return 3;
        if (a == 0xE2 && b == 0x80 && c == 0xAF) return 3; // narrow no-break space
        if (a == 0xE2 && b == 0x81 && c == 0x9F) return 3; // medium mathematical space
    }
    if (i + 1 < n) {
        auto a = static_cast<unsigned char>(s[i]);
        auto b = static_cast<unsigned char>(s[i + 1]);
        if (a == 0xC2 && b == 0xA0) return 2;
        if (a == 0xC6 && b == 0x8E) return 2; // mongolian vowel separator
    }
    return 0;
}

static bool isHex(char c) {
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}

static bool isDigit(char c) { return c >= '0' && c <= '9'; }

// TS: isDelimiter includes ',:[]/{}()\n+'
static bool isDelimiter(char c) {
    return c == ',' || c == ':' || c == '[' || c == ']' || c == '/' ||
           c == '{' || c == '}' || c == '(' || c == ')' || c == '\n' || c == '+';
}

// TS: isUnquotedStringDelimiter includes ',[]/{}\n+'
static bool isUnquotedStringDelimiter(char c) {
    return c == ',' || c == '[' || c == ']' || c == '/' || c == '{' ||
           c == '}' || c == '\n' || c == '+';
}

static bool isControlCharacter(char c) {
    return c == '\n' || c == '\r' || c == '\t' || c == '\b' || c == '\f';
}

// TS: isStartOfValue — alpha, number, minus, [, {, or any quote
static bool isStartOfValue(const char* s, size_t i, size_t n) {
    if (i >= n) return false;
    char c = s[i];
    if (c == '[' || c == '{') return true;
    if (c == '-') return true;
    if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_') return true;
    if (c >= '0' && c <= '9') return true;
    if (matchQuote(s, i, n) > 0) return true;
    return false;
}

// TS: isFunctionNameCharStart
static bool isFunctionNameCharStart(char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_' || c == '$';
}

// TS: isFunctionNameChar
static bool isFunctionNameChar(char c) {
    return isFunctionNameCharStart(c) || (c >= '0' && c <= '9');
}

// ═══════════════════════════════════════════════════════════
// 字符串操作 — 严格对照 TS stringUtils.js
// ═══════════════════════════════════════════════════════════

// TS: insertBeforeLastWhitespace
static std::string insertBeforeLastWhitespace(const std::string& text, char ch) {
    size_t idx = text.size();
    if (idx == 0 || !isWhitespace(text[idx - 1])) {
        return text + ch;
    }
    while (idx > 0 && isWhitespace(text[idx - 1])) {
        idx--;
    }
    return text.substr(0, idx) + ch + text.substr(idx);
}

static std::string insertBeforeLastWhitespace(const std::string& text, const char* s) {
    size_t idx = text.size();
    if (idx == 0 || !isWhitespace(text[idx - 1])) {
        return text + s;
    }
    while (idx > 0 && isWhitespace(text[idx - 1])) {
        idx--;
    }
    return text.substr(0, idx) + s + text.substr(idx);
}

// TS: stripLastOccurrence — uses lastIndexOf (full string search)
static std::string stripLastOccurrence(const std::string& text, char ch) {
    size_t pos = text.rfind(ch);
    if (pos == std::string::npos) return text;
    return text.substr(0, pos) + text.substr(pos + 1);
}

// ═══════════════════════════════════════════════════════════
// 转义字符映射
// ═══════════════════════════════════════════════════════════

static bool isEscapeChar(char c) {
    return c == '"' || c == '\\' || c == '/' || c == 'b' || c == 'f' ||
           c == 'n' || c == 'r' || c == 't';
}

static const char* controlCharEscape(char c) {
    switch (c) {
    case '\b': return "\\b";
    case '\f': return "\\f";
    case '\n': return "\\n";
    case '\r': return "\\r";
    case '\t': return "\\t";
    default: return nullptr;
    }
}

// ═══════════════════════════════════════════════════════════
// JsonRepairer — 严格翻译 jsonrepair.js
// ═══════════════════════════════════════════════════════════

class JsonRepairer {
    const char* s_;
    size_t n_;
    size_t i_ = 0;
    std::string output_;

public:
    explicit JsonRepairer(const char* data, size_t len)
        : s_(data), n_(len) {
        output_.reserve(len + len / 8);
    }

    std::string repair() {
        const bool processed = parseValue();
        if (!processed) {
            throwUnexpectedEnd();
        }

        // TS: parseCharacter(',') at top level
        bool processedComma = parseCharacter(',');
        if (processedComma) {
            parseWhitespaceAndSkipComments();
        }

        if (processedComma) {
            // repair: remove trailing comma
            output_ = stripLastOccurrence(output_, ',');
        }

        // repair redundant end brackets
        while (i_ < n_ && (s_[i_] == '}' || s_[i_] == ']')) {
            i_++;
            parseWhitespaceAndSkipComments();
        }

        if (i_ >= n_) {
            return std::move(output_);
        }
        throwUnexpectedCharacter();
        return {}; // unreachable
    }

private:
    // ── parseValue ──
    bool parseValue() {
        parseWhitespaceAndSkipComments();
        bool processed = parseObject() || parseArray() || parseString() ||
                         parseNumber() || parseKeywords() ||
                         parseUnquotedString(false);
        parseWhitespaceAndSkipComments();
        return processed;
    }

    // ── parseWhitespaceAndSkipComments ──
    void parseWhitespaceAndSkipComments(bool skipNewline = true) {
        bool changed = parseWhitespace(skipNewline);
        do {
            changed = parseComment();
            if (changed) {
                changed = parseWhitespace(skipNewline);
            }
        } while (changed);
    }

    bool parseWhitespace(bool skipNewline) {
        std::string ws;
        while (i_ < n_) {
            char c = s_[i_];
            if (skipNewline ? isWhitespace(c) : (c == ' ' || c == '\t' || c == '\r')) {
                ws += c;
                i_++;
            } else {
                size_t swl = matchSpecialWhitespace(s_, i_, n_);
                if (swl > 0) {
                    ws += ' ';
                    i_ += swl;
                } else {
                    break;
                }
            }
        }
        if (!ws.empty()) {
            output_ += ws;
            return true;
        }
        return false;
    }

    bool parseComment() {
        // block comment /* ... */
        if (i_ + 1 < n_ && s_[i_] == '/' && s_[i_ + 1] == '*') {
            while (i_ < n_ && !(s_[i_] == '*' && i_ + 1 < n_ && s_[i_ + 1] == '/')) {
                i_++;
            }
            i_ += 2;
            return true;
        }
        // line comment // ...
        if (i_ + 1 < n_ && s_[i_] == '/' && s_[i_ + 1] == '/') {
            while (i_ < n_ && s_[i_] != '\n') {
                i_++;
            }
            return true;
        }
        return false;
    }

    // ── parseCharacter / skipCharacter ──
    bool parseCharacter(char expected) {
        if (i_ < n_ && s_[i_] == expected) {
            output_ += s_[i_];
            i_++;
            return true;
        }
        return false;
    }

    bool skipCharacter(char expected) {
        if (i_ < n_ && s_[i_] == expected) {
            i_++;
            return true;
        }
        return false;
    }

    // ── skipEllipsis ──
    void skipEllipsis() {
        parseWhitespaceAndSkipComments();
        if (i_ + 2 < n_ && s_[i_] == '.' && s_[i_ + 1] == '.' && s_[i_ + 2] == '.') {
            i_ += 3;
            parseWhitespaceAndSkipComments();
            skipCharacter(',');
        }
    }

    // ── parseObject ──
    bool parseObject() {
        if (i_ >= n_ || s_[i_] != '{') return false;
        output_ += '{';
        i_++;
        parseWhitespaceAndSkipComments();

        // repair: skip leading comma
        if (skipCharacter(',')) {
            parseWhitespaceAndSkipComments();
        }

        bool initial = true;
        while (i_ < n_ && s_[i_] != '}') {
            bool processedComma;
            if (!initial) {
                processedComma = parseCharacter(',');
                if (!processedComma) {
                    output_ = insertBeforeLastWhitespace(output_, ',');
                }
                parseWhitespaceAndSkipComments();
            } else {
                processedComma = true;
                initial = false;
            }

            skipEllipsis();

            bool processedKey = parseString() || parseUnquotedString(true);
            if (!processedKey) {
                if (i_ < n_ && s_[i_] == ',') {
                    // repair: skip duplicate comma
                    output_ = stripLastOccurrence(output_, ',');
                    continue;
                }
                if (i_ >= n_ || s_[i_] == '}' || s_[i_] == '{' ||
                    s_[i_] == ']' || s_[i_] == '[') {
                    output_ = stripLastOccurrence(output_, ',');
                } else {
                    throwObjectKeyExpected();
                }
                break;
            }

            parseWhitespaceAndSkipComments();

            bool processedColon = parseCharacter(':');
            bool truncatedText = i_ >= n_;
            if (!processedColon) {
                if (isStartOfValue(s_, i_, n_) || truncatedText) {
                    output_ = insertBeforeLastWhitespace(output_, ':');
                } else {
                    throwColonExpected();
                }
            }

            bool processedValue = parseValue();
            if (!processedValue) {
                if (processedColon || truncatedText) {
                    output_ += "null";
                } else {
                    throwColonExpected();
                }
            }
        }

        if (i_ < n_ && s_[i_] == '}') {
            output_ += '}';
            i_++;
        } else {
            output_ = insertBeforeLastWhitespace(output_, '}');
        }
        return true;
    }

    // ── parseArray ──
    bool parseArray() {
        if (i_ >= n_ || s_[i_] != '[') return false;
        output_ += '[';
        i_++;
        parseWhitespaceAndSkipComments();

        // repair: skip leading comma
        if (skipCharacter(',')) {
            parseWhitespaceAndSkipComments();
        }

        bool initial = true;
        while (i_ < n_ && s_[i_] != ']') {
            if (!initial) {
                bool processedComma = parseCharacter(',');
                if (!processedComma) {
                    output_ = insertBeforeLastWhitespace(output_, ',');
                }
            } else {
                initial = false;
            }

            skipEllipsis();

            bool processedValue = parseValue();
            if (!processedValue) {
                if (i_ < n_ && s_[i_] == ',') {
                    // repair: skip duplicate comma in array
                    output_ = stripLastOccurrence(output_, ',');
                    continue;
                }
                output_ = stripLastOccurrence(output_, ',');
                break;
            }
        }

        if (i_ < n_ && s_[i_] == ']') {
            output_ += ']';
            i_++;
        } else {
            output_ = insertBeforeLastWhitespace(output_, ']');
        }
        return true;
    }

    // ── parseString ──
    // TS: parseString(stopAtDelimiter=false, stopAtIndex=-1)
    bool parseString(bool stopAtDelimiter = false, size_t stopAtIndex = SIZE_MAX) {
        // TS: let skipEscapeChars = text[i] === '\\'
        bool skipEscapeChars = false;
        if (i_ < n_ && s_[i_] == '\\') {
            i_++;
            skipEscapeChars = true;
        }

        size_t ql = matchQuote(s_, i_, n_);
        if (ql == 0) {
            if (skipEscapeChars) i_--;
            return false;
        }

        // TS: determine isEndQuote function based on opening quote
        EndQuoteMode endQuoteMode = determineEndQuoteMode(s_, i_, n_);

        size_t iBefore = i_;
        size_t oBefore = output_.size();
        std::string str = "\"";
        i_ += ql;

        while (true) {
            if (i_ >= n_) {
                // end of text, missing end quote
                size_t iPrev = prevNonWhitespaceIndex(i_ - 1);
                if (!stopAtDelimiter && iPrev < n_ && isDelimiter(s_[iPrev])) {
                    i_ = iBefore;
                    output_.resize(oBefore);
                    return parseString(true);
                }
                str = insertBeforeLastWhitespace(str, '"');
                output_ += str;
                return true;
            }

            if (stopAtIndex != SIZE_MAX && i_ == stopAtIndex) {
                str = insertBeforeLastWhitespace(str, '"');
                output_ += str;
                return true;
            }

            // check end quote
            size_t eql = matchEndQuote(endQuoteMode, s_, i_, n_);
            if (eql > 0) {
                size_t iQuote = i_;
                size_t oQuote = str.size();
                str += '"';
                i_ += eql;
                output_ += str;
                parseWhitespaceAndSkipComments(false);

                if (stopAtDelimiter || i_ >= n_ || isDelimiter(s_[i_]) ||
                    matchQuote(s_, i_, n_) > 0 || isDigit(s_[i_])) {
                    // valid end quote
                    return true;
                }

                // TS: prevChar === ',' case
                size_t iPrevChar = prevNonWhitespaceIndex(iQuote - 1);
                char prevChar = (iPrevChar < n_) ? s_[iPrevChar] : '\0';
                if (prevChar == ',') {
                    i_ = iBefore;
                    output_.resize(oBefore);
                    return parseString(false, iPrevChar);
                }

                if (isDelimiter(prevChar)) {
                    i_ = iBefore;
                    output_.resize(oBefore);
                    return parseString(true);
                }

                // revert to right after the quote, repair unescaped quote
                output_.resize(oBefore);
                i_ = iQuote + eql;
                str = str.substr(0, oQuote) + "\\" + str.substr(oQuote);
            } else if (stopAtDelimiter && i_ < n_ && isUnquotedStringDelimiter(s_[i_])) {
                // stop at delimiter (missing end quote)
                str = insertBeforeLastWhitespace(str, '"');
                output_ += str;
                return true;
            } else if (i_ < n_ && s_[i_] == '\\') {
                // handle escaped content
                char nextCh = (i_ + 1 < n_) ? s_[i_ + 1] : '\0';
                if (isEscapeChar(nextCh)) {
                    str += s_[i_];
                    str += s_[i_ + 1];
                    i_ += 2;
                } else if (nextCh == 'u') {
                    size_t j = 2;
                    while (j < 6 && i_ + j < n_ && isHex(s_[i_ + j])) {
                        j++;
                    }
                    if (j == 6) {
                        str.append(s_ + i_, 6);
                        i_ += 6;
                    } else if (i_ + j >= n_) {
                        i_ = n_;
                    } else {
                        throwInvalidUnicodeCharacter();
                    }
                } else if (nextCh == '\n') {
                    str += "\\n";
                    i_ += 2;
                } else {
                    // invalid escape: remove backslash
                    str += nextCh;
                    i_ += 2;
                }
            } else if (i_ < n_) {
                // handle regular characters
                char c = s_[i_];

                // TS: if char === '"' && text[i-1] !== '\\' → repair unescaped double quote
                if (c == '"' && (i_ == 0 || s_[i_ - 1] != '\\')) {
                    str += "\\\"";
                    i_++;
                } else if (isControlCharacter(c)) {
                    const char* esc = controlCharEscape(c);
                    if (esc) str += esc;
                    i_++;
                } else {
                    str += c;
                    i_++;
                }
            }

            if (skipEscapeChars) {
                // skip escape character if present
                if (i_ < n_ && s_[i_] == '\\') {
                    i_++;
                }
            }
        }
        return false;
    }

    // ── parseNumber ──
    bool parseNumber() {
        size_t start = i_;

        if (i_ < n_ && s_[i_] == '-') {
            i_++;
            if (atEndOfNumber()) {
                repairNumberEndingWithNumericSymbol(start);
                return true;
            }
            if (i_ >= n_ || !isDigit(s_[i_])) {
                i_ = start;
                return false;
            }
        }

        if (i_ >= n_ || !isDigit(s_[i_])) {
            i_ = start;
            return false;
        }

        while (i_ < n_ && isDigit(s_[i_])) {
            i_++;
        }

        if (i_ < n_ && s_[i_] == '.') {
            i_++;
            if (atEndOfNumber()) {
                repairNumberEndingWithNumericSymbol(start);
                return true;
            }
            if (i_ >= n_ || !isDigit(s_[i_])) {
                i_ = start;
                return false;
            }
            while (i_ < n_ && isDigit(s_[i_])) {
                i_++;
            }
        }

        if (i_ < n_ && (s_[i_] == 'e' || s_[i_] == 'E')) {
            i_++;
            if (i_ < n_ && (s_[i_] == '-' || s_[i_] == '+')) {
                i_++;
            }
            if (atEndOfNumber()) {
                repairNumberEndingWithNumericSymbol(start);
                return true;
            }
            if (i_ >= n_ || !isDigit(s_[i_])) {
                i_ = start;
                return false;
            }
            while (i_ < n_ && isDigit(s_[i_])) {
                i_++;
            }
        }

        if (!atEndOfNumber()) {
            i_ = start;
            return false;
        }

        if (i_ > start) {
            // TS: repair leading zeros — we skip this for game JSON semantics
            output_.append(s_ + start, i_ - start);
            return true;
        }
        return false;
    }

    // ── parseKeywords ──
    bool parseKeywords() {
        return parseKeyword("true", "true") ||
               parseKeyword("false", "false") ||
               parseKeyword("null", "null") ||
               parseKeyword("True", "true") ||
               parseKeyword("False", "false") ||
               parseKeyword("None", "null");
    }

    bool parseKeyword(const char* name, const char* value) {
        size_t len = 0;
        while (name[len]) len++;
        if (i_ + len > n_) return false;
        for (size_t k = 0; k < len; ++k) {
            if (s_[i_ + k] != name[k]) return false;
        }
        // TS: no boundary check — keywords are greedy match by prefix
        // but we need boundary check to avoid matching "trueValue" etc.
        if (i_ + len < n_ && isFunctionNameChar(s_[i_ + len])) return false;
        output_ += value;
        i_ += len;
        return true;
    }

    // ── parseUnquotedString ──
    bool parseUnquotedString(bool isKey) {
        size_t start = i_;

        // TS: skip function name chars, check for '(' (MongoDB/JSONP) — we skip this

        while (i_ < n_ && !isUnquotedStringDelimiter(s_[i_]) &&
               matchQuote(s_, i_, n_) == 0 &&
               (!isKey || s_[i_] != ':')) {
            i_++;
        }

        if (i_ > start) {
            // go back to prevent trailing whitespace in the string
            while (i_ > start && isWhitespace(s_[i_ - 1])) {
                i_--;
            }

            std::string symbol(s_ + start, i_ - start);

            if (symbol == "undefined") {
                output_ += "null";
            } else {
                // JSON.stringify equivalent: wrap in quotes, escape special chars
                output_ += '"';
                for (char c : symbol) {
                    if (c == '"') { output_ += "\\\""; }
                    else if (c == '\\') { output_ += "\\\\"; }
                    else if (isControlCharacter(c)) {
                        const char* esc = controlCharEscape(c);
                        if (esc) output_ += esc;
                    } else {
                        output_ += c;
                    }
                }
                output_ += '"';
            }

            // TS: if text[i] === '"', skip it (missing start quote had end quote)
            if (i_ < n_ && s_[i_] == '"') {
                i_++;
            }

            return true;
        }
        return false;
    }

    // ── 辅助函数 ──

    size_t prevNonWhitespaceIndex(size_t start) const {
        size_t prev = start;
        while (prev > 0 && prev < n_ && isWhitespace(s_[prev])) {
            prev--;
        }
        return prev;
    }

    bool atEndOfNumber() const {
        return i_ >= n_ || isDelimiter(s_[i_]) || isWhitespace(s_[i_]);
    }

    void repairNumberEndingWithNumericSymbol(size_t start) {
        output_.append(s_ + start, i_ - start);
        output_ += '0';
    }

    [[noreturn]] void throwUnexpectedEnd() const {
        throw std::runtime_error("json repair: unexpected end of json string");
    }

    [[noreturn]] void throwUnexpectedCharacter() const {
        throw std::runtime_error(
            std::string("json repair: unexpected character '") + s_[i_] +
            "' at position " + std::to_string(i_));
    }

    [[noreturn]] void throwObjectKeyExpected() const {
        throw std::runtime_error(
            "json repair: object key expected at position " + std::to_string(i_));
    }

    [[noreturn]] void throwColonExpected() const {
        throw std::runtime_error(
            "json repair: colon expected at position " + std::to_string(i_));
    }

    [[noreturn]] void throwInvalidUnicodeCharacter() const {
        throw std::runtime_error(
            "json repair: invalid unicode character at position " + std::to_string(i_));
    }
};

}  // anonymous namespace

std::string clean_text(const std::string& text) {
    if (text.empty()) return text;
    return JsonRepairer(text.data(), text.size()).repair();
}

}  // namespace sultan
