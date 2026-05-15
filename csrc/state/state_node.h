#pragma once
#include "change_kind.h"
#include "node_value.h"
#include <cstdint>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

namespace sultan {

using std::string;
using std::unique_ptr;
using std::unordered_map;
using std::variant;
using std::vector;

struct JsonElementState;
struct JsonDictState;
struct JsonArrayState;

enum class StateType : uint8_t { Element, Dict, Array };

struct StateBase {
    StateType state_type_;

    virtual ~StateBase() = default;
    virtual ChangeKind kind() const = 0;
    virtual bool is_modified() const = 0;
    virtual unique_ptr<StateBase> clone() const = 0;

    bool is_element() const { return state_type_ == StateType::Element; }
    bool is_dict() const    { return state_type_ == StateType::Dict; }
    bool is_array() const   { return state_type_ == StateType::Array; }

    JsonElementState& as_element();
    JsonDictState& as_dict();
    JsonArrayState& as_array();
    const JsonElementState& as_element() const;
    const JsonDictState& as_dict() const;
    const JsonArrayState& as_array() const;

protected:
    explicit StateBase(StateType t) : state_type_(t) {}
};

using StateNodePtr = unique_ptr<StateBase>;

// ── 叶子：标量 ──

struct JsonElementState : StateBase {
    JsonElementState() : StateBase(StateType::Element) {}
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
    int version = 0;
    unordered_map<string, StateNodePtr> entries;

    JsonDictState() : StateBase(StateType::Dict) {}

    ChangeKind kind() const override { return kind_; }
    bool is_modified() const override;
    StateNodePtr clone() const override;

    StateBase* find(const string& key) const;
    void insert(string key, StateNodePtr node);
    size_t size() const { return entries.size(); }
};

// ── 数组 ──

struct JsonArrayState : StateBase {
    JsonArrayState() : StateBase(StateType::Array) {}
    ChangeKind kind_ = ChangeKind::Origin;
    int version = 0;
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

StateNodePtr make_state_node(JsonVal v);

}  // namespace sultan
