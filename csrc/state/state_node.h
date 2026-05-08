#pragma once
#include "change_kind.h"
#include <cstdint>
#include <memory>
#include <string>
#include <unordered_map>
#include <variant>
#include <vector>

namespace sultan {

using std::string;
using std::unique_ptr;
using std::unordered_map;
using std::variant;
using std::vector;

using ScalarValue = variant<std::nullptr_t, bool, int64_t, double, string>;

string serialize_scalar(const ScalarValue& val);
bool scalar_equal(const ScalarValue& a, const ScalarValue& b);

class JsonVal;
ScalarValue val_to_scalar(JsonVal v);

struct JsonElementState;
struct JsonDictState;
struct JsonArrayState;

struct StateBase {
    virtual ~StateBase() = default;
    virtual ChangeKind kind() const = 0;
    virtual bool is_modified() const = 0;
    virtual unique_ptr<StateBase> clone() const = 0;

    bool is_element() const;
    bool is_dict() const;
    bool is_array() const;

    JsonElementState& as_element();
    JsonDictState& as_dict();
    JsonArrayState& as_array();
    const JsonElementState& as_element() const;
    const JsonDictState& as_dict() const;
    const JsonArrayState& as_array() const;
};

using StateNodePtr = unique_ptr<StateBase>;

// ── 叶子：标量 ──

struct JsonElementState : StateBase {
    ChangeKind kind_ = ChangeKind::Origin;
    ScalarValue value;
    ScalarValue old_value;  // 无旧值时为 nullptr
    int version = 0;

    ChangeKind kind() const override { return kind_; }
    bool is_modified() const override;
    StateNodePtr clone() const override;
};

// ── 字典 ──

struct JsonDictState : StateBase {
    ChangeKind kind_ = ChangeKind::Origin;
    unordered_map<string, StateNodePtr> entries;

    ChangeKind kind() const override { return kind_; }
    bool is_modified() const override;
    StateNodePtr clone() const override;

    StateBase* find(const string& key) const;
    void insert(string key, StateNodePtr node);
    size_t size() const { return entries.size(); }
};

// ── 数组 ──

struct JsonArrayState : StateBase {
    ChangeKind kind_ = ChangeKind::Origin;
    vector<StateNodePtr> diffs;
    int base_count = 0;
    vector<int> indices;
    vector<int> order;
    bool is_duplist = false;
    vector<int> old_order;

    ChangeKind kind() const override { return kind_; }
    bool is_modified() const override;
    StateNodePtr clone() const override;
};

StateNodePtr make_element(ScalarValue value, ChangeKind kind = ChangeKind::Origin);
StateNodePtr make_dict(ChangeKind kind = ChangeKind::Origin);
StateNodePtr make_array(ChangeKind kind = ChangeKind::Origin);

StateNodePtr build_state_node(JsonVal v);

}  // namespace sultan
