#include <nanobind/nanobind.h>

#include "diag.h"
#include "json_ops.h"
#include "json_doc.h"
#include "batch_parse.h"
#include "change_kind.h"
#include "json_state.h"
#include "state_formatter.h"
#include "delta_node.h"
#include "compute_delta.h"
#include "apply_delta.h"
#include "array_match.h"
#include "perf.h"

NB_MAKE_OPAQUE(std::unordered_map<std::string, sultan::DeltaNodePtr>)
NB_MAKE_OPAQUE(std::vector<sultan::DeltaNodePtr>)

#include <nanobind/stl/function.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/unordered_map.h>
#include <nanobind/stl/vector.h>

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

static PyObject* s_parse_error = nullptr;

static void bind_json(nb::module_& parent) {
    auto m = parent.def_submodule("json");

    s_parse_error = PyErr_NewExceptionWithDoc(
        "sultan_core.json.ParseError",
        "JSON 解析错误，携带 .lineno 和 .msg 属性",
        PyExc_ValueError, nullptr);
    Py_INCREF(s_parse_error);
    m.attr("ParseError") = nb::handle(s_parse_error);

    nb::register_exception_translator(
        [](const std::exception_ptr& p, void*) {
            try {
                std::rethrow_exception(p);
            } catch (const JsonParseError& e) {
                PyObject* exc = PyObject_CallFunction(
                    s_parse_error, "s", e.what());
                PyObject_SetAttrString(
                    exc, "lineno",
                    PyLong_FromSsize_t(static_cast<Py_ssize_t>(e.line)));
                PyObject_SetAttrString(
                    exc, "msg",
                    PyUnicode_FromString(e.detail.c_str()));
                PyErr_SetObject(s_parse_error, exc);
                Py_DECREF(exc);
            }
        });

    nb::class_<BatchHandle>(m, "BatchHandle")
        .def("total", &BatchHandle::total)
        .def("completed", &BatchHandle::completed)
        .def("done", &BatchHandle::done)
        .def("wait", &BatchHandle::wait)
        .def("take_doc", &BatchHandle::take_doc, nb::arg("index"))
        .def("error", &BatchHandle::error, nb::arg("index"));

    nb::class_<JsonDoc>(m, "JsonDoc")
        .def_static("parse", &JsonDoc::parse,
            nb::arg("text"), nb::arg("clean") = true)
        .def_static("parse_file", &JsonDoc::parse_file,
            nb::arg("path"), nb::arg("clean") = true)
        .def_static("batch_parse_files", &JsonDoc::batch_parse_files,
            nb::arg("paths"), nb::arg("async_") = true,
            nb::rv_policy::take_ownership)
        .def("to_string", &JsonDoc::to_string,
            nb::arg("compact") = false)
        .def("valid", &JsonDoc::valid)
        .def("__repr__", [](const JsonDoc& d) {
            return d.valid()
                ? std::string("<JsonDoc valid>")
                : std::string("<JsonDoc invalid>");
        });
}

static void bind_json_ops(nb::module_& parent) {
    auto m = parent.def_submodule("json_ops");

    m.def("extract_string_values", &extract_string_values,
        nb::arg("doc"), nb::arg("field_name"));
    m.def("extract_int_values", &extract_int_values,
        nb::arg("doc"), nb::arg("field_name"));
    m.def("extract_root_keys", &extract_root_keys,
        nb::arg("doc"));
    m.def("replace_field_ints", &replace_field_ints,
        nb::arg("doc"), nb::arg("field_name"), nb::arg("mapping"));
    m.def("replace_field_strs", &replace_field_strs,
        nb::arg("doc"), nb::arg("field_name"), nb::arg("mapping"));
    m.def("replace_root_keys", &replace_root_keys,
        nb::arg("doc"), nb::arg("mapping"));
    m.def("remap_all_ints", &remap_all_ints,
        nb::arg("doc"), nb::arg("mapping"));
    m.def("remap_all_str_ids", &remap_all_str_ids,
        nb::arg("doc"), nb::arg("mapping"));
    m.def("extract_root_field_ints", &extract_root_field_ints,
        nb::arg("doc"), nb::arg("field_name"));
    m.def("extract_root_field_strs", &extract_root_field_strs,
        nb::arg("doc"), nb::arg("field_name"));
    m.def("classify_json", &classify_json,
        nb::arg("doc"));
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
        .def("to_doc", &JsonState::to_doc)
        .def("format", &JsonState::format, nb::arg("highlight_version"))
        .def("clone", &JsonState::clone)
        .def("valid", &JsonState::valid);
}

static std::vector<FileGroupInput> unpack_inputs(
    const std::vector<MergeMode>& modes,
    const std::vector<nb::object>& mod_docs,
    const std::vector<nb::object>& hist_docs)
{
    std::vector<FileGroupInput> inputs;
    inputs.reserve(modes.size());
    for (size_t i = 0; i < modes.size(); ++i) {
        inputs.push_back({
            modes[i],
            mod_docs[i].is_none()
                ? nullptr
                : &nb::cast<const JsonDoc&>(mod_docs[i]),
            hist_docs[i].is_none()
                ? nullptr
                : &nb::cast<const JsonDoc&>(hist_docs[i]),
        });
    }
    return inputs;
}

static void bind_delta(nb::module_& parent) {
    auto m = parent.def_submodule("delta");

    nb::class_<DeltaDict>(m, "DeltaDict");

    nb::class_<FlatField>(m, "FlatField")
        .def_ro("path", &FlatField::path)
        .def_ro("kind", &FlatField::kind)
        .def_ro("value_str", &FlatField::value_str);

    m.def("compute_delta",
        [](const JsonDoc& base, const JsonDoc& mod,
           MergeMode merge_mode, bool skip_root_deletion) {
            return sultan::to_delta_dict(
                sultan::compute_delta(base, mod, merge_mode, skip_root_deletion));
        },
        nb::arg("base"), nb::arg("mod"),
        nb::arg("merge_mode") = MergeMode::Normal,
        nb::arg("skip_root_deletion") = false);

    m.def("apply_delta",
        [](const DeltaDict& delta, JsonState& state,
           int version, bool is_override) {
            apply_delta_to_state(state, delta, nullptr, version, is_override);
        },
        nb::arg("delta"), nb::arg("state"),
        nb::arg("version") = 0, nb::arg("is_override") = false);

    m.def("remap_delta",
        [](DeltaDict& delta, const JsonDoc& hist_base,
           const JsonDoc& current_base) -> bool {
            return remap_delta_to_current(delta, hist_base, current_base);
        },
        nb::arg("delta"), nb::arg("hist_base"), nb::arg("current_base"));

    m.def("flatten_delta", &sultan::flatten_delta,
        nb::arg("delta"));

    m.def("serialize_delta",
        [](const DeltaDict& delta) -> JsonDoc {
            return sultan::serialize_delta(delta);
        },
        nb::arg("delta"), nb::rv_policy::move);

    m.def("deserialize_delta",
        [](const JsonDoc& doc) {
            return sultan::to_delta_dict(sultan::deserialize_delta(doc));
        },
        nb::arg("doc"));

    m.def("process_file_group",
        [](const JsonDoc& base_doc,
           const std::vector<MergeMode>& modes,
           const std::vector<nb::object>& mod_docs,
           const std::vector<nb::object>& hist_docs)
        {
            return sultan::process_file_group(
                base_doc, unpack_inputs(modes, mod_docs, hist_docs));
        },
        nb::arg("base_doc"), nb::arg("modes"),
        nb::arg("mod_docs"), nb::arg("hist_docs"));

    m.def("batch_process_all_groups",
        [](const std::vector<nb::object>& base_docs,
           const std::vector<size_t>& group_offsets,
           const std::vector<MergeMode>& modes,
           const std::vector<nb::object>& mod_docs,
           const std::vector<nb::object>& hist_docs)
        {
            std::vector<const JsonDoc*> bases;
            bases.reserve(base_docs.size());
            for (auto& o : base_docs)
                bases.push_back(&nb::cast<const JsonDoc&>(o));
            return sultan::batch_process_all_groups(
                bases, group_offsets,
                unpack_inputs(modes, mod_docs, hist_docs));
        },
        nb::arg("base_docs"), nb::arg("group_offsets"),
        nb::arg("modes"), nb::arg("mod_docs"), nb::arg("hist_docs"));
}

static void bind_perf(nb::module_& parent) {
    auto m = parent.def_submodule("perf", "C++ 性能分析");
#ifdef SULTAN_PERF
    m.attr("available") = true;
    m.def("enable", []() {
        sultan::perf::Registry::instance().set_enabled(true);
    });
    m.def("disable", []() {
        sultan::perf::Registry::instance().set_enabled(false);
    });
    m.def("reset", []() {
        sultan::perf::Registry::instance().reset();
    });
    m.def("snapshot", []() {
        auto data = sultan::perf::Registry::instance().snapshot();
        std::vector<std::tuple<std::string, int64_t, double, double, double, double>> result;
        result.reserve(data.size());
        for (auto& e : data)
            result.emplace_back(e.name, e.call_count, e.total_us, e.avg_us, e.max_us, e.min_us);
        return result;
    });
    m.def("report", [](int top_n) {
        auto data = sultan::perf::Registry::instance().snapshot();
        std::string out;
        out += "C++ Performance Report\n";
        out += std::string(90, '=') + "\n";
        char buf[256];
        snprintf(buf, sizeof(buf), "%-32s %8s %12s %10s %10s %10s\n",
                 "Function", "Calls", "Total(ms)", "Avg(us)", "Max(us)", "Min(us)");
        out += buf;
        out += std::string(90, '-') + "\n";
        int count = 0;
        for (auto& e : data) {
            if (count >= top_n) break;
            snprintf(buf, sizeof(buf), "%-32s %8lld %12.1f %10.1f %10.1f %10.1f\n",
                     e.name.c_str(),
                     static_cast<long long>(e.call_count),
                     e.total_us / 1000.0,
                     e.avg_us, e.max_us, e.min_us);
            out += buf;
            ++count;
        }
        return out;
    }, nb::arg("top_n") = 20);
#else
    m.attr("available") = false;
    m.def("enable", []() {});
    m.def("disable", []() {});
    m.def("reset", []() {});
    m.def("snapshot", []() {
        return std::vector<std::tuple<std::string, int64_t, double, double, double, double>>{};
    });
    m.def("report", [](int) { return std::string("(perf not compiled)"); },
        nb::arg("top_n") = 20);
#endif
}

NB_MODULE(sultan_core, m) {
    m.doc() = "苏丹的游戏 Mod 合并器 - C++ 加速层";
    m.attr("__version__") = "0.1.0";
    bind_diag(m);
    bind_json(m);
    bind_json_ops(m);
    bind_state(m);
    bind_delta(m);
    bind_perf(m);
}
