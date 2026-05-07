#include "field_ops.h"
#include "json_doc.h"
#include "yyjson.h"

#include <stdexcept>

namespace sultan {

// ── 提取辅助（不可变树遍历） ──

static void collect_strings(yyjson_val* val, const char* name, size_t name_len,
                            std::vector<std::string>& out) {
    if (!val) return;

    if (yyjson_is_obj(val)) {
        yyjson_obj_iter iter = yyjson_obj_iter_with(val);
        yyjson_val* key;
        while ((key = yyjson_obj_iter_next(&iter))) {
            yyjson_val* child = yyjson_obj_iter_get_val(key);
            if (yyjson_equals_strn(key, name, name_len) && yyjson_is_str(child)) {
                out.emplace_back(yyjson_get_str(child));
            }
            collect_strings(child, name, name_len, out);
        }
    } else if (yyjson_is_arr(val)) {
        yyjson_arr_iter iter = yyjson_arr_iter_with(val);
        yyjson_val* elem;
        while ((elem = yyjson_arr_iter_next(&iter))) {
            collect_strings(elem, name, name_len, out);
        }
    }
}

static void collect_ints(yyjson_val* val, const char* name, size_t name_len,
                         std::vector<int64_t>& out) {
    if (!val) return;

    if (yyjson_is_obj(val)) {
        yyjson_obj_iter iter = yyjson_obj_iter_with(val);
        yyjson_val* key;
        while ((key = yyjson_obj_iter_next(&iter))) {
            yyjson_val* child = yyjson_obj_iter_get_val(key);
            if (yyjson_equals_strn(key, name, name_len) && yyjson_is_int(child)) {
                out.push_back(yyjson_get_sint(child));
            }
            collect_ints(child, name, name_len, out);
        }
    } else if (yyjson_is_arr(val)) {
        yyjson_arr_iter iter = yyjson_arr_iter_with(val);
        yyjson_val* elem;
        while ((elem = yyjson_arr_iter_next(&iter))) {
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

// ── 替换辅助（可变树遍历） ──

static void replace_field_ints_recursive(
    yyjson_mut_val* val,
    const char* name, size_t name_len,
    const std::unordered_map<int64_t, int64_t>& mapping) {
    if (!val) return;

    if (yyjson_mut_is_obj(val)) {
        yyjson_mut_obj_iter iter = yyjson_mut_obj_iter_with(val);
        yyjson_mut_val* key;
        while ((key = yyjson_mut_obj_iter_next(&iter))) {
            yyjson_mut_val* child = yyjson_mut_obj_iter_get_val(key);
            const char* k = yyjson_mut_get_str(key);
            size_t klen = yyjson_mut_get_len(key);
            if (klen == name_len && memcmp(k, name, name_len) == 0
                && yyjson_mut_is_int(child)) {
                auto it = mapping.find(yyjson_mut_get_sint(child));
                if (it != mapping.end())
                    yyjson_mut_set_sint(child, it->second);
            }
            replace_field_ints_recursive(child, name, name_len, mapping);
        }
    } else if (yyjson_mut_is_arr(val)) {
        yyjson_mut_arr_iter iter = yyjson_mut_arr_iter_with(val);
        yyjson_mut_val* elem;
        while ((elem = yyjson_mut_arr_iter_next(&iter))) {
            replace_field_ints_recursive(elem, name, name_len, mapping);
        }
    }
}

static void replace_field_strs_recursive(
    yyjson_mut_val* val,
    const char* name, size_t name_len,
    const std::unordered_map<std::string, std::string>& mapping) {
    if (!val) return;

    if (yyjson_mut_is_obj(val)) {
        yyjson_mut_obj_iter iter = yyjson_mut_obj_iter_with(val);
        yyjson_mut_val* key;
        while ((key = yyjson_mut_obj_iter_next(&iter))) {
            yyjson_mut_val* child = yyjson_mut_obj_iter_get_val(key);
            const char* k = yyjson_mut_get_str(key);
            size_t klen = yyjson_mut_get_len(key);
            if (klen == name_len && memcmp(k, name, name_len) == 0
                && yyjson_mut_is_str(child)) {
                const char* s = yyjson_mut_get_str(child);
                if (s) {
                    auto it = mapping.find(s);
                    if (it != mapping.end())
                        yyjson_mut_set_strn(child, it->second.c_str(), it->second.size());
                }
            }
            replace_field_strs_recursive(child, name, name_len, mapping);
        }
    } else if (yyjson_mut_is_arr(val)) {
        yyjson_mut_arr_iter iter = yyjson_mut_arr_iter_with(val);
        yyjson_mut_val* elem;
        while ((elem = yyjson_mut_arr_iter_next(&iter))) {
            replace_field_strs_recursive(elem, name, name_len, mapping);
        }
    }
}

static JsonDoc mut_to_doc(yyjson_mut_doc* mut) {
    yyjson_doc* result = yyjson_mut_doc_imut_copy(mut, nullptr);
    yyjson_mut_doc_free(mut);
    if (!result)
        throw std::runtime_error("Failed to convert mutable document to immutable");
    return JsonDoc::from_raw(result);
}

// ── 替换 API ──

JsonDoc replace_field_ints(
    const JsonDoc& doc,
    const std::string& field_name,
    const std::unordered_map<int64_t, int64_t>& mapping) {
    yyjson_mut_doc* mut = yyjson_doc_mut_copy(doc.raw_doc(), nullptr);
    if (!mut) throw std::runtime_error("Failed to create mutable copy");

    if (!mapping.empty())
        replace_field_ints_recursive(
            yyjson_mut_doc_get_root(mut),
            field_name.c_str(), field_name.size(), mapping);

    return mut_to_doc(mut);
}

JsonDoc replace_field_strs(
    const JsonDoc& doc,
    const std::string& field_name,
    const std::unordered_map<std::string, std::string>& mapping) {
    yyjson_mut_doc* mut = yyjson_doc_mut_copy(doc.raw_doc(), nullptr);
    if (!mut) throw std::runtime_error("Failed to create mutable copy");

    if (!mapping.empty())
        replace_field_strs_recursive(
            yyjson_mut_doc_get_root(mut),
            field_name.c_str(), field_name.size(), mapping);

    return mut_to_doc(mut);
}

JsonDoc replace_root_keys(
    const JsonDoc& doc,
    const std::unordered_map<std::string, std::string>& mapping) {
    yyjson_mut_doc* mut = yyjson_doc_mut_copy(doc.raw_doc(), nullptr);
    if (!mut) throw std::runtime_error("Failed to create mutable copy");

    if (!mapping.empty()) {
        yyjson_mut_val* root = yyjson_mut_doc_get_root(mut);
        if (root && yyjson_mut_is_obj(root)) {
            yyjson_mut_obj_iter iter = yyjson_mut_obj_iter_with(root);
            yyjson_mut_val* key;
            while ((key = yyjson_mut_obj_iter_next(&iter))) {
                const char* k = yyjson_mut_get_str(key);
                if (k) {
                    auto it = mapping.find(k);
                    if (it != mapping.end())
                        yyjson_mut_set_strn(key, it->second.c_str(), it->second.size());
                }
            }
        }
    }

    return mut_to_doc(mut);
}

}  // namespace sultan
