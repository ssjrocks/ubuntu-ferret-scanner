cmake_minimum_required(VERSION 3.20)
project(ferret_capture CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE RelWithDebInfo)
endif()

find_package(PkgConfig REQUIRED)
pkg_check_modules(LIBUSB REQUIRED IMPORTED_TARGET libusb-1.0)

add_compile_options(-Wall -Wextra)

add_executable(ferret-capture ferret_capture.cpp)
target_link_libraries(ferret-capture PRIVATE PkgConfig::LIBUSB)

enable_testing()
add_executable(test-uvc-payload test_uvc_payload.cpp)
target_compile_options(test-uvc-payload PRIVATE -UNDEBUG)
add_test(NAME uvc_payload COMMAND test-uvc-payload)
