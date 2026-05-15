#pragma once
#include "json_doc.h"
#include <cstdint>
#include <memory>
#include <string>
#include <variant>

namespace sultan {

using std::shared_ptr;
using std::string;
using std::variant;

// 纯标量值（不含复杂类型）
using ScalarValue = variant<std::nullptr_t, bool, int64_t, double, string>;

// 标量 或 复杂值（不可变 JSON 文档共享引用）
using NodeValue = variant<std::nullptr_t, bool, int64_t, double, string, shared_ptr<const JsonDoc>>;

// ── ScalarValue 工具 ──

string serialize_scalar(const ScalarValue& val);
bool scalar_equal(const ScalarValue& a, const ScalarValue& b);

ScalarValue val_to_scalar(JsonVal v);

// ── NodeValue 工具 ──

inline bool nv_is_complex(const NodeValue& v) {
    return std::holds_alternative<shared_ptr<const JsonDoc>>(v);
}

inline bool nv_is_null(const NodeValue& v) {
    return std::holds_alternative<std::nullptr_t>(v);
}

inline const shared_ptr<const JsonDoc>& nv_to_doc(const NodeValue& v) {
    return std::get<shared_ptr<const JsonDoc>>(v);
}

ScalarValue nv_to_scalar(const NodeValue& v);
NodeValue nv_from_scalar(const ScalarValue& sv);

// ── JsonVal → 独立 JsonDoc 复制 ──

JsonDoc copy_val_to_doc(JsonVal v);

}  // namespace sultan
