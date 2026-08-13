<#
tools/upload_edgeimpulse.ps1 -- export captures + push to Edge Impulse.

One-time setup (needs a fresh terminal / VS Code restart to take effect
elsewhere -- setx only writes the registry, it doesn't touch the current
process's environment):
    setx EI_API_KEY ei_your_key_here

Or just to unblock the current terminal right now:
    $env:EI_API_KEY = "ei_your_key_here"

Then, from anywhere:
    tools\upload_edgeimpulse.ps1

Replaces an earlier .bat version. cmd.exe's delayed-expansion variables
and its raw wildcard handling (edge-impulse-uploader v1.39.2 calls
fs.statSync() on the literal argument before its own "Windows doesn't
expand globs" fallback ever runs, so a bare "*.jpg" throws ENOENT) both
turned out unreliable across a multi-label run. PowerShell arrays sidestep
both: file lists are real arrays splatted as arguments, never reassembled
strings, and Get-ChildItem does the globbing instead of the child process.
#>

$ErrorActionPreference = 'Stop'

if (-not $env:EI_API_KEY) {
    Write-Host "EI_API_KEY is not set in this session."
    Write-Host "One-time (needs a fresh terminal to take effect elsewhere):"
    Write-Host "    setx EI_API_KEY ei_your_key_here"
    Write-Host "Or just for this terminal:"
    Write-Host '    $env:EI_API_KEY = "ei_your_key_here"'
    exit 1
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "Exporting captures to datasets\export_ei ..."
python tools\export_edgeimpulse.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$exportDir = Join-Path $repoRoot 'datasets\export_ei'
if (-not (Test-Path $exportDir)) {
    Write-Host "$exportDir not found - nothing to upload."
    exit 1
}

# Doc 19.2 targets 150+ images/class; chunking keeps each uploader
# invocation's argument list well clear of any process-launch limits.
$chunkSize = 100

Get-ChildItem $exportDir -Directory | ForEach-Object {
    $label = $_.Name
    Write-Host ""
    Write-Host "=== $label ==="
    $files = Get-ChildItem $_.FullName -Filter '*.jpg' | Select-Object -ExpandProperty FullName
    if (-not $files) {
        Write-Host "  no .jpg files, skipping"
        return
    }
    for ($i = 0; $i -lt $files.Count; $i += $chunkSize) {
        $end = [Math]::Min($i + $chunkSize, $files.Count) - 1
        $chunk = $files[$i..$end]
        & edge-impulse-uploader --api-key $env:EI_API_KEY --category split --label $label @chunk
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Upload failed for $label"
            exit 1
        }
    }
}

Write-Host ""
Write-Host "Done."
