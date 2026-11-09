cmake_minimum_required(VERSION 4.4)

# Activation value for import std in CMake 4.4; set before compiler detection.
set(CMAKE_EXPERIMENTAL_CXX_IMPORT_STD "f35a9ac6-8463-4d38-8eec-5d6008153e7d")
project(study_workspace_ LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23 CACHE STRING "C++ standard for practice programs")
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_CXX_MODULE_STD ON)
set(STUDY_STARTUP_SOURCE "U01/reading/1.cpp" CACHE STRING
        "Source for the existing study_workspace_ CLion run configuration")

# Only personal unit directories are build inputs; materials has its own judge.
file(GLOB unit_dirs CONFIGURE_DEPENDS LIST_DIRECTORIES true
        "${CMAKE_CURRENT_SOURCE_DIR}/[Uu][0-9]*")

foreach(unit_dir IN LISTS unit_dirs)
    get_filename_component(unit_name "${unit_dir}" NAME)
    if(NOT IS_DIRECTORY "${unit_dir}" OR NOT unit_name MATCHES "^[Uu][0-9]+$")
        continue()
    endif()

    file(GLOB_RECURSE program_sources CONFIGURE_DEPENDS
            "${unit_dir}/*.cpp")
    foreach(source IN LISTS program_sources)
        file(RELATIVE_PATH relative_source "${CMAKE_CURRENT_SOURCE_DIR}" "${source}")
        string(REGEX REPLACE "\\.cpp$" "" target_name "${relative_source}")
        string(MAKE_C_IDENTIFIER "${target_name}" target_name)
        if(TARGET "${target_name}")
            message(FATAL_ERROR "Duplicate program target ${target_name}: ${relative_source}. Rename the file or directory.")
        endif()

        add_executable("${target_name}" "${source}")
        set_target_properties("${target_name}" PROPERTIES
                RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")

        # Keep the existing CLion configuration and its executable path working.
        # New programs are discovered above; do not append sources to this target.
        if(relative_source STREQUAL STUDY_STARTUP_SOURCE)
            add_executable(study_workspace_ "${source}")
        endif()
    endforeach()
endforeach()
