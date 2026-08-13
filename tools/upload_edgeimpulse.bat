@echo off
rem tools/upload_edgeimpulse.bat -- export captures + push to Edge Impulse.
rem
rem One-time setup (new terminal window after, so the var sticks):
rem     setx EI_API_KEY ei_your_key_here
rem
rem Then just run this file from anywhere:
rem     tools\upload_edgeimpulse.bat
rem
rem Why per-label loop instead of "*/*.jpg": edge-impulse-uploader.js only
rem special-cases a single trailing "*.ext" wildcard (Windows shells don't
rem expand globs for it the way bash does), so a two-level glob like
rem export_ei\*\*.jpg silently reaches Node as a literal path and 404s
rem with ENOENT. Looping one label folder at a time keeps every glob single-
rem level, which the uploader does expand itself.

setlocal enabledelayedexpansion

if "%EI_API_KEY%"=="" (
    echo EI_API_KEY is not set.
    echo Run this once, then open a new terminal:
    echo     setx EI_API_KEY ei_your_key_here
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

for /d %%D in (datasets\export_ei\*) do (
    echo.
    echo === %%~nxD ===
    edge-impulse-uploader --api-key %EI_API_KEY% --category split --label %%~nxD "datasets\export_ei\%%~nxD\*.jpg"
    if errorlevel 1 (
        echo Upload failed for %%~nxD
        exit /b 1
    )
)

echo.
echo Done.
endlocal
