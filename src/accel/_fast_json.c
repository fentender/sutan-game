/*
 * _fast_json - C accelerated JSON text cleaning functions
 *
 * strip_js_comments: strip // line comments, preserve // inside strings
 * strip_trailing_commas: remove commas before } or ]
 * fix_missing_commas: insert missing commas between key-value pairs
 * has_duplist: check if a Python object tree contains DupList instances
 * pairs_hook: json.loads object_pairs_hook detecting duplicate keys
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

/* ===== DupList type reference (lazy init) ===== */
static PyObject *DupListType = NULL;

static int
ensure_duplist_type(void)
{
    if (DupListType)
        return 0;
    PyObject *mod = PyImport_ImportModule("src.core.json_parser");
    if (!mod) return -1;
    DupListType = PyObject_GetAttrString(mod, "DupList");
    Py_DECREF(mod);
    return DupListType ? 0 : -1;
}

/* ===== Helper: skip a quoted string, returning position after closing " ===== */
static Py_ssize_t
skip_string(const char *text, Py_ssize_t i, Py_ssize_t len)
{
    /* i points to opening " */
    i++;
    while (i < len) {
        if (text[i] == '\\') {
            i += 2;
        } else if (text[i] == '"') {
            return i + 1;
        } else {
            i++;
        }
    }
    return i;
}

/* ===== Helper: copy a quoted string to output ===== */
static Py_ssize_t
copy_string(const char *text, Py_ssize_t i, Py_ssize_t len,
            char *out, Py_ssize_t oi)
{
    /* i points to opening " */
    out[oi++] = text[i++];
    while (i < len) {
        char c = text[i];
        if (c == '\\') {
            out[oi++] = c; i++;
            if (i < len) { out[oi++] = text[i]; i++; }
        } else if (c == '"') {
            out[oi++] = c; i++;
            break;
        } else {
            out[oi++] = c; i++;
        }
    }
    /* Return packed: high bits = new i, needs separate tracking */
    /* Actually, use struct or out-params instead */
    return oi; /* caller must also track i separately */
}

/* ===== strip_js_comments ===== */
static PyObject *
fast_strip_js_comments(PyObject *self, PyObject *args)
{
    const char *text;
    Py_ssize_t len;

    if (!PyArg_ParseTuple(args, "s#", &text, &len))
        return NULL;

    /* Fast path: no // at all */
    {
        int found = 0;
        for (Py_ssize_t i = 0; i < len - 1; i++) {
            if (text[i] == '/' && text[i + 1] == '/') {
                found = 1;
                break;
            }
        }
        if (!found)
            return PyUnicode_FromStringAndSize(text, len);
    }

    char *out = (char *)PyMem_Malloc(len);
    if (!out) return PyErr_NoMemory();

    Py_ssize_t oi = 0, i = 0;

    while (i < len) {
        char ch = text[i];

        if (ch == '"') {
            out[oi++] = ch; i++;
            while (i < len) {
                char c = text[i];
                if (c == '\\') {
                    out[oi++] = c; i++;
                    if (i < len) { out[oi++] = text[i]; i++; }
                } else if (c == '"') {
                    out[oi++] = c; i++;
                    break;
                } else {
                    out[oi++] = c; i++;
                }
            }
        } else if (ch == '/' && i + 1 < len && text[i + 1] == '/') {
            i += 2;
            while (i < len && text[i] != '\n' && text[i] != '\r')
                i++;
            if (i < len && text[i] == '\r')
                i++;
        } else {
            out[oi++] = ch; i++;
        }
    }

    PyObject *result = PyUnicode_FromStringAndSize(out, oi);
    PyMem_Free(out);
    return result;
}

/* ===== strip_trailing_commas ===== */
/* Equivalent to: re.sub(r',(\s*[}\]])', r'\1', text)
 * Single pass: track string boundaries, find , followed by whitespace + } or ]
 */
static PyObject *
fast_strip_trailing_commas(PyObject *self, PyObject *args)
{
    const char *text;
    Py_ssize_t len;

    if (!PyArg_ParseTuple(args, "s#", &text, &len))
        return NULL;

    /* Worst case: same length (no commas removed) */
    char *out = (char *)PyMem_Malloc(len);
    if (!out) return PyErr_NoMemory();

    Py_ssize_t oi = 0, i = 0;

    while (i < len) {
        char ch = text[i];

        if (ch == '"') {
            /* Copy string verbatim */
            out[oi++] = ch; i++;
            while (i < len) {
                char c = text[i];
                if (c == '\\') {
                    out[oi++] = c; i++;
                    if (i < len) { out[oi++] = text[i]; i++; }
                } else if (c == '"') {
                    out[oi++] = c; i++;
                    break;
                } else {
                    out[oi++] = c; i++;
                }
            }
        } else if (ch == ',') {
            /* Check if this comma is followed by optional whitespace + } or ] */
            Py_ssize_t j = i + 1;
            while (j < len && (text[j] == ' ' || text[j] == '\t' ||
                               text[j] == '\r' || text[j] == '\n'))
                j++;
            if (j < len && (text[j] == '}' || text[j] == ']')) {
                /* Skip the comma (trailing comma) */
                i++;
            } else {
                out[oi++] = ch; i++;
            }
        } else {
            out[oi++] = ch; i++;
        }
    }

    PyObject *result = PyUnicode_FromStringAndSize(out, oi);
    PyMem_Free(out);
    return result;
}

/* ===== fix_missing_commas ===== */
/* Character-level state machine: insert missing commas between adjacent
 * key-value pairs. Equivalent to the Python fix_missing_commas function.
 */

static int
is_ws(char c)
{
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

static Py_ssize_t
skip_ws(const char *text, Py_ssize_t pos, Py_ssize_t n)
{
    while (pos < n && is_ws(text[pos]))
        pos++;
    return pos;
}

/* Check if pos points to "key": pattern */
static int
is_key_start(const char *text, Py_ssize_t pos, Py_ssize_t n)
{
    if (pos >= n || text[pos] != '"')
        return 0;
    Py_ssize_t k = pos + 1;
    while (k < n) {
        if (text[k] == '\\') {
            k += 2;
        } else if (text[k] == '"') {
            k++;
            break;
        } else {
            k++;
        }
    }
    if (k > n) return 0;
    /* Skip whitespace after closing " */
    while (k < n && is_ws(text[k]))
        k++;
    return k < n && text[k] == ':';
}

/* Try to insert comma at pos if next non-ws is a key start */
static void
try_insert_comma(const char *text, Py_ssize_t pos, Py_ssize_t n,
                 char *out, Py_ssize_t *oi)
{
    Py_ssize_t j = skip_ws(text, pos, n);
    if (j < n && is_key_start(text, j, n))
        out[(*oi)++] = ',';
}

static PyObject *
fast_fix_missing_commas(PyObject *self, PyObject *args)
{
    const char *text;
    Py_ssize_t len;

    if (!PyArg_ParseTuple(args, "s#", &text, &len))
        return NULL;

    /* Worst case: every position gets a comma inserted → 2x length */
    char *out = (char *)PyMem_Malloc(len * 2 + 1);
    if (!out) return PyErr_NoMemory();

    Py_ssize_t oi = 0, i = 0;

    while (i < len) {
        char ch = text[i];

        if (ch == '"') {
            /* Copy complete string */
            out[oi++] = ch; i++;
            while (i < len) {
                char c = text[i];
                if (c == '\\') {
                    out[oi++] = c; i++;
                    if (i < len) { out[oi++] = text[i]; i++; }
                } else if (c == '"') {
                    out[oi++] = c; i++;
                    break;
                } else {
                    out[oi++] = c; i++;
                }
            }
            try_insert_comma(text, i, len, out, &oi);

        } else if (ch == ']' || ch == '}') {
            out[oi++] = ch; i++;
            try_insert_comma(text, i, len, out, &oi);

        } else if ((ch >= '0' && ch <= '9') ||
                   (ch == '-' && i + 1 < len && text[i+1] >= '0' && text[i+1] <= '9')) {
            /* Number */
            out[oi++] = ch; i++;
            while (i < len && ((text[i] >= '0' && text[i] <= '9') ||
                               text[i] == '.' || text[i] == 'e' || text[i] == 'E' ||
                               text[i] == '+' || text[i] == '-')) {
                out[oi++] = text[i]; i++;
            }
            try_insert_comma(text, i, len, out, &oi);

        } else if (i + 4 <= len && text[i]=='t' && text[i+1]=='r' &&
                   text[i+2]=='u' && text[i+3]=='e' &&
                   (i+4 >= len || !(text[i+4] >= 'a' && text[i+4] <= 'z') &&
                    !(text[i+4] >= 'A' && text[i+4] <= 'Z') &&
                    !(text[i+4] >= '0' && text[i+4] <= '9'))) {
            out[oi++] = 't'; out[oi++] = 'r'; out[oi++] = 'u'; out[oi++] = 'e';
            i += 4;
            try_insert_comma(text, i, len, out, &oi);

        } else if (i + 5 <= len && text[i]=='f' && text[i+1]=='a' &&
                   text[i+2]=='l' && text[i+3]=='s' && text[i+4]=='e' &&
                   (i+5 >= len || !(text[i+5] >= 'a' && text[i+5] <= 'z') &&
                    !(text[i+5] >= 'A' && text[i+5] <= 'Z') &&
                    !(text[i+5] >= '0' && text[i+5] <= '9'))) {
            out[oi++] = 'f'; out[oi++] = 'a'; out[oi++] = 'l';
            out[oi++] = 's'; out[oi++] = 'e';
            i += 5;
            try_insert_comma(text, i, len, out, &oi);

        } else if (i + 4 <= len && text[i]=='n' && text[i+1]=='u' &&
                   text[i+2]=='l' && text[i+3]=='l' &&
                   (i+4 >= len || !(text[i+4] >= 'a' && text[i+4] <= 'z') &&
                    !(text[i+4] >= 'A' && text[i+4] <= 'Z') &&
                    !(text[i+4] >= '0' && text[i+4] <= '9'))) {
            out[oi++] = 'n'; out[oi++] = 'u'; out[oi++] = 'l'; out[oi++] = 'l';
            i += 4;
            try_insert_comma(text, i, len, out, &oi);

        } else {
            out[oi++] = ch; i++;
        }
    }

    PyObject *result = PyUnicode_FromStringAndSize(out, oi);
    PyMem_Free(out);
    return result;
}

/* ===== has_duplist ===== */
/* Recursive tree walk checking if any node is a DupList instance */
static int
_has_duplist_inner(PyObject *obj)
{
    if (PyObject_IsInstance(obj, DupListType))
        return 1;

    if (PyDict_Check(obj)) {
        PyObject *key, *value;
        Py_ssize_t pos = 0;
        while (PyDict_Next(obj, &pos, &key, &value)) {
            if (_has_duplist_inner(value))
                return 1;
        }
        return 0;
    }

    if (PyList_Check(obj)) {
        Py_ssize_t n = PyList_GET_SIZE(obj);
        for (Py_ssize_t i = 0; i < n; i++) {
            if (_has_duplist_inner(PyList_GET_ITEM(obj, i)))
                return 1;
        }
        return 0;
    }

    return 0;
}

static PyObject *
fast_has_duplist(PyObject *self, PyObject *arg)
{
    if (ensure_duplist_type() < 0)
        return NULL;
    int result = _has_duplist_inner(arg);
    if (result < 0) return NULL;
    return PyBool_FromLong(result);
}

/* ===== pairs_hook ===== */
/* object_pairs_hook for json.loads: detect duplicate keys, build DupList.
 * Equivalent to Python _pairs_hook but as C PyCFunction for speed. */
static PyObject *
fast_pairs_hook(PyObject *self, PyObject *args)
{
    PyObject *pairs;
    if (!PyArg_ParseTuple(args, "O", &pairs))
        return NULL;

    if (ensure_duplist_type() < 0)
        return NULL;

    PyObject *result = PyDict_New();
    if (!result) return NULL;

    PyObject *iter = PyObject_GetIter(pairs);
    if (!iter) { Py_DECREF(result); return NULL; }

    PyObject *item;
    while ((item = PyIter_Next(iter)) != NULL) {
        PyObject *key = PyTuple_GET_ITEM(item, 0);
        PyObject *value = PyTuple_GET_ITEM(item, 1);

        /* Check if key already exists */
        PyObject *existing = PyDict_GetItemWithError(result, key);
        if (existing) {
            /* Duplicate key: build or extend DupList */
            int is_dup = PyObject_IsInstance(existing, DupListType);
            if (is_dup < 0) {
                Py_DECREF(item); Py_DECREF(iter); Py_DECREF(result);
                return NULL;
            }
            if (is_dup) {
                /* Append to existing DupList */
                if (PyList_Append(existing, value) < 0) {
                    Py_DECREF(item); Py_DECREF(iter); Py_DECREF(result);
                    return NULL;
                }
            } else {
                /* Create new DupList([existing, value]) */
                PyObject *dup_args = PyTuple_New(1);
                if (!dup_args) {
                    Py_DECREF(item); Py_DECREF(iter); Py_DECREF(result);
                    return NULL;
                }
                PyObject *init_list = PyList_New(2);
                if (!init_list) {
                    Py_DECREF(dup_args);
                    Py_DECREF(item); Py_DECREF(iter); Py_DECREF(result);
                    return NULL;
                }
                Py_INCREF(existing);
                Py_INCREF(value);
                PyList_SET_ITEM(init_list, 0, existing);
                PyList_SET_ITEM(init_list, 1, value);
                PyTuple_SET_ITEM(dup_args, 0, init_list);

                PyObject *dup = PyObject_Call(DupListType, dup_args, NULL);
                Py_DECREF(dup_args);
                if (!dup) {
                    Py_DECREF(item); Py_DECREF(iter); Py_DECREF(result);
                    return NULL;
                }
                PyDict_SetItem(result, key, dup);
                Py_DECREF(dup);
            }
        } else {
            if (PyErr_Occurred()) {
                Py_DECREF(item); Py_DECREF(iter); Py_DECREF(result);
                return NULL;
            }
            PyDict_SetItem(result, key, value);
        }
        Py_DECREF(item);
    }
    Py_DECREF(iter);

    if (PyErr_Occurred()) {
        Py_DECREF(result);
        return NULL;
    }

    return result;
}

/* ===== Module definition ===== */

static PyMethodDef methods[] = {
    {"strip_js_comments", fast_strip_js_comments, METH_VARARGS,
     "Strip // line comments, preserve // inside strings"},
    {"strip_trailing_commas", fast_strip_trailing_commas, METH_VARARGS,
     "Remove trailing commas before } or ]"},
    {"fix_missing_commas", fast_fix_missing_commas, METH_VARARGS,
     "Insert missing commas between adjacent key-value pairs"},
    {"has_duplist", fast_has_duplist, METH_O,
     "Check if object tree contains DupList instances"},
    {"pairs_hook", fast_pairs_hook, METH_VARARGS,
     "json.loads object_pairs_hook with duplicate key detection"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_fast_json",
    "C accelerated JSON text cleaning",
    -1,
    methods
};

PyMODINIT_FUNC
PyInit__fast_json(void)
{
    return PyModule_Create(&module);
}
