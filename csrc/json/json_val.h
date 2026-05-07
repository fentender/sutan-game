#pragma once
#include <cstddef>
#include <cstdint>

struct yyjson_val;

namespace sultan {

enum class JsonType : uint8_t {
    Null, Bool, Int, Real, Str, Obj, Arr
};

class JsonVal {
    yyjson_val* val_ = nullptr;
public:
    JsonVal() = default;
    explicit JsonVal(yyjson_val* v) : val_(v) {}

    bool valid() const { return val_ != nullptr; }
    explicit operator bool() const { return valid(); }

    JsonType type() const;

    bool is_null() const { return type() == JsonType::Null; }
    bool is_bool() const { return type() == JsonType::Bool; }
    bool is_int() const  { return type() == JsonType::Int; }
    bool is_real() const { return type() == JsonType::Real; }
    bool is_str() const  { return type() == JsonType::Str; }
    bool is_obj() const  { return type() == JsonType::Obj; }
    bool is_arr() const  { return type() == JsonType::Arr; }

    bool        get_bool() const;
    int64_t     get_int() const;
    uint64_t    get_uint() const;
    double      get_real() const;
    const char* get_str() const;

    JsonVal obj_get(const char* key) const;
    size_t  obj_size() const;

    size_t arr_size() const;

    struct ObjEntry;
    class ObjIter;
    class ArrIter;

    ObjIter obj_iter() const;
    ArrIter arr_iter() const;

    yyjson_val* raw() const { return val_; }
};

struct JsonVal::ObjEntry {
    const char* key;
    size_t key_len;
    JsonVal val;
};

class JsonVal::ObjIter {
    alignas(8) char buf_[32]{};
public:
    explicit ObjIter(yyjson_val* obj);
    bool next(ObjEntry& out);
};

class JsonVal::ArrIter {
    alignas(8) char buf_[32]{};
public:
    explicit ArrIter(yyjson_val* arr);
    bool next(JsonVal& out);
};

}  // namespace sultan
