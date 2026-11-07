<#
.SYNOPSIS
    One-time setup for the UniversityBotDemoAPI frozen demo.
.DESCRIPTION
    Creates the demo virtual environment, installs the pinned requirements,
    creates data/log directories, creates the demo .env from the template,
    and optionally creates admissions_demo + demo_user and initializes the
    schema.

    The demo uses the same application code, model, prompts, voices, and
    language settings as the approved development version. Only the database,
    secrets, and demo runtime state differ.
.PARAMETER InitDb
    Create the admissions_demo database and demo_user role (requires Docker),
    generate a password, update .env, and run init_db().
.PARAMETER SkipVenv
    Do not create or install into .venv. Useful for a dry inspection.
.PARAMETER RebuildAssets
    Run scripts/build_call_assets.py --ensure after setup.
#>
[CmdletBinding()]
param(
    [switch]$InitDb,
    [switch]$SkipVenv,
    [switch]$RebuildAssets,
    [int]$DbHostPort = 0
)

$ErrorActionPreference = 'Stop'
$DemoRoot = $PSScriptRoot
Set-Location $DemoRoot

function Write-Step([string]$Text) { Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "  [OK] $Text" -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host "  [WARN] $Text" -ForegroundColor Yellow }
function Write-Fail([string]$Text) { Write-Host "  [FAIL] $Text" -ForegroundColor Red }

function Write-TextNoBom([string]$Path, [string[]]$Lines) {
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($Path, $Lines, $utf8)
}

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

function Set-DotEnvValue([string]$Path, [string]$Key, [string]$Value) {
    $lines = @(Get-Content -LiteralPath $Path)
    $out = @()
    $found = $false
    foreach ($line in $lines) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=") {
            $out += "$Key=$Value"
            $found = $true
        } else {
            $out += $line
        }
    }
    if (-not $found) { $out += "$Key=$Value" }
    Write-TextNoBom $Path $out
}

function Invoke-Checked([string]$FilePath, [string[]]$Arguments) {
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

function Set-ProcessEnvFromDotEnv($Map) {
    # Environment variables win over .env in app/config.py. Export the demo
    # values so a stale DATABASE_URL or CRM_ENABLED in the parent shell cannot
    # silently redirect the demo to the development database.
    foreach ($key in $Map.Keys) {
        Set-Item -Path "env:$key" -Value ([string]$Map[$key])
    }
}

Write-Host "UniversityBotDemoAPI setup" -ForegroundColor Cyan
Write-Host "Root: $DemoRoot"

# ---------------------------------------------------------------------------
# Python + venv
# ---------------------------------------------------------------------------
Write-Step "Python and virtual environment"

$PythonExe = 'python'
$PythonArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.11 --version > $null 2>&1
    if ($LASTEXITCODE -eq 0) {
        $PythonExe = 'py'
        $PythonArgs = @('-3.11')
    }
}
$versionOutput = & $PythonExe @PythonArgs --version 2>&1
Write-Ok "Using $versionOutput"
if ($versionOutput -notmatch '3\.11\.') {
    Write-Warn "Python 3.11 is the tested version. Continuing with $versionOutput."
}

$VenvDir = Join-Path $DemoRoot '.venv'
$VenvPy = Join-Path $VenvDir 'Scripts\python.exe'

if (-not $SkipVenv) {
    if (-not (Test-Path -LiteralPath $VenvPy)) {
        Write-Ok "Creating .venv (one time, several GB of packages)..."
        Invoke-Checked $PythonExe (@($PythonArgs) + @('-m', 'venv', $VenvDir))
    } else {
        Write-Ok ".venv already exists"
    }

    Invoke-Checked $VenvPy @('-m', 'pip', 'install', '--upgrade', 'pip', 'wheel')

    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        Write-Ok "NVIDIA GPU detected; installing torch cu128 first"
        Invoke-Checked $VenvPy @('-m', 'pip', 'install', 'torch==2.7.1+cu128', '--index-url', 'https://download.pytorch.org/whl/cu128')
    } else {
        Write-Warn "No NVIDIA GPU detected; requirements.txt will install the PyPI torch build"
    }

    Write-Ok "Installing requirements.txt (this can take several minutes)"
    Invoke-Checked $VenvPy @('-m', 'pip', 'install', '-r', (Join-Path $DemoRoot 'requirements.txt'))
    Invoke-Checked $VenvPy @('-m', 'pip', 'check')
} else {
    Write-Warn "Skipping venv creation and package installation"
    if (-not (Test-Path -LiteralPath $VenvPy)) {
        throw "SkipVenv was used but $VenvPy does not exist."
    }
}

# ---------------------------------------------------------------------------
# Runtime directories and .env
# ---------------------------------------------------------------------------
Write-Step "Runtime directories and environment"

foreach ($dir in @('data\documents', 'data\offers', 'logs')) {
    New-Item -ItemType Directory -Force (Join-Path $DemoRoot $dir) | Out-Null
}
Write-Ok "data/documents, data/offers, logs"

$envPath = Join-Path $DemoRoot '.env'
$examplePath = Join-Path $DemoRoot 'env.demo.example'
if (-not (Test-Path -LiteralPath $envPath)) {
    if (-not (Test-Path -LiteralPath $examplePath)) {
        throw "env.demo.example is missing."
    }
    Copy-Item -LiteralPath $examplePath -Destination $envPath
    # Rewrite without a BOM so python-dotenv reads the first key cleanly.
    Write-TextNoBom $envPath @(Get-Content -LiteralPath $envPath)
    Write-Ok "Created .env from env.demo.example"
} else {
    Write-Ok ".env already exists (not overwritten)"
}

$envMap = Read-DotEnv $envPath
if (-not $envMap['DATABASE_URL']) {
    throw "DATABASE_URL is missing in .env"
}

# ---------------------------------------------------------------------------
# Demo database (optional, requires Docker)
# ---------------------------------------------------------------------------
if ($InitDb) {
    Write-Step "Demo database (admissions_demo + demo_user)"

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "Docker is required for -InitDb. Start the elearning-postgres container or create the database manually."
    }

    $container = 'elearning-postgres'
    $running = @(& docker ps --filter "name=$container" --format '{{.Names}}' 2>$null)
    if ($running -notcontains $container) {
        throw "Container '$container' is not running. Start it with: docker start $container"
    }

    $resolvedDbPort = $DbHostPort
    if ($resolvedDbPort -le 0) {
        $portLines = @(& docker port $container 5432/tcp 2>$null)
        foreach ($line in $portLines) {
            if ($line -match ':(\d+)\s*$') {
                $resolvedDbPort = [int]$matches[1]
                break
            }
        }
    }
    if ($resolvedDbPort -le 0) {
        throw "Could not detect the published PostgreSQL port for '$container'. Pass -DbHostPort explicitly."
    }
    Write-Ok "PostgreSQL host port: $resolvedDbPort"

    $password = [guid]::NewGuid().ToString('N').Substring(0, 24)
    $sqlRole = @'
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='demo_user') THEN
    CREATE ROLE demo_user LOGIN PASSWORD '{{PASSWORD}}';
  ELSE
    ALTER ROLE demo_user WITH PASSWORD '{{PASSWORD}}';
  END IF;
END $$;
'@
    $sqlRole = $sqlRole.Replace('{{PASSWORD}}', $password)

    Invoke-Checked 'docker' @('exec', $container, 'psql', '-U', 'elearning', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-c', $sqlRole)

    $exists = & docker exec $container psql -U elearning -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='admissions_demo'"
    if ($LASTEXITCODE -ne 0) { throw "Could not query PostgreSQL for admissions_demo." }
    $existsText = if ($null -eq $exists) { "" } else { ([string]$exists).Trim() }
    if ($existsText -ne '1') {
        Invoke-Checked 'docker' @('exec', $container, 'psql', '-U', 'elearning', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-c', 'CREATE DATABASE admissions_demo OWNER demo_user')
        Write-Ok "Created database admissions_demo"
    } else {
        Write-Ok "Database admissions_demo already exists"
    }

    Invoke-Checked 'docker' @('exec', $container, 'psql', '-U', 'elearning', '-d', 'admissions_demo', '-v', 'ON_ERROR_STOP=1', '-c', 'GRANT ALL ON SCHEMA public TO demo_user')

    $dsn = "postgresql://demo_user:$password@127.0.0.1:${resolvedDbPort}/admissions_demo"
    Set-DotEnvValue $envPath 'DATABASE_URL' $dsn
    Write-Ok "DATABASE_URL updated for demo_user / admissions_demo on port $resolvedDbPort"
    $envMap = Read-DotEnv $envPath
    Set-ProcessEnvFromDotEnv $envMap

    Write-Step "Initializing schema"
    # Single-line code with single-quoted strings: Windows PowerShell 5.1
    # strips inner double quotes when passing a native command argument.
    $initCode = "from dotenv import load_dotenv; load_dotenv('.env'); import asyncio; from app.database import init_db; raise SystemExit(0 if asyncio.run(init_db()) else 1)"
    Invoke-Checked $VenvPy @('-c', $initCode)
    Write-Ok "Schema initialized"
} else {
    Write-Warn "Database setup skipped. Run .\demo_setup.ps1 -InitDb when ready."
    if (([string]$envMap['DATABASE_URL']) -match '__DEMO_DB_PASSWORD__') {
        Write-Warn "DATABASE_URL still contains the password placeholder."
    }
}

# ---------------------------------------------------------------------------
# External caches and tools
# ---------------------------------------------------------------------------
Write-Step "External caches and tools"

$hfHub = Join-Path $env:USERPROFILE '.cache\huggingface\hub'
$hfCaches = @(
    'models--Systran--faster-whisper-small',
    'models--OpenVoiceOS--ai4bharat-indicconformer-hi-onnx',
    'models--OpenVoiceOS--ai4bharat-indicconformer-gu-onnx'
)
foreach ($cache in $hfCaches) {
    if (Test-Path -LiteralPath (Join-Path $hfHub $cache)) {
        Write-Ok "HF cache: $cache"
    } else {
        Write-Warn "Missing HF cache: $cache (the first warmup will download it)"
    }
}

$kokoroCache = Join-Path $env:USERPROFILE '.cache\pipecat\kokoro-onnx'
if (Test-Path -LiteralPath $kokoroCache) {
    Write-Ok "Kokoro cache present"
} else {
    Write-Warn "Kokoro cache missing; TTS_PROVIDER=sarvam makes this non-fatal"
}

if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Ok "ffmpeg is on PATH"
} else {
    Write-Warn "ffmpeg is not on PATH; audio conversion may fail"
}

if (Get-Command cloudflared -ErrorAction SilentlyContinue) {
    Write-Ok "cloudflared is on PATH (needed for -Live)"
} else {
    Write-Warn "cloudflared is not on PATH; -Live will not be able to start a tunnel"
}

# ---------------------------------------------------------------------------
# Import smoke test and optional assets
# ---------------------------------------------------------------------------
Write-Step "Import smoke test"
$envMap = Read-DotEnv $envPath
Set-ProcessEnvFromDotEnv $envMap
Invoke-Checked $VenvPy @('-c', "import app.main; print('import app.main OK')")

if ($RebuildAssets) {
    Write-Step "Rebuilding pre-rendered call assets"
    Invoke-Checked $VenvPy @('scripts\build_call_assets.py', '--ensure')
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Step "Next steps"

$envMap = Read-DotEnv $envPath
$required = @(
    @('LLM_API_KEY', 'LLM API key'),
    @('SARVAM_API_KEY', 'Sarvam STT/TTS key'),
    @('TWILIO_ACCOUNT_SID', 'Twilio Account SID'),
    @('TWILIO_AUTH_TOKEN', 'Twilio Auth Token'),
    @('TWILIO_PHONE_NUMBER', 'Twilio voice number'),
    @('TWILIO_WHATSAPP_NUMBER', 'Twilio WhatsApp number')
)
foreach ($pair in $required) {
    if (-not $envMap[$pair[0]]) {
        Write-Warn "$($pair[1]) is empty in .env"
    }
}

Write-Host ""
Write-Host "1. Fill the blank secrets in: $envPath" -ForegroundColor Yellow
Write-Host "2. Seed demo data:            .\demo_seed.ps1 -Confirm" -ForegroundColor Yellow
Write-Host "3. Local demo:                .\demo_start.ps1" -ForegroundColor Yellow
Write-Host "4. Full client demo:          .\demo_start.ps1 -Live -StartCrm" -ForegroundColor Yellow
Write-Host "5. Stop and restore webhooks: .\demo_stop.ps1" -ForegroundColor Yellow
Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
