#include "similarity.h"
#include <algorithm>
#include <cstdlib>
#include <vector>

namespace sultan {

int levenshtein_distance(const std::string& a, const std::string& b) {
    const size_t m = a.size();
    const size_t n = b.size();
    if (m == 0) return static_cast<int>(n);
    if (n == 0) return static_cast<int>(m);

    const std::string& shorter = (m <= n) ? a : b;
    const std::string& longer  = (m <= n) ? b : a;
    const size_t slen = shorter.size();
    const size_t llen = longer.size();

    std::vector<int> prev(slen + 1);
    std::vector<int> curr(slen + 1);

    for (size_t j = 0; j <= slen; ++j)
        prev[j] = static_cast<int>(j);

    for (size_t i = 1; i <= llen; ++i) {
        curr[0] = static_cast<int>(i);
        for (size_t j = 1; j <= slen; ++j) {
            int cost = (longer[i - 1] == shorter[j - 1]) ? 0 : 1;
            curr[j] = std::min({prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost});
        }
        std::swap(prev, curr);
    }
    return prev[slen];
}

double string_ratio(const std::string& a, const std::string& b) {
    if (a.empty() && b.empty()) return 1.0;
    size_t max_len = std::max(a.size(), b.size());
    if (max_len == 0) return 1.0;
    int dist = levenshtein_distance(a, b);
    return 1.0 - static_cast<double>(dist) / static_cast<double>(max_len);
}

}  // namespace sultan
