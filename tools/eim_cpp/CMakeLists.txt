# tools/eim_cpp/CMakeLists.txt — compiles vendor/ (the Edge Impulse
# "C++ library" export, doc section 19.4's `backend_ei.py` runtime) into a
# small CLI, `classify`, that classifier/backend_ei.py shells out to.
#
# Why a hand-written CMakeLists instead of the one the zip ships
# (vendor/CMakeLists.txt): that one is Zephyr's own integration recipe —
# it requires a pre-existing `app` Zephyr target and pulls the SDK in via
# Zephyr's module system, because it was generated while the Studio
# project's top-right "Target device" was set to a Himax WiseEye2 dev
# board (a Zephyr target). The SDK dropped alongside it is NOT
# MCU-exclusive though — vendor/edge-impulse-sdk/porting/ ships posix,
# mingw32, clib, arduino, zephyr and half a dozen others side by side, all
# guarded by their own `#if EI_PORTING_XXX == 1`, so only the one whose
# macro is defined actually compiles any content (see main.cpp's
# EI_PORTING_CLIB define below). This file adapts the SDK's OWN generic,
# non-Zephyr recipe (vendor/edge-impulse-sdk/cmake/zephyr/CMakeLists.txt's
# `else()` branch, the one taken when `CONFIG_EDGE_IMPULSE_SDK` is unset —
# i.e. "use edge-impulse-sdk as a c++ to be integrated directly into the
# project") to a plain executable instead of a Zephyr library.
#
# clib, not posix or mingw32, is the deliberate choice: posix's porting
# layer assumes pthreads/unistd.h (fine for the ODYSSEY, doc section 1.4,
# but not attempted here), and mingw32's assumes MinGW's unistd.h shim,
# which plain MSVC (this file's actual compiler, doc's CLAUDE.md: "this
# dev machine is Windows") does not provide. clib's porting file
# (vendor/edge-impulse-sdk/porting/clib/ei_classifier_porting.cpp) only
# calls the standard C library — it is the one porting layer guaranteed
# to build under cl.exe with no POSIX shim at all.
cmake_minimum_required(VERSION 3.13.1)
project(hotpot_classify CXX C)

# model-parameters/model_variables.h uses C-style designated initializers
# (`{ .name = "Image", .axis = 0 }`) — gcc/clang accept that in C++14 as an
# extension (which is presumably what the SDK was written/tested against),
# but MSVC only accepts it from C++20 on (error C7555 below /std:c++20).
# C++20, not a narrower -std flag some MSVC-only shim, because the fix
# needs to apply to every vendored file compiled the same way regardless
# of which language mode each was originally written for.
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(EI_SDK_FOLDER ${CMAKE_CURRENT_SOURCE_DIR}/vendor/edge-impulse-sdk)
set(EI_MODEL_FOLDER ${CMAKE_CURRENT_SOURCE_DIR}/vendor/tflite-model)
set(EI_PARAMS_FOLDER ${CMAKE_CURRENT_SOURCE_DIR}/vendor/model-parameters)

include(${EI_SDK_FOLDER}/cmake/utils.cmake)

add_executable(classify main.cpp)

target_include_directories(classify PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/vendor
)

# EON-compiled (model_metadata.h: EI_CLASSIFIER_COMPILED == 1), so no
# TFLite op resolver/interpreter is linked (EI_CLASSIFIER_HAS_TFLITE_OPS_
# RESOLVER == 0 in that same file) — the recursive .cpp/.cc glob below still
# picks up the wider SDK (DSP feature extraction, quantization, the
# classifier core) exactly the way the SDK's own non-Zephyr integration
# recipe does; it is not this file inventing a narrower one.
RECURSIVE_FIND_FILE_APPEND(EI_SOURCE_FILES "${EI_SDK_FOLDER}" "*.cpp")
RECURSIVE_FIND_FILE_APPEND(EI_SOURCE_FILES "${EI_SDK_FOLDER}" "*.cc")

# The SDK's own generic (non-Zephyr) recipe also globs CMSIS/DSP/Source's
# Transform/CommonTables/BasicMath/etc. .c files in unconditionally — left
# out here on purpose. This impulse has one DSP block, "Image" (doc section
# 19.2 — squash-resize + RGB, no spectral/MFCC block), and CMSIS-DSP's
# transform functions exist for the FFT-based blocks a voice/audio impulse
# would use (doc section 19.3). Pulling them in would mean pulling in
# CMSIS's ARM-only headers (`__attribute__((packed))` on `T_UINT32`,
# `__ALIGNED(x)`) for code this impulse never calls — better not compiled
# at all than compiled and quietly relying on the attribute-blanking below.
list(APPEND EI_SOURCE_FILES "${EI_SDK_FOLDER}/tensorflow/lite/c/common.c")

# vendor/edge-impulse-sdk/porting/clib/ ships DebugLog() in BOTH
# ei_classifier_porting.cpp AND debug_log.cpp, both guarded by the same
# `#if EI_PORTING_CLIB == 1` — a genuine duplicate inside the SDK's own
# clib layer, not something introduced by this file's globbing (confirmed:
# linker reported it as multiply-defined between exactly those two .obj
# files, both compiled from the one porting/clib/ directory). Every other
# porting/*/debug_log.cpp is inert (its own macro is never defined), so
# only clib's needs dropping.
list(REMOVE_ITEM EI_SOURCE_FILES "${EI_SDK_FOLDER}/porting/clib/debug_log.cpp")

file(GLOB EI_MODEL_SOURCES "${EI_MODEL_FOLDER}/*.cpp")

target_sources(classify PRIVATE ${EI_SOURCE_FILES} ${EI_MODEL_SOURCES})

target_compile_definitions(classify PRIVATE
    EI_PORTING_CLIB=1
)

if (MSVC)
    # The SDK is GCC/Clang-written throughout and leans on raw
    # `__attribute__((...))` with no MSVC fallback anywhere in it (unlike
    # e.g. EIDSP_SIGNAL_C_FN_POINTER, which IS guarded per-compiler) —
    # cl.exe has no such keyword at all, so the first occurrence
    # (ei_constants.h) fails to parse and every later one cascades into
    # nonsense "redefinition" errors. Defining the macro away is the
    # standard fix for vendoring GCC-flavoured C/C++ under MSVC.
    #
    # This DOES reach a layout-affecting use, not just optimizer hints
    # (`unused`/`warn_unused_result`/`format`): `tflite-model`'s own
    # `#define ALIGN(X) __attribute__((aligned(X)))` on the tensor-arena
    # buffers. Checked, not assumed — an earlier version of this comment
    # claimed no layout-affecting use existed in this export at all, which
    # a grep for `__attribute__((packed` / `aligned(` proved wrong (CMSIS's
    # `T_UINT32`/`__ALIGNED`, excluded above by dropping CMSIS entirely, and
    # this ALIGN one, which is NOT excludable — the model file needs it).
    # Blanking it is still safe on this project's two real targets
    # specifically: x86 (this dev machine and the ODYSSEY, doc section 1.4)
    # has no alignment-fault requirement, so an unaligned tensor-arena
    # buffer is a possible speed cost, never a correctness one. It would
    # NOT be safe to reuse this CMakeLists unmodified for an ARM build.
    # Neither target_compile_definitions NOR a raw /D flag actually works
    # here — confirmed empirically twice: the first came from
    # target_compile_definitions, which CMake silently drops for a define
    # name containing `(...)`; the second was `/D__attribute__(x)=` passed
    # verbatim via target_compile_options (visible, correctly spelled, in
    # the generated flags.make) and the build failed identically anyway.
    # cl.exe simply does not support defining a function-like macro from
    # the command line at all, unlike gcc/clang. A force-included header
    # is the fix that actually takes effect — see that file's own comment.
    target_compile_options(classify PRIVATE
        "/FI${CMAKE_CURRENT_SOURCE_DIR}/msvc_gcc_attribute_compat.h")
    # The SDK is written against POSIX/GCC assumptions in a few spots
    # (e.g. treating narrowing conversions and unknown-pragma as fine) —
    # match the warning level example-standalone-inferencing-linux uses
    # rather than fighting MSVC's stricter defaults for a vendored tree
    # we do not maintain.
    target_compile_options(classify PRIVATE /bigobj /EHsc /w)
else()
    target_compile_options(classify PRIVATE -w)
endif()
