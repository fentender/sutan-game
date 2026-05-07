#include "json_val.h"
#include "yyjson.h"
#include <cstring>

namespace sultan {

static_assert(sizeof(yyjson_obj_iter) <= 32, "ObjIter buf_ too small");
static_assert(sizeof(yyjson_arr_iter) <= 32, "ArrIter buf_ too small");

// ── JsonVal ──

JsonType JsonVal::type() const {
    if (!val_) return JsonType::Null;
    if (yyjson_is_null(val_))  return JsonType::Null;
    if (yyjson_is_bool(val_))  return JsonType::Bool;
    if (yyjson_is_int(val_))   return JsonType::Int;
    if (yyjson_is_real(val_))  return JsonType::Real;
    if (yyjson_is_str(val_))   return JsonType::Str;
    if (yyjson_is_obj(val_))   return JsonType::Obj;
    if (yyjson_is_arr(val_))   return JsonType::Arr;
    return JsonType::Null;
}

bool JsonVal::get_bool() const {
    return val_ ? yyjson_get_bool(val_) : false;
}

int64_t JsonVal::get_int() const {
    if (!val_) return 0;
    if (yyjson_is_sint(val_)) return yyjson_get_sint(val_);
    if (yyjson_is_uint(val_)) return static_cast<int64_t>(yyjson_get_uint(val_));
    return 0;
}

uint64_t JsonVal::get_uint() const {
    return val_ ? yyjson_get_uint(val_) : 0;
}

double JsonVal::get_real() const {
    return val_ ? yyjson_get_real(val_) : 0.0;
}

const char* JsonVal::get_str() const {
    return val_ ? yyjson_get_str(val_) : nullptr;
}

JsonVal JsonVal::obj_get(const char* key) const {
    if (!val_) return {};
    return JsonVal(yyjson_obj_get(val_, key));
}

size_t JsonVal::obj_size() const {
    return val_ ? yyjson_obj_size(val_) : 0;
}

size_t JsonVal::arr_size() const {
    return val_ ? yyjson_arr_size(val_) : 0;
}

// ── ObjIter ──

JsonVal::ObjIter::ObjIter(yyjson_val* obj) {
    auto& iter = *reinterpret_cast<yyjson_obj_iter*>(buf_);
    iter = yyjson_obj_iter_with(obj);
}

bool JsonVal::ObjIter::next(ObjEntry& out) {
    auto& iter = *reinterpret_cast<yyjson_obj_iter*>(buf_);
    yyjson_val* key = yyjson_obj_iter_next(&iter);
    if (!key) return false;
    out.key = yyjson_get_str(key);
    out.key_len = yyjson_get_len(key);
    out.val = JsonVal(yyjson_obj_iter_get_val(key));
    return true;
}

JsonVal::ObjIter JsonVal::obj_iter() const {
    return ObjIter(val_);
}

// ── ArrIter ──

JsonVal::ArrIter::ArrIter(yyjson_val* arr) {
    auto& iter = *reinterpret_cast<yyjson_arr_iter*>(buf_);
    iter = yyjson_arr_iter_with(arr);
}

bool JsonVal::ArrIter::next(JsonVal& out) {
    auto& iter = *reinterpret_cast<yyjson_arr_iter*>(buf_);
    yyjson_val* val = yyjson_arr_iter_next(&iter);
    if (!val) return false;
    out = JsonVal(val);
    return true;
}

JsonVal::ArrIter JsonVal::arr_iter() const {
    return ArrIter(val_);
}

}  // namespace sultan
