#include <nanobind/nanobind.h>
#include <nanobind/stl/function.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/unordered_map.h>
#include <nanobind/stl/vector.h>

#include "diag.h"
#include "field_ops.h"
#include "json_doc.h"

namespace nb = nanobind;
using namespace sultan;

static void bind_diag(nb::module_& parent) {
    auto m = parent.def_submodule("diag");

    nb::enum_<DiagLevel>(m, "Level")
        .value("INFO", DiagLevel::Info)
        .value("WARN", DiagLevel::Warn)
        .value("ERROR", DiagLevel::Error);

    nb::class_<DiagMessage>(m, "Message")
        .def_ro("level", &DiagMessage::level)
        .def_ro("category", &DiagMessage::category)
        .def_ro("message", &DiagMessage::message)
        .def("__repr__", [](const DiagMessage& msg) {
            const char* lvl = msg.level == DiagLevel::Error  ? "ERROR"
                              : msg.level == DiagLevel::Warn ? "WARN"
                                                             : "INFO";
            return std::string("<Message ") + lvl + " [" + msg.category +
                   "] " + msg.message + ">";
        });

    nb::class_<DiagManager>(m, "DiagManager")
        .def("snapshot",
             nb::overload_cast<>(&DiagManager::snapshot))
        .def("snapshot",
             nb::overload_cast<const std::vector<std::string>&>(
                 &DiagManager::snapshot))
        .def("set_callback", &DiagManager::set_callback)
        .def("clear_callback", &DiagManager::clear_callback);

    m.def("get_manager", &diag_manager, nb::rv_policy::reference);

    m.def("_test_emit",
          [](DiagLevel level, const std::string& category,
             const std::string& msg, bool notify) {
              diag_manager().emit(level, category, msg, notify);
          },
          nb::arg("level"), nb::arg("category"), nb::arg("msg"),
          nb::arg("notify") = false);
}

static void bind_json(nb::module_& parent) {
    auto m = parent.def_submodule("json");

    nb::class_<JsonDoc>(m, "JsonDoc")
        .def_static("parse", &JsonDoc::parse,
            nb::arg("text"), nb::arg("clean") = true)
        .def_static("parse_file", &JsonDoc::parse_file,
            nb::arg("path"), nb::arg("clean") = true)
        .def("to_string", &JsonDoc::to_string,
            nb::arg("compact") = false)
        .def("valid", &JsonDoc::valid)
        .def("__repr__", [](const JsonDoc& d) {
            return d.valid()
                ? std::string("<JsonDoc valid>")
                : std::string("<JsonDoc invalid>");
        });
}

static void bind_field_ops(nb::module_& parent) {
    auto m = parent.def_submodule("field_ops");

    m.def("extract_string_values", &extract_string_values,
        nb::arg("doc"), nb::arg("field_name"));
    m.def("extract_int_values", &extract_int_values,
        nb::arg("doc"), nb::arg("field_name"));
    m.def("replace_field_ints", &replace_field_ints,
        nb::arg("doc"), nb::arg("field_name"), nb::arg("mapping"));
    m.def("replace_field_strs", &replace_field_strs,
        nb::arg("doc"), nb::arg("field_name"), nb::arg("mapping"));
    m.def("replace_root_keys", &replace_root_keys,
        nb::arg("doc"), nb::arg("mapping"));
}

NB_MODULE(sultan_core, m) {
    m.doc() = "苏丹的游戏 Mod 合并器 - C++ 加速层";
    m.attr("__version__") = "0.1.0";
    bind_diag(m);
    bind_json(m);
    bind_field_ops(m);
}
