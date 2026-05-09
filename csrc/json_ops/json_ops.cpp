#include "json_ops.h"
#include "json_doc.h"
#include "json_val.h"
#include "mut_doc.h"
#include "mut_val.h"

#include <cstring>

namespace sultan {

// ── 提取辅助（不可变树遍历） ──

static void collect_strings(JsonVal val, const char* name, size_t name_len,
                            std::vector<std::string>& out) {
    if (!val) return;

    if (val.is_obj()) {
        auto it = val.obj_iter();
        JsonVal::ObjEntry e;
        while (it.next(e)) {
            if (e.key_len == name_len && memcmp(e.key, name, name_len) == 0
                && e.val.is_str()) {
                out.emplace_back(e.val.get_str());
            }
            collect_strings(e.val, name, name_len, out);
        }
    } else if (val.is_arr()) {
        auto it = val.arr_iter();
        JsonVal elem;
        while (it.next(elem)) {
            collect_strings(elem, name, name_len, out);
        }
    }
}

static void collect_ints(JsonVal val, const char* name, size_t name_len,
                         std::vector<int64_t>& out) {
    if (!val) return;

    if (val.is_obj()) {
        auto it = val.obj_iter();
        JsonVal::ObjEntry e;
        while (it.next(e)) {
            if (e.key_len == name_len && memcmp(e.key, name, name_len) == 0
                && e.val.is_int()) {
                out.push_back(e.val.get_int());
            }
            collect_ints(e.val, name, name_len, out);
        }
    } else if (val.is_arr()) {
        auto it = val.arr_iter();
        JsonVal elem;
        while (it.next(elem)) {
            collect_ints(elem, name, name_len, out);
        }
    }
}

// ── 提取 API ──

std::vector<std::string> extract_string_values(
    const JsonDoc& doc, const std::string& field_name) {
    std::vector<std::string> result;
    collect_strings(doc.root(), field_name.c_str(), field_name.size(), result);
    return result;
}

std::vector<int64_t> extract_int_values(
    const JsonDoc& doc, const std::string& field_name) {
    std::vector<int64_t> result;
    collect_ints(doc.root(), field_name.c_str(), field_name.size(), result);
    return result;
}

std::vector<std::string> extract_root_keys(const JsonDoc& doc) {
    std::vector<std::string> result;
    auto root = doc.root();
    if (!root || !root.is_obj()) return result;

    result.reserve(root.obj_size());
    auto it = root.obj_iter();
    JsonVal::ObjEntry e;
    while (it.next(e)) {
        result.emplace_back(e.key, e.key_len);
    }
    return result;
}

// ── 替换辅助（可变树遍历） ──

static void replace_field_ints_recursive(
    MutVal val,
    const char* name, size_t name_len,
    const std::unordered_map<int64_t, int64_t>& mapping) {
    if (!val) return;

    if (val.is_obj()) {
        auto it = val.obj_iter();
        MutVal::ObjEntry e;
        while (it.next(e)) {
            if (e.key_len == name_len && memcmp(e.key_str, name, name_len) == 0
                && e.val.is_int()) {
                auto found = mapping.find(e.val.get_int());
                if (found != mapping.end())
                    e.val.set_int(found->second);
            }
            replace_field_ints_recursive(e.val, name, name_len, mapping);
        }
    } else if (val.is_arr()) {
        auto it = val.arr_iter();
        MutVal elem;
        while (it.next(elem)) {
            replace_field_ints_recursive(elem, name, name_len, mapping);
        }
    }
}

static void replace_field_strs_recursive(
    MutVal val,
    const char* name, size_t name_len,
    const std::unordered_map<std::string, std::string>& mapping) {
    if (!val) return;

    if (val.is_obj()) {
        auto it = val.obj_iter();
        MutVal::ObjEntry e;
        while (it.next(e)) {
            if (e.key_len == name_len && memcmp(e.key_str, name, name_len) == 0
                && e.val.is_str()) {
                auto* s = e.val.get_str();
                if (s) {
                    auto found = mapping.find(s);
                    if (found != mapping.end())
                        e.val.set_str(found->second);
                }
            }
            replace_field_strs_recursive(e.val, name, name_len, mapping);
        }
    } else if (val.is_arr()) {
        auto it = val.arr_iter();
        MutVal elem;
        while (it.next(elem)) {
            replace_field_strs_recursive(elem, name, name_len, mapping);
        }
    }
}

// ── 替换 API ──

JsonDoc replace_field_ints(
    const JsonDoc& doc,
    const std::string& field_name,
    const std::unordered_map<int64_t, int64_t>& mapping) {
    auto d = MutDoc::from(doc);
    if (!mapping.empty())
        replace_field_ints_recursive(
            d.root(), field_name.c_str(), field_name.size(), mapping);
    return d.freeze();
}

JsonDoc replace_field_strs(
    const JsonDoc& doc,
    const std::string& field_name,
    const std::unordered_map<std::string, std::string>& mapping) {
    auto d = MutDoc::from(doc);
    if (!mapping.empty())
        replace_field_strs_recursive(
            d.root(), field_name.c_str(), field_name.size(), mapping);
    return d.freeze();
}

JsonDoc replace_root_keys(
    const JsonDoc& doc,
    const std::unordered_map<std::string, std::string>& mapping) {
    auto d = MutDoc::from(doc);
    if (!mapping.empty()) {
        auto root = d.root();
        if (root && root.is_obj()) {
            auto it = root.obj_iter();
            MutVal::ObjEntry e;
            while (it.next(e)) {
                if (e.key_str) {
                    auto found = mapping.find(e.key_str);
                    if (found != mapping.end())
                        e.key.set_str(found->second);
                }
            }
        }
    }
    return d.freeze();
}

// ── 分类 API ──

std::string classify_json(const JsonDoc& doc) {
    auto root = doc.root();
    if (!root || !root.is_obj()) return "config";

    if (root.obj_get("id").valid()) return "entity";

    auto it = root.obj_iter();
    JsonVal::ObjEntry e;
    bool all_obj = true;
    bool any_has_id = false;
    bool has_values = false;

    while (it.next(e)) {
        has_values = true;
        if (!e.val.is_obj()) { all_obj = false; break; }
        if (e.val.obj_get("id").valid()) any_has_id = true;
    }

    if (has_values && all_obj && any_has_id) return "dictionary";
    return "config";
}

}  // namespace sultan
