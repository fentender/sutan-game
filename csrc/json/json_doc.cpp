#include "json_doc.h"
#include "json_cleaner.h"
#include "resource_loader.h"
#include "yyjson.h"

#include <stdexcept>

namespace sultan {

static constexpr yyjson_read_flag kReadFlags =
    YYJSON_READ_ALLOW_TRAILING_COMMAS |
    YYJSON_READ_ALLOW_COMMENTS |
    YYJSON_READ_ALLOW_BOM;

// ── 生命周期 ──

JsonDoc::JsonDoc(yyjson_doc* doc) : doc_(doc) {}

JsonDoc::JsonDoc(JsonDoc&& other) noexcept : doc_(other.doc_) {
    other.doc_ = nullptr;
}

JsonDoc& JsonDoc::operator=(JsonDoc&& other) noexcept {
    if (this != &other) {
        yyjson_doc_free(doc_);
        doc_ = other.doc_;
        other.doc_ = nullptr;
    }
    return *this;
}

JsonDoc::~JsonDoc() {
    yyjson_doc_free(doc_);
}

// ── 工厂 ──

JsonDoc JsonDoc::parse(const std::string& text, bool clean) {
    std::string input = clean ? clean_text(text) : text;

    yyjson_read_err err{};
    yyjson_doc* doc = yyjson_read_opts(
        const_cast<char*>(input.data()), input.size(),
        kReadFlags, nullptr, &err);

    if (!doc) {
        std::string msg = "JSON parse error";
        if (err.msg) {
            msg += ": ";
            msg += err.msg;
            msg += " (pos: " + std::to_string(err.pos) + ")";
        }
        throw std::runtime_error(msg);
    }
    return JsonDoc(doc);
}

JsonDoc JsonDoc::parse_file(const std::string& path, bool clean) {
    auto content = resource_loader().read_text(path);
    return parse(*content, clean);
}

// ── 序列化 ──

std::string JsonDoc::to_string(bool compact) const {
    if (!doc_)
        throw std::runtime_error("Cannot serialize null JsonDoc");

    yyjson_write_flag flg = compact
        ? YYJSON_WRITE_NOFLAG
        : YYJSON_WRITE_PRETTY;

    size_t len = 0;
    char* json = yyjson_write_opts(doc_, flg, nullptr, &len, nullptr);
    if (!json)
        throw std::runtime_error("JSON serialization failed");

    std::string result(json, len);
    free(json);
    return result;
}

// ── 内部访问 ──

yyjson_val* JsonDoc::root() const {
    return doc_ ? yyjson_doc_get_root(doc_) : nullptr;
}

JsonDoc JsonDoc::from_raw(yyjson_doc* doc) {
    return JsonDoc(doc);
}

}  // namespace sultan
