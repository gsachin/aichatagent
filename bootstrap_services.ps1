# Windows entry point for bootstrap_services — ensures Python 3.11 exists,
# then delegates every phase to bootstrap_services.py (one source of truth).
# Double-click bootstrap_services.bat, or run:
#   powershell -ExecutionPolicy Bypass -File .\bootstrap_services.ps1 [flags]

$ProjectRoot = $PSScriptRoot

function Find-Python {
    # Prefer the launcher for the exact version, then PATH python.
    $py = Get-Command "py" -ErrorAction SilentlyContinue
    if ($py) {
        $ver = & py -3.11 -c "import sys; print(sys.executable)" 2>$null
        if ($ver) { return $ver.Trim() }
    }
    $p = Get-Command "python" -ErrorAction SilentlyContinue
    if ($p) { return $p.Source }
    return $null
}

$PythonExe = Find-Python

if (-not $PythonExe) {
    Write-Host "Python 3.11 not found -- installing via winget..." -ForegroundColor Yellow
    winget install --id Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "winget install failed. Install Python 3.11 manually, then re-run." -ForegroundColor Red
        exit 1
    }
    $PythonExe = Find-Python
    if (-not $PythonExe) {
        Write-Host "Python installed but PATH not refreshed -- close and reopen this window, then re-run bootstrap_services.bat" -ForegroundColor Red
        exit 1
    }
}

& $PythonExe (Join-Path $ProjectRoot "bootstrap_services.py") @args
exit $LASTEXITCODE
