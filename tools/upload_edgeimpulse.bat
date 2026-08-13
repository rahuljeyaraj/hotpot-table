@echo off
rem tools/upload_edgeimpulse.bat -- export captures + push to Edge Impulse.
rem
rem One-time setup (needs a fresh top-level process -- a new VS Code
rem integrated terminal tab does NOT pick this up, only a relaunched
rem VS Code / a new cmd.exe opened outside it):
rem     setx EI_API_KEY ei_your_key_here
rem
rem To unblock the *current* terminal without restarting anything:
rem     $env:EI_API_KEY = "ei_your_key_here"
rem
rem Then just run this file from anywhere:
rem     tools\upload_edgeimpulse.bat
rem
rem Why we expand the glob ourselves instead of handing edge-impulse-uploader
rem a "*.jpg": in CLI v1.39.2, uploader.js calls fs.statSync() directly on
rem the raw argument before its own "Windows doesn't expand globs" fallback
rem ever runs -- so a literal "*.jpg" argument throws ENOENT and the
rem fallback code is unreachable. cmd's `for` loop does real filesystem
rem expansion (unlike handing a wildcard straight to a child process), so we
rem build the file list ourselves and pass real paths.
rem
rem Chunked at 100 files per call: doc 19.2 targets 150+ images/class, and a
rem single command line with 150+ full paths risks Windows' ~8191-char
rem command-line limit.

setlocal enabledelayedexpansion

if "%EI_API_KEY%"=="" (
    echo EI_API_KEY is not set in this terminal.
    echo One-time, needs a fresh terminal/VS Code restart to take effect elsewhere:
    echo     setx EI_API_KEY ei_your_key_here
    echo Or, just to unblock this terminal right now:
    echo     $env:EI_API_KEY = "ei_your_key_here"
    exit /b 1
)

cd /d "%~dp0.."

echo Exporting captures to datasets\export_ei ...
python tools\export_edgeimpulse.py
if errorlevel 1 exit /b 1

if not exist datasets\export_ei (
    echo datasets\export_ei not found - nothing to upload.
    exit /b 1
)

set "CHUNK_SIZE=100"

for /d %%D in (datasets\export_ei\*) do (
    echo.
    echo === %%~nxD ===
    set "FILES="
    set "COUNT=0"
    for %%F in (datasets\export_ei\%%~nxD\*.jpg) do (
        set "FILES=!FILES! "%%F""
        set /a COUNT+=1
        if !COUNT! geq %CHUNK_SIZE% (
            edge-impulse-uploader --api-key %EI_API_KEY% --category split --label %%~nxD !FILES!
            if errorlevel 1 (
                echo Upload failed for %%~nxD
                exit /b 1
            )
            set "FILES="
            set "COUNT=0"
        )
    )
    if defined FILES (
        edge-impulse-uploader --api-key %EI_API_KEY% --category split --label %%~nxD !FILES!
        if errorlevel 1 (
            echo Upload failed for %%~nxD
            exit /b 1
        )
    )
)

echo.
echo Done.
endlocal
