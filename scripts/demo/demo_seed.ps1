<#
.SYNOPSIS
    Seed synthetic demo data into admissions_demo.
.DESCRIPTION
    Refuses any database whose name does not end in _demo. The Python seed
    script performs the same check independently. Requires -Confirm.
#>
[CmdletBinding()]
param(
    [switch]$Confirm
)

$ErrorActionPreference = 'Stop'
$DemoRoot = $PSScriptRoot
Set-Location $DemoRoot

function Read-DotEnv([string]$Path) {
    $map = [ordered]@{}
    if (-not (Test-Path -LiteralPath $Path)) { return $map }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $parts = $line -split '=', 2
        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($value.Length -ge 2) {
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        $map[$key] = $value
    }
    return $map
}

function Set-ProcessEnvFromDotEnv($Map) {
    # The Python seed loads .env without override, so a stale DATABASE_URL in
    # the parent shell would win. Export the demo values first.
    foreach ($key in $Map.Keys) {
        Set-Item -Path "env:$key" -Value ([string]$Map[$key])
    }
}

$envPath = Join-Path $DemoRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    throw ".env is missing. Run .\demo_setup.ps1 first."
}

$envMap = Read-DotEnv $envPath
Set-ProcessEnvFromDotEnv $envMap
$dsn = [string]$envMap['DATABASE_URL']
if (-not $dsn) { throw "DATABASE_URL is missing in .env" }
if ($dsn -notmatch '^postgresql://[^/]+/([^?]+)') {
    throw "Cannot parse DATABASE_URL. Expected postgresql://user:pass@host:port/database"
}
$dbName = $Matches[1]
if ($dbName -notmatch '_demo$') {
    throw "Refusing to seed '$dbName': the database name must end in _demo."
}

if (-not $Confirm) {
    Write-Host ""
    Write-Host "This will DELETE existing rows and insert synthetic demo data in:" -ForegroundColor Yellow
    Write-Host "  database: $dbName"
    Write-Host ""
    Write-Host "Run .\demo_seed.ps1 -Confirm to proceed." -ForegroundColor Yellow
    exit 0
}

$venvPy = Join-Path $DemoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPy)) {
    throw "Demo venv not found: $venvPy. Run .\demo_setup.ps1 first."
}

& $venvPy (Join-Path $DemoRoot 'scripts\seed_demo_data.py') --yes
if ($LASTEXITCODE -ne 0) {
    throw "Demo seed failed with exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Demo data seeded into $dbName." -ForegroundColor Green
