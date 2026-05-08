#pragma once
#include "json_val.h"
#include <string>
#include <utility>
#include <vector>

namespace sultan {

struct ArrayMatching {
    std::vector<std::pair<int, int>> pairs;
    std::vector<int> unmatched_mod;
    std::vector<int> unmatched_base;
    double confidence = 1.0;
};

ArrayMatching match_by_heuristic(JsonVal base_arr, JsonVal mod_arr);

double element_similarity(JsonVal a, JsonVal b);

}  // namespace sultan
