include(CTest)
include(Catch)

add_executable(sultan_tests
    test_diag.cpp
)
target_link_libraries(sultan_tests PRIVATE
    sultan_core_lib
    Catch2::Catch2WithMain
)
set_target_properties(sultan_tests PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED ON
)

catch_discover_tests(sultan_tests)
