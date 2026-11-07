<#
.SYNOPSIS
    Start the UniversityBotDemoAPI frozen demo.
.DESCRIPTION
    Default: local UI only (FastAPI, Streamlit chat, optional dashboard).
    -Live:   also start the Cloudflare tunnel, back up and repoint the Twilio
             voice webhook, check/start the CRM API, and print the WhatsApp
             Sandbox webhook to set manually.

    Refuses to start unless .env targets admissions_demo. Never kills a
    process that owns a port; it reports the owner instead.
.PARAMETER Live
    Full client demo: tunnel + Twilio voice repoint + CRM check.
.PARAMETER StartCrm
    Start the Salesforce Admission API at 127.0.0.1:8098 if it is not running.
.PARAMETER TunnelUi
    Also start Cloudflare tunnels for the Streamlit chat UI (8501) and the
    admin dashboard (8502), matching development's start_services.ps1
    -WithStreamlit. The 8502 tunnel is skipped when -NoDashboard is used.
.PARAMETER NoDashboard
    Do not start the admin dashboard on 8502.
.EXAMPLE
    .\demo_start.ps1
    Local UI only: FastAPI, Streamlit chat, and the dashboard.
.EXAMPLE
    .\demo_start.ps1 -StartCrm
    Local UI plus the CRM API on port 8098.
.EXAMPLE
    .\demo_start.ps1 -Live -StartCrm
    Full client demo: FastAPI tunnel, Twilio voice repoint, WhatsApp Sandbox
    URL, and the CRM API.
.EXAMPLE
    .\demo_start.ps1 -Live -StartCrm -TunnelUi
    Same as the previous example, plus public Cloudflare tunnels for the
    Streamlit chat (8501) and dashboard (8502). This matches development's
    .\start_services.ps1 -WithStreamlit.
.EXAMPLE
    .\demo_start.ps1 -TunnelUi
    Local UI with public chat and dashboard tunnels, without changing Twilio.
.EXAMPLE
    .\demo_start.ps1 -Live -StartCrm -NoDashboard
    Full client demo without the admin dashboard or its tunnel.
#>
[CmdletBinding()]
param(
    [switch]$Live,
    [switch]$StartCrm,
    [switch]$TunnelUi,
    [switch]$NoDashboard
)

$ErrorActionPreference = 'Stop'
$DemoRoot = $PSScriptRoot
Set-Location $DemoRoot

function Write-Step([string]$Text) { Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "  [OK] $Text" -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host "  [WARN] $Text" -ForegroundColor Yellow }
function Write-Fail([string]$Text) { Write-Host "  [FAIL] $Text" -ForegroundColor Red }

function Write-TextNoBom([string]$Path, [string]$Text) {
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $utf8)
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

function Assert-PortFree([int]$Port) {
    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($connections) {
        $owners = @()
        foreach ($connection in $connections) {
            $proc = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
            $owners += "$($connection.OwningProcess):$($proc.ProcessName)"
        }
        $owners = $owners | Sort-Object -Unique
        throw "Port $Port is already in use ($($owners -join ', ')). Stop the development stack first."
    }
}

function Wait-Http([string]$Url, [int]$Seconds) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) { return $true }
        } catch {
            # not ready yet
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Test-CrmHealth {
    try {
        $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8098/health' -TimeoutSec 3
        return ($response.status -eq 'ok')
    } catch {
        return $false
    }
}

function Get-TunnelHost([string]$LogPath, [int]$Seconds) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Path -LiteralPath $LogPath) {
            $match = Select-String -Path $LogPath -Pattern 'https://([a-z0-9-]+\.trycloudflare\.com)' -AllMatches | Select-Object -First 1
            if ($match) {
                return $match.Matches[0].Groups[1].Value
            }
        }
    }
    return $null
}

function Set-ProcessEnvFromDotEnv($Map) {
    # Environment variables win over .env in app/config.py. Export the demo
    # values so a stale DATABASE_URL or CRM_ENABLED in the parent shell cannot
    # silently redirect the demo to the development database.
    foreach ($key in $Map.Keys) {
        Set-Item -Path "env:$key" -Value ([string]$Map[$key])
    }
}

function Stop-ProcessTree([int]$ProcessId) {
    $children = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ParentProcessId -eq $ProcessId }
    foreach ($child in $children) {
        Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

$envPath = Join-Path $DemoRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    throw ".env is missing. Run .\demo_setup.ps1 first."
}
$envMap = Read-DotEnv $envPath

$dsn = [string]$envMap['DATABASE_URL']
if (-not $dsn) { throw "DATABASE_URL is missing in .env" }
if ($dsn -notmatch '^postgresql://[^/]+/([^?]+)') {
    throw "Cannot parse DATABASE_URL. Expected postgresql://user:pass@host:port/database"
}
$dbName = $Matches[1]
if ($dbName -ne 'admissions_demo') {
    throw "Refusing to start: DATABASE_URL targets '$dbName', not 'admissions_demo'."
}

$crmRaw = $envMap['CRM_ENABLED']
$crmEnabled = if ($null -eq $crmRaw) { "" } else { ([string]$crmRaw).Trim().ToLowerInvariant() }
if ($crmEnabled -ne 'true') {
    $message = "CRM_ENABLED in .env is '$crmEnabled'. The approved demo requires the literal string 'true'."
    if ($Live) { throw $message } else { Write-Warn $message }
}

if (-not $envMap['LLM_API_KEY']) { Write-Warn "LLM_API_KEY is empty; answers will use the fixed-response path." }
if (-not $envMap['SARVAM_API_KEY']) { Write-Warn "SARVAM_API_KEY is empty; voice STT/TTS will not work." }

$VenvPy = Join-Path $DemoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPy)) {
    throw "Demo venv not found: $VenvPy. Run .\demo_setup.ps1 first."
}

$logDir = Join-Path $DemoRoot 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null

Write-Step "Pre-flight"
foreach ($port in @(8000, 8501, 8502)) {
    Assert-PortFree $port
}
Write-Ok "Ports 8000/8501/8502 are free"

$crmStartedByDemo = $false
$crmPid = $null
if (-not (Test-CrmHealth)) {
    if ($StartCrm) {
        $crmRoot = 'D:\project\salesforce\salesforce-admission-api'
        $crmPy = Join-Path $crmRoot 'venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $crmPy)) {
            throw "CRM venv not found: $crmPy"
        }
        $crmProc = Start-Process -FilePath $crmPy -ArgumentList @('-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8098') -WorkingDirectory $crmRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'crm.out.log') -RedirectStandardError (Join-Path $logDir 'crm.err.log')
        $crmPid = $crmProc.Id
        $crmStartedByDemo = $true
        $healthy = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Seconds 1
            if (Test-CrmHealth) { $healthy = $true; break }
        }
        if (-not $healthy) {
            throw "CRM API did not become healthy. See logs\crm.err.log."
        }
        Write-Ok "CRM API started from $crmRoot (PID $crmPid)"
    } elseif ($Live) {
        throw "CRM API is not running at :8098. Start it from D:\project\salesforce\salesforce-admission-api or pass -StartCrm."
    } else {
        Write-Warn "CRM API is not running; CRM writes will queue in the outbox."
    }
} else {
    Write-Ok "CRM API healthy at :8098"
}

$started = [ordered]@{}
$tunnelPid = $null
$uiTunnelPid = $null
$dashboardTunnelPid = $null

try {
    # Export the demo .env into this process before starting the app children.
    # The CRM API was already checked/started above and keeps its own .env.
    Set-ProcessEnvFromDotEnv $envMap

    Write-Step "Starting the demo stack"

    $fastapi = Start-Process -FilePath $VenvPy -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $DemoRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'fastapi.out.log') -RedirectStandardError (Join-Path $logDir 'fastapi.err.log')
    $started['fastapi'] = $fastapi.Id

    $streamlit = Start-Process -FilePath $VenvPy -ArgumentList @('-m', 'streamlit', 'run', 'app.py', '--server.port', '8501', '--server.headless', 'true') -WorkingDirectory $DemoRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'streamlit.out.log') -RedirectStandardError (Join-Path $logDir 'streamlit.err.log')
    $started['streamlit'] = $streamlit.Id

    if (-not $NoDashboard) {
        $dashboard = Start-Process -FilePath $VenvPy -ArgumentList @('-m', 'streamlit', 'run', 'dashboard.py', '--server.port', '8502', '--server.headless', 'true') -WorkingDirectory $DemoRoot -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'dashboard.out.log') -RedirectStandardError (Join-Path $logDir 'dashboard.err.log')
        $started['dashboard'] = $dashboard.Id
    }

    if (-not (Wait-Http 'http://127.0.0.1:8000/' 180)) {
        throw "FastAPI did not become ready. See logs\fastapi.err.log."
    }
    Write-Ok "FastAPI ready at http://127.0.0.1:8000"

    if (-not (Wait-Http 'http://127.0.0.1:8501/_stcore/health' 120)) {
        throw "Streamlit did not become ready. See logs\streamlit.err.log."
    }
    Write-Ok "Streamlit ready at http://127.0.0.1:8501"

    if ($Live) {
        $cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
        if (-not $cloudflared) {
            throw "cloudflared is not on PATH; -Live needs a public tunnel for Twilio and WhatsApp."
        }

        $tunnelLog = Join-Path $logDir 'tunnel.err.log'
        if (Test-Path -LiteralPath $tunnelLog) { Remove-Item -LiteralPath $tunnelLog -Force }
        $tunnel = Start-Process -FilePath 'cloudflared' -ArgumentList @('tunnel', '--url', 'http://127.0.0.1:8000') -PassThru -WindowStyle Hidden -RedirectStandardError $tunnelLog
        $tunnelPid = $tunnel.Id
        $tunnelHost = Get-TunnelHost $tunnelLog 40
        if (-not $tunnelHost) {
            throw "Could not read the cloudflared URL. See logs\tunnel.err.log."
        }
        Write-TextNoBom (Join-Path $DemoRoot '.whatsapp_tunnel') ($tunnelHost + "`n")
        Write-Ok "Tunnel: https://$tunnelHost"

        try {
            & (Join-Path $DemoRoot 'demo_twilio.ps1') -Enable -TunnelUrl $tunnelHost
            Write-Ok "Twilio voice webhook repointed to the demo tunnel"
        } catch {
            Write-Warn "Twilio repoint failed: $($_.Exception.Message)"
            Write-Warn "The demo is still running. Run .\demo_twilio.ps1 -Enable -TunnelUrl $tunnelHost manually."
        }

        # The WhatsApp Sandbox webhook is manual, so a restart leaves it on the
        # previous tunnel host until someone fixes it — the first inbound
        # message then dies with Twilio 11210 and looks like "hi did nothing".
        # Check it before the demo rather than after.
        $waCheck = Join-Path $DemoRoot 'check_whatsapp_webhook.py'
        if (Test-Path -LiteralPath $waCheck) {
            Write-Step "WhatsApp Sandbox webhook check"
            try {
                & $VenvPy $waCheck --minutes 60
            } catch {
                Write-Warn "WhatsApp webhook check could not run: $($_.Exception.Message)"
            }
            if ($LASTEXITCODE -eq 2) {
                Write-Warn "Repoint the WhatsApp Sandbox webhook to the URL above before demoing."
            }
        }

        Write-Host ""
        Write-Host "WHATSAPP SANDBOX - set 'When a message comes in' to:" -ForegroundColor Yellow
        Write-Host "  https://$tunnelHost/twilio/whatsapp  (HTTP POST)" -ForegroundColor Yellow
        Write-Host "After the demo, demo_stop.ps1 prints the development URL to restore." -ForegroundColor Yellow
    }

    if ($TunnelUi) {
        $uiLog = Join-Path $logDir 'tunnel-ui.err.log'
        if (Test-Path -LiteralPath $uiLog) { Remove-Item -LiteralPath $uiLog -Force }
        $uiTunnel = Start-Process -FilePath 'cloudflared' -ArgumentList @('tunnel', '--url', 'http://127.0.0.1:8501') -PassThru -WindowStyle Hidden -RedirectStandardError $uiLog
        $uiTunnelPid = $uiTunnel.Id
        $uiHost = Get-TunnelHost $uiLog 40
        if ($uiHost) {
            Write-Ok "Streamlit chat tunnel: https://$uiHost"
        } else {
            Write-Warn "Could not read the Streamlit tunnel URL. See logs\tunnel-ui.err.log."
        }

        if (-not $NoDashboard) {
            $dashboardLog = Join-Path $logDir 'tunnel-dashboard.err.log'
            if (Test-Path -LiteralPath $dashboardLog) { Remove-Item -LiteralPath $dashboardLog -Force }
            $dashboardTunnel = Start-Process -FilePath 'cloudflared' -ArgumentList @('tunnel', '--url', 'http://127.0.0.1:8502') -PassThru -WindowStyle Hidden -RedirectStandardError $dashboardLog
            $dashboardTunnelPid = $dashboardTunnel.Id
            $dashboardHost = Get-TunnelHost $dashboardLog 40
            if ($dashboardHost) {
                Write-Ok "Dashboard tunnel: https://$dashboardHost"
            } else {
                Write-Warn "Could not read the dashboard tunnel URL. See logs\tunnel-dashboard.err.log."
            }
        }
    }

    $state = [ordered]@{
        started_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        demo_root = $DemoRoot
        processes = $started
        crm_started_by_demo = $crmStartedByDemo
        crm_pid = $crmPid
        tunnel_pid = $tunnelPid
        ui_tunnel_pid = $uiTunnelPid
        dashboard_tunnel_pid = $dashboardTunnelPid
    }
    $state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $DemoRoot '.demo_pids.json') -Encoding UTF8

    Write-Step "Demo is running"
    Write-Host "  Chat UI:      http://127.0.0.1:8501" -ForegroundColor Green
    if (-not $NoDashboard) { Write-Host "  Dashboard:    http://127.0.0.1:8502" -ForegroundColor Green }
    Write-Host "  FastAPI:      http://127.0.0.1:8000" -ForegroundColor Green
    Write-Host "  CRM API:      http://127.0.0.1:8098" -ForegroundColor Green
    Write-Host ""
    Write-Host "Stop with: .\demo_stop.ps1" -ForegroundColor Yellow

    Start-Process 'http://127.0.0.1:8501'
} catch {
    Write-Fail $_.Exception.Message
    foreach ($procId in $started.Values) {
        Stop-ProcessTree -ProcessId ([int]$procId)
    }
    if ($crmStartedByDemo -and $crmPid) {
        Stop-ProcessTree -ProcessId ([int]$crmPid)
    }
    if ($tunnelPid) { Stop-ProcessTree -ProcessId ([int]$tunnelPid) }
    if ($uiTunnelPid) { Stop-ProcessTree -ProcessId ([int]$uiTunnelPid) }
    if ($dashboardTunnelPid) { Stop-ProcessTree -ProcessId ([int]$dashboardTunnelPid) }
    $pidFile = Join-Path $DemoRoot '.demo_pids.json'
    if (Test-Path -LiteralPath $pidFile) { Remove-Item -LiteralPath $pidFile -Force }
    throw
}
