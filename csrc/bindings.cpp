#include <nanobind/nanobind.h>
#include <nanobind/stl/function.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/unordered_map.h>
#include <nanobind/stl/vector.h>

#include "diag.h"
#include "field_ops.h"
#include "json_doc.h"
#include "change_kind.h"
#include "json_state.h"
#include "state_formatter.h"

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

static void bind_state(nb::module_& parent) {
    auto m = parent.def_submodule("state");

    nb::enum_<ChangeKind>(m, "ChangeKind", nb::is_arithmetic())
        .value("ORIGIN",    ChangeKind::Origin)
        .value("ADDED",     ChangeKind::Added)
        .value("DELETED",   ChangeKind::Deleted)
        .value("CHANGED",   ChangeKind::Changed)
        .value("MULTI_MOD", ChangeKind::MultiMod)
        .value("OVERRIDE",  ChangeKind::Override);

    m.def("base_kind",    &sultan::base_kind);
    m.def("change_flags", &sultan::change_flags);
    m.def("is_origin",    &sultan::is_origin);
    m.def("is_added",     &sultan::is_added);
    m.def("is_deleted",   &sultan::is_deleted);
    m.def("is_changed",   &sultan::is_changed);
    m.def("is_multi_mod", &sultan::is_multi_mod);
    m.def("is_override",  &sultan::is_override);

    nb::enum_<MergeMode>(m, "MergeMode")
        .value("NORMAL",   MergeMode::Normal)
        .value("SMART",    MergeMode::Smart)
        .value("REPLACE",  MergeMode::Replace)
        .value("ADAPTIVE", MergeMode::Adaptive);

    nb::class_<FormatResult>(m, "FormatResult")
        .def_ro("left_lines",  &FormatResult::left_lines)
        .def_ro("right_lines", &FormatResult::right_lines)
        .def_ro("left_kinds",  &FormatResult::left_kinds)
        .def_ro("right_kinds", &FormatResult::right_kinds)
        .def("__len__", &FormatResult::size);

    nb::class_<JsonState>(m, "JsonState")
        .def_static("from_doc", &JsonState::from_doc)
        .def_static("from_text", &JsonState::from_text,
            nb::arg("text"), nb::arg("clean") = true)
        .def_static("from_file", &JsonState::from_file,
            nb::arg("path"), nb::arg("clean") = true)
        .def("to_doc", &JsonState::to_doc)
        .def("format", &JsonState::format, nb::arg("highlight_version"))
        .def("clone", &JsonState::clone)
        .def("valid", &JsonState::valid);
}

NB_MODULE(sultan_core, m) {
    m.doc() = "苏丹的游戏 Mod 合并器 - C++ 加速层";
    m.attr("__version__") = "0.1.0";
    bind_diag(m);
    bind_json(m);
    bind_field_ops(m);
    bind_state(m);
}
