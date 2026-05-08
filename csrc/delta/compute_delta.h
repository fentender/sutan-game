#pragma once
#include "change_kind.h"
#include "delta_node.h"

namespace sultan {

class JsonDoc;

DeltaNodePtr compute_delta(
    const JsonDoc& base,
    const JsonDoc& mod,
    MergeMode merge_mode = MergeMode::Normal);

DeltaNodePtr remap_delta_to_current(
    const DeltaDict& delta,
    const JsonDoc& hist_base,
    const JsonDoc& current_base);

}  // namespace sultan
