#include "state_node.h"
#include <cmath>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace sultan {

// ── ScalarValue 辅助 ──

string serialize_scalar(const ScalarValue& val) {
    struct Visitor {
        string operator()(std::nullptr_t) const { return "null"; }
        string operator()(bool v) const { return v ? "true" : "false"; }
        string operator()(int64_t v) const { return std::to_string(v); }
        string operator()(double v) const {
            if (std::isinf(v) || std::isnan(v)) return "null";
            std::ostringstream oss;
            oss << std::setprecision(17) << v;
            string s = oss.str();
            if (s.find('.') == string::npos && s.find('e') == string::npos)
                s += ".0";
            return s;
        }
        string operator()(const string& v) const {
            std::ostringstream oss;
            oss << '"';
            for (char c : v) {
                switch (c) {
                    case '"':  oss << "\\\""; break;
                    case '\\': oss << "\\\\"; break;
                    case '\b': oss << "\\b"; break;
                    case '\f': oss << "\\f"; break;
                    case '\n': oss << "\\n"; break;
                    case '\r': oss << "\\r"; break;
                    case '\t': oss << "\\t"; break;
                    default:
                        if (static_cast<unsigned char>(c) < 0x20) {
                            oss << "\\u" << std::hex << std::setfill('0')
                                << std::setw(4) << static_cast<int>(c);
                        } else {
                            oss << c;
                        }
                }
            }
            oss << '"';
            return oss.str();
        }
    };
    return std::visit(Visitor{}, val);
}

bool scalar_equal(const ScalarValue& a, const ScalarValue& b) {
    return a == b;
}

// ── StateBase ──

JsonElementState& StateBase::as_element() {
    if (state_type_ != StateType::Element)
        throw std::runtime_error("StateBase: not an element");
    return static_cast<JsonElementState&>(*this);
}
JsonDictState& StateBase::as_dict() {
    if (state_type_ != StateType::Dict)
        throw std::runtime_error("StateBase: not a dict");
    return static_cast<JsonDictState&>(*this);
}
JsonArrayState& StateBase::as_array() {
    if (state_type_ != StateType::Array)
        throw std::runtime_error("StateBase: not an array");
    return static_cast<JsonArrayState&>(*this);
}
const JsonElementState& StateBase::as_element() const {
    if (state_type_ != StateType::Element)
        throw std::runtime_error("StateBase: not an element");
    return static_cast<const JsonElementState&>(*this);
}
const JsonDictState& StateBase::as_dict() const {
    if (state_type_ != StateType::Dict)
        throw std::runtime_error("StateBase: not a dict");
    return static_cast<const JsonDictState&>(*this);
}
const JsonArrayState& StateBase::as_array() const {
    if (state_type_ != StateType::Array)
        throw std::runtime_error("StateBase: not an array");
    return static_cast<const JsonArrayState&>(*this);
}

// ── JsonElementState ──

bool JsonElementState::is_modified() const {
    return base_kind(kind_) != ChangeKind::Origin;
}

StateNodePtr JsonElementState::clone() const {
    auto node = std::make_unique<JsonElementState>();
    node->kind_ = kind_;
    node->value = value;
    node->old_value = old_value;
    node->version = version;
    return node;
}

// ── JsonDictState ──

bool JsonDictState::is_modified() const {
    for (auto& [key, child] : entries) {
        if (child && child->is_modified()) return true;
    }
    return false;
}

StateNodePtr JsonDictState::clone() const {
    auto node = std::make_unique<JsonDictState>();
    node->kind_ = kind_;
    node->version = version;
    for (auto& [key, child] : entries) {
        node->entries.emplace(key, child ? child->clone() : nullptr);
    }
    return node;
}

StateBase* JsonDictState::find(const string& key) const {
    auto it = entries.find(key);
    if (it == entries.end()) return nullptr;
    return it->second.get();
}

void JsonDictState::insert(string key, StateNodePtr node) {
    entries.insert_or_assign(std::move(key), std::move(node));
}

// ── JsonArrayState ──

bool JsonArrayState::is_modified() const {
    for (auto& child : diffs) {
        if (child && child->is_modified()) return true;
    }
    return false;
}

StateNodePtr JsonArrayState::clone() const {
    auto node = std::make_unique<JsonArrayState>();
    node->kind_ = kind_;
    node->version = version;
    node->base_count = base_count;
    node->indices = indices;
    node->order = order;
    node->is_duplist = is_duplist;
    node->old_order = old_order;
    for (auto& child : diffs) {
        node->diffs.push_back(child ? child->clone() : nullptr);
    }
    return node;
}

// ── 工厂 ──

StateNodePtr make_element(ScalarValue value, ChangeKind kind) {
    auto node = std::make_unique<JsonElementState>();
    node->kind_ = kind;
    node->value = std::move(value);
    node->old_value = nullptr;
    return node;
}

}  // namespace sultan
