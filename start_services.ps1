<#
.SYNOPSIS
    University Admissions Voice Assistant - One-shot launcher.
.DESCRIPTION
    Kills stale services, releases ports, starts everything fresh,
    and updates the Cloudflare tunnel URL everywhere it is needed.
.PARAMETER WithStreamlit
    Also launch Streamlit dashboard (port 8502) and main app (port 8501).
.PARAMETER SkipTwilio
    Skip updating the Twilio webhook.
.EXAMPLE
    .\start_services.ps1
    .\start_services.ps1 -WithStreamlit
    .\start_services.ps1 -SkipTwilio
#>

param(
    [switch]$WithStreamlit = $false,
    [switch]$SkipTwilio = $false
)

# ---- Config ---------------------------------------------------------------
$ProjectRoot = $PSScriptRoot
$TunnelFile  = Join-Path $ProjectRoot ".whatsapp_tunnel"
$FastAPIPort = 8000
$StreamlitMainPort = 8501
$StreamlitDashboardPort = 8502

$ESC  = [char]27
$GREEN = "$ESC[32m"; $YELLOW = "$ESC[33m"; $RED = "$ESC[31m"
$CYAN = "$ESC[36m"; $RESET = "$ESC[0m"; $BOLD = "$ESC[1m"

function Write-Step   { Write-Host ("{0}{1}{2}--- {3} ---{4}" -f "`n", $CYAN, $BOLD, ($args -join ' '), $RESET) }
function Write-OK     { Write-Host ("{0}  OK: {1}{2}" -f $GREEN, ($args -join ' '), $RESET) }
function Write-Warn   { Write-Host ("{0}  WARN: {1}{2}" -f $YELLOW, ($args -join ' '), $RESET) }
function Write-Err    { Write-Host ("{0}  ERROR: {1}{2}" -f $RED, ($args -join ' '), $RESET) }

# ==== Step 1: Kill stale processes ========================================
Write-Step "Step 1: Killing stale processes"

$cloudflaredProcs = Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue
if ($cloudflaredProcs) {
    $cloudflaredProcs | Stop-Process -Force
    Write-OK ("Killed {0} cloudflared process(es)" -f $cloudflaredProcs.Count)
} else {
    Write-OK "No cloudflared processes running"
}

foreach ($port in @($FastAPIPort, $StreamlitMainPort, $StreamlitDashboardPort)) {
    $connections = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    $pids = $connections.OwningProcess | Select-Object -Unique | Where-Object { $_ -gt 0 }
    foreach ($procId in $pids) {
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($proc) {
            $proc | Stop-Process -Force
            Write-OK ("Killed {0} (PID {1}) on port {2}" -f $proc.ProcessName, $procId, $port)
        }
    }
}
Write-OK "Process cleanup complete"

# Clear stale TUNNEL_HOST from previous runs so the server reads the file
$env:TUNNEL_HOST = ""
Write-OK "Cleared stale TUNNEL_HOST env var (server will read .whatsapp_tunnel file)"

# ==== Step 2: Verify ports are free ========================================
Write-Step "Step 2: Verifying ports are free"

foreach ($port in @($FastAPIPort, $StreamlitMainPort, $StreamlitDashboardPort)) {
    $attempt = 0
    $portBusy = $true
    while ($portBusy -and $attempt -lt 10) {
        $conn = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
        if (-not $conn) {
            $portBusy = $false
        } else {
            $attempt++
            Write-Warn ("Port {0} still in use - waiting ({1}/10)..." -f $port, $attempt)
            Start-Sleep -Seconds 1
        }
    }

    if ($portBusy) {
        Write-Warn ("Port {0} is STILL busy after 10s - may be TIME_WAIT" -f $port)
        if ($conn) {
            $conn.OwningProcess | Select-Object -Unique | Where-Object { $_ -gt 0 } | ForEach-Object {
                Get-Process -Id $_ -ErrorAction SilentlyContinue | Stop-Process -Force
            }
        }
        Start-Sleep -Seconds 2
    } else {
        Write-OK ("Port {0} is free" -f $port)
    }
}

# ==== Step 3: Start FastAPI backend ========================================
Write-Step "Step 3: Starting FastAPI backend (port $FastAPIPort)"

$ServerLog = Join-Path $env:TEMP "university_fastapi.log"
$fastApiArgs = @{
    FilePath               = "python"
    ArgumentList           = "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$FastAPIPort"
    WindowStyle            = "Hidden"
    PassThru               = $true
    RedirectStandardOutput = $ServerLog
}
$FastAPIProcess = Start-Process @fastApiArgs

Write-OK ("FastAPI starting (PID {0}) - log: {1}" -f $FastAPIProcess.Id, $ServerLog)

# Wait for server to be ready (use curl — Invoke-WebRequest unreliable in PS 5.1)
$attempt = 0
$serverReady = $false
$healthUrl = "http://127.0.0.1:{0}/" -f $FastAPIPort
while (-not $serverReady -and $attempt -lt 30) {
    Start-Sleep -Seconds 2
    $attempt++
    $curlResult = curl.exe -s -o NUL -w "%{http_code}" $healthUrl 2>$null
    if ($curlResult -eq "200") {
        Write-OK ("FastAPI server responding (took ~{0}s)" -f ($attempt * 2))
        $serverReady = $true
    } else {
        Write-Warn ("Waiting for FastAPI server... ({0}/30)" -f $attempt)
    }
}

if (-not $serverReady) {
    Write-Err ("FastAPI server did NOT come up - check {0}" -f $ServerLog)
    exit 1
}

# ==== Step 4: Start Cloudflare tunnel ======================================
Write-Step "Step 4: Starting Cloudflare tunnel"

$cloudflaredPath = Get-Command "cloudflared" -ErrorAction SilentlyContinue
if (-not $cloudflaredPath) {
    Write-Err "cloudflared not found! Install from: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/"
    exit 1
}

$TunnelLog = Join-Path $env:TEMP "university_cloudflared.log"
# Use cmd /c to merge stderr into stdout (cloudflared logs to stderr)
$tunnelCmd = "cloudflared tunnel --url http://localhost:{0} 2>&1" -f $FastAPIPort
$cfArgs = @{
    FilePath               = "cmd"
    ArgumentList           = "/c", $tunnelCmd
    WindowStyle            = "Hidden"
    PassThru               = $true
    RedirectStandardOutput = $TunnelLog
}
$CloudflaredProcess = Start-Process @cfArgs

Write-OK ("Cloudflare tunnel starting (PID {0}) - log: {1}" -f $CloudflaredProcess.Id, $TunnelLog)

# Parse tunnel URL from log output
$TunnelHost = $null
$attempt = 0
$tunnelFound = $false
while (-not $tunnelFound -and $attempt -lt 15) {
    Start-Sleep -Seconds 3
    $attempt++
    if (Test-Path $TunnelLog) {
        $logContent = Get-Content $TunnelLog -Raw -ErrorAction SilentlyContinue
        if ($logContent) {
            $regex = [regex]'https://([a-zA-Z0-9\-]+\.trycloudflare\.com)'
            $match = $regex.Match($logContent)
            if ($match.Success) {
                $TunnelHost = $match.Groups[1].Value
                $tunnelFound = $true
            }
        }
    }
    if (-not $tunnelFound) {
        Write-Warn ("Waiting for tunnel URL... ({0}/15)" -f $attempt)
    }
}

if (-not $TunnelHost) {
    Write-Err ("Failed to capture tunnel URL - check {0}" -f $TunnelLog)
    exit 1
}

Write-OK ("Tunnel URL captured: {0}" -f $TunnelHost)

# ==== Step 5: Write tunnel hostname everywhere =============================
Write-Step "Step 5: Writing tunnel hostname everywhere"

[System.IO.File]::WriteAllText($TunnelFile, $TunnelHost)
Write-OK (".whatsapp_tunnel file updated -> {0}" -f $TunnelHost)

$env:TUNNEL_HOST = $TunnelHost
Write-OK ("Env TUNNEL_HOST = {0}" -f $TunnelHost)

$env:DASHBOARD_API_URL = ("https://{0}" -f $TunnelHost)
Write-OK ("Env DASHBOARD_API_URL = https://{0}" -f $TunnelHost)

# ==== Step 6: Update Twilio webhooks =======================================
Write-Step "Step 6: Updating Twilio webhooks"

if ($SkipTwilio) {
    Write-Warn "Skipping Twilio (SkipTwilio flag set)"
} else {
    $TwilioScript = Join-Path $ProjectRoot "scripts\update_twilio_webhook.py"
    if (Test-Path $TwilioScript) {
        $result = & python $TwilioScript $TunnelHost 2>&1
        if ($LASTEXITCODE -eq 0) {
            $result | ForEach-Object { Write-OK $_ }
        } else {
            Write-Warn "Twilio update had issues (check .env for credentials)"
            $result | ForEach-Object { Write-Warn $_ }
        }
    } else {
        Write-Warn ("Twilio update script not found: {0}" -f $TwilioScript)
        Write-Warn ("Manual fix: set Voice URL to https://{0}/twilio/voice (GET)" -f $TunnelHost)
    }
}

# ==== Step 7: Optional Streamlit apps ======================================
if ($WithStreamlit) {
    Write-Step "Step 7: Starting Streamlit apps"

    # Dashboard (port 8502)
    $DashLog = Join-Path $env:TEMP "university_dashboard.log"
    $dashArgs = @{
        FilePath               = "streamlit"
        ArgumentList           = "run", "dashboard.py", "--server.port", "$StreamlitDashboardPort", "--server.headless", "true"
        WindowStyle            = "Hidden"
        PassThru               = $true
        RedirectStandardOutput = $DashLog
        # stderr not redirected (separate from stdout)
    }
    $DashProcess = Start-Process @dashArgs
    Write-OK ("Dashboard starting (PID {0}) -> http://localhost:{1}" -f $DashProcess.Id, $StreamlitDashboardPort)

    # Main app (port 8501)
    $AppLog = Join-Path $env:TEMP "university_streamlit.log"
    $appArgs = @{
        FilePath               = "streamlit"
        ArgumentList           = "run", "app.py", "--server.port", "$StreamlitMainPort", "--server.headless", "true"
        WindowStyle            = "Hidden"
        PassThru               = $true
        RedirectStandardOutput = $AppLog
        # stderr not redirected (separate from stdout)
    }
    $AppProcess = Start-Process @appArgs
    Write-OK ("Main app starting (PID {0}) -> http://localhost:{1}" -f $AppProcess.Id, $StreamlitMainPort)
}

# ==== Step 8: Summary ======================================================
Write-Host ""
Write-Host ("{0}{1}ALL SERVICES STARTED SUCCESSFULLY{2}" -f $GREEN, $BOLD, $RESET)
Write-Host ""
Write-Host ("{0}Public Tunnel:{1}" -f $BOLD, $RESET)
Write-Host ("{0}   https://{1}{2}" -f $CYAN, $TunnelHost, $RESET)
Write-Host ""
Write-Host ("{0}Inbound Calls:{1}" -f $BOLD, $RESET)
Write-Host ("   Dial {0}+19788198953{1} for AI admissions assistant" -f $YELLOW, $RESET)
Write-Host ("   Webhook: {0}https://{1}/twilio/voice{2}" -f $CYAN, $TunnelHost, $RESET)
Write-Host ""
Write-Host ("{0}WhatsApp:{1}" -f $BOLD, $RESET)
Write-Host ("   Webhook: {0}https://{1}/twilio/whatsapp{2}" -f $CYAN, $TunnelHost, $RESET)
Write-Host ("   (Configure in the Twilio Console -> WhatsApp Sandbox)")
Write-Host ""
Write-Host ("{0}Local Services:{1}" -f $BOLD, $RESET)
Write-Host ("   FastAPI backend:  {0}http://localhost:{1}{2}" -f $CYAN, $FastAPIPort, $RESET)

if ($WithStreamlit) {
    Write-Host ("   Dashboard:         {0}http://localhost:{1}{2}" -f $CYAN, $StreamlitDashboardPort, $RESET)
    Write-Host ("   Main Streamlit:    {0}http://localhost:{1}{2}" -f $CYAN, $StreamlitMainPort, $RESET)
}

Write-Host ""
Write-Host ("{0}Quick API Test:{1}" -f $BOLD, $RESET)
Write-Host ("   curl https://{0}/" -f $TunnelHost)
Write-Host ""
Write-Host ("{0}Logs:{1}" -f $BOLD, $RESET)
Write-Host ("   Server:  {0}" -f $ServerLog)
Write-Host ("   Tunnel:  {0}" -f $TunnelLog)

if ($WithStreamlit) {
    Write-Host ("   Dash:    {0}" -f $DashLog)
    Write-Host ("   App:     {0}" -f $AppLog)
}

Write-Host ""
Write-Host ("{0}{1}IMPORTANT:{2}{0} This tunnel is ephemeral. If you close this" -f $YELLOW, $BOLD, $RESET)
Write-Host ("{0}terminal, the tunnel URL will CHANGE. Re-run this script to get" -f $YELLOW)
Write-Host ("{0}a fresh tunnel and update all configs.{1}" -f $YELLOW, $RESET)
Write-Host ""
Write-Host ("{0}Press Ctrl+C to stop all services...{1}" -f $CYAN, $RESET)

# Check background processes
$FastAPIProcess, $CloudflaredProcess | ForEach-Object {
    if ($_.HasExited) {
        Write-Err ("{0} (PID {1}) has already exited!" -f $_.ProcessName, $_.Id)
    }
}
