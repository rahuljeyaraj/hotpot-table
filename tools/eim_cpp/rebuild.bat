@echo off
rem tools/eim_cpp/rebuild.bat -- (re)configure and build `classify[.exe]`
rem from whatever tools/eim_cpp/vendor/ currently holds.
rem
rem Exists so core/main.py's _handle_ei_download (doc section 19.5) can
rem drive an MSVC/nmake build from a plain subprocess call: cl.exe and
rem nmake.exe are not on PATH outside a Developer Command Prompt, and the
rem env vars vcvars64.bat sets (INCLUDE/LIB/PATH) only apply within the
rem process that sourced it -- so vcvars64.bat and the actual build must
rem run in the SAME cmd.exe invocation, which is what this script is for.
rem vswhere.exe (ships with every VS installer since 2017) finds the
rem install path instead of a hardcoded "\18\Community\..." that breaks on
rem the next VS upgrade or a different edition (Community/Pro/Enterprise).
rem
rem Delayed expansion (!VAR! instead of %VAR%) is required throughout, not
rem a style choice: every path here is rooted at "Program Files (x86)" /
rem "Program Files", and %VAR%-style expansion happens BEFORE cmd.exe
rem parses an if(...)/for(...) block's own parentheses, so a path
rem containing literal "(x86)" text corrupts the block's paren matching
rem the moment it's substituted in with %. !VAR! expands at execution
rem time, after the block was already parsed, which sidesteps that.
rem
rem Always reconfigures before building, not just on a fresh build/ dir:
rem tools/eim_cpp/CMakeLists.txt globs vendor/tflite-model/*.cpp, and that
rem glob is evaluated at CONFIGURE time and baked into the generated
rem Makefile. A redeploy swaps in a differently-named
rem tflite_learn_<project>_<n>_compiled.cpp (models/README.md's
rem provenance log has the exact names) -- skip the reconfigure and nmake
rem keeps building the PREVIOUS model's source file, or fails outright once
rem that file is gone. Reconfiguring against an existing CMakeCache.txt is
rem cheap (no compiler re-detection), so there's no reason to special-case
rem "first build" vs "redeploy" here.
setlocal EnableDelayedExpansion

set "HERE=%~dp0"
set "BUILD=%HERE%build"

set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "!VSWHERE!" (
    echo rebuild.bat: vswhere.exe not found at "!VSWHERE!" -- is Visual Studio installed? 1>&2
    exit /b 1
)

set "VSINSTALL="
for /f "usebackq tokens=*" %%i in (`"!VSWHERE!" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSINSTALL=%%i"
if "!VSINSTALL!"=="" (
    echo rebuild.bat: vswhere found no Visual Studio install with the "C++ build tools" ^(VC.Tools.x86.x64^) component 1>&2
    exit /b 1
)

set "VCVARS=!VSINSTALL!\VC\Auxiliary\Build\vcvars64.bat"
if not exist "!VCVARS!" (
    echo rebuild.bat: !VCVARS! not found 1>&2
    exit /b 1
)
call "!VCVARS!" >nul
if errorlevel 1 (
    echo rebuild.bat: vcvars64.bat exited non-zero 1>&2
    exit /b 1
)

set "CMAKE=!VSINSTALL!\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
if not exist "!CMAKE!" (
    rem Fall back to whatever `cmake` vcvars64.bat put on PATH, if anything
    rem -- the VS-bundled copy above is just the one every VC.Tools.x86.x64
    rem install is confirmed (2026-08-24, this file's own testing) to ship.
    set "CMAKE=cmake"
)

echo rebuild.bat: configuring...
"!CMAKE!" -G "NMake Makefiles" -S "!HERE!." -B "!BUILD!"
if errorlevel 1 (
    echo rebuild.bat: cmake configure failed 1>&2
    exit /b 1
)

echo rebuild.bat: building...
"!CMAKE!" --build "!BUILD!"
if errorlevel 1 (
    echo rebuild.bat: cmake build failed 1>&2
    exit /b 1
)

echo rebuild.bat: OK
exit /b 0
