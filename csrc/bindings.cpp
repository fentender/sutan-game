#include <nanobind/nanobind.h>

namespace nb = nanobind;

NB_MODULE(sultan_core, m) {
    m.doc() = "苏丹的游戏 Mod 合并器 - C++ 加速层";
    m.attr("__version__") = "0.1.0";
}
