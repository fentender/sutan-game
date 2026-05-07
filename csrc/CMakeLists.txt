# sultan_core 内部逻辑（静态库，供 nanobind 模块和 C++ 测试共用）
add_library(sultan_core_lib STATIC
    diag.cpp
    resource_loader.cpp
    json/json_cleaner.cpp
    json/json_doc.cpp
    field_ops/field_ops.cpp
)
target_include_directories(sultan_core_lib PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/json
    ${CMAKE_CURRENT_SOURCE_DIR}/field_ops
)
target_link_libraries(sultan_core_lib PUBLIC yyjson)
set_target_properties(sultan_core_lib PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED ON
    POSITION_INDEPENDENT_CODE ON
)

# nanobind Python 模块
nanobind_add_module(sultan_core
    NB_STATIC
    bindings.cpp
)
target_link_libraries(sultan_core PRIVATE sultan_core_lib)

install(TARGETS sultan_core LIBRARY DESTINATION .)
