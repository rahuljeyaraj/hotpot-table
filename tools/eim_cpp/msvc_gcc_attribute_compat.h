// tools/eim_cpp/msvc_gcc_attribute_compat.h — force-included (CMakeLists'
// /FI flag) ahead of every other header in every translation unit.
//
// cl.exe cannot define a function-like macro from the command line at all
// (confirmed empirically: /D__attribute__(x)= parses without error but
// silently does not affect a single call site — flags.make showed it
// present verbatim, and the build failed identically with or without it).
// A force-included header is the standard fix for vendoring GCC/Clang
// -flavoured C/C++ under MSVC, and is what makes this actually take
// effect where the command-line attempt did not.
//
// See tools/eim_cpp/CMakeLists.txt's own comment for which uses in
// vendor/ this reaches (including the one layout-affecting one,
// tflite-model's tensor-arena ALIGN() macro) and why blanking it is safe
// specifically on this project's two x86 targets.
#ifndef HOTPOT_MSVC_GCC_ATTRIBUTE_COMPAT_H_
#define HOTPOT_MSVC_GCC_ATTRIBUTE_COMPAT_H_

#if defined(_MSC_VER) && !defined(__clang__)
#ifndef __attribute__
#define __attribute__(x)
#endif
#endif

#endif  // HOTPOT_MSVC_GCC_ATTRIBUTE_COMPAT_H_
