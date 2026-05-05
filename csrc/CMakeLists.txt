nanobind_add_module(sultan_core
    NB_STATIC
    bindings.cpp
)

install(TARGETS sultan_core LIBRARY DESTINATION .)
