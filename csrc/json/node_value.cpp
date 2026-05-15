#include "node_value.h"
#include "json_val.h"
#include "mut_doc.h"
#include "mut_val.h"
#include <stdexcept>

extern "C" {
#include <yyjson.h>
}

namespace sultan {

ScalarValue nv_to_scalar(const NodeValue& v) {
    return std::visit([](auto&& arg) -> ScalarValue {
        using T = std::decay_t<decltype(arg)>;
        if constexpr (std::is_same_v<T, shared_ptr<const JsonDoc>>)
            throw std::runtime_error("nv_to_scalar: value is complex");
        else
            return ScalarValue{arg};
    }, v);
}

NodeValue nv_from_scalar(const ScalarValue& sv) {
    return std::visit([](auto&& arg) -> NodeValue {
        return NodeValue{arg};
    }, sv);
}

JsonDoc copy_val_to_doc(JsonVal v) {
    MutDoc md;
    yyjson_mut_val* copied = yyjson_val_mut_copy(md.raw(), v.raw());
    if (!copied)
        throw std::runtime_error("copy_val_to_doc: failed to copy value");
    md.set_root(MutVal(md.raw(), copied));
    return md.freeze();
}

}  // namespace sultan
