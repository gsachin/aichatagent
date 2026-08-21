<#
.SYNOPSIS
    University Admissions Voice Assistant - One-shot launcher.
.DESCRIPTION
    Kills stale services, releases ports, starts everything fresh,
    and updates the Cloudflare tunnel URL everywhere it is needed.
    Includes GPU health check, Ollama model pre-warming, and
    optional named Cloudflare tunnel for a permanent URL.
.PARAMETER WithStreamlit
    Also launch Streamlit dashboard (port 8502) and main app (port 8501).
.PARAMETER SkipTwilio
    Skip updating the Twilio webhook.
.PARAMETER NamedTunnel
    Use a named Cloudflare tunnel (permanent URL) instead of ephemeral quick tunnel.
    Requires cloudflared to be authenticated (cloudflared tunnel login).
.PARAMETER TunnelName
    Name for the Cloudflare tunnel (default: "admissions-tunnel").
    Only used when -NamedTunnel is specified.
.EXAMPLE
    .\start_services.ps1
    .\start_services.ps1 -WithStreamlit
    .\start_services.ps1 -SkipTwilio
    .\start_services.ps1 -NamedTunnel
    .\start_services.ps1 -NamedTunnel -TunnelName "my-prod-tunnel"
#>

param(
    [switch]$WithStreamlit = $false,
    [switch]$SkipTwilio = $false,
    [switch]$NamedTunnel = $false,
    [string]$TunnelName = "admissions-tunnel"
)

# ---- Config ---------------------------------------------------------------
$ProjectRoot = $PSScriptRoot
$TunnelFile  = Join-Path $ProjectRoot ".whatsapp_tunnel"
$FastAPIPort = 8000
$StreamlitMainPort = 8501
$StreamlitDashboardPort = 8502

# Prefer the project venv (created by bootstrap_services) when present,
# falling back to system python — keeps pre-venv setups working unchanged.
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PythonExe  = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
$StreamlitExe = if (Test-Path $VenvPython) { Join-Path $ProjectRoot ".venv\Scripts\streamlit.exe" } else { "streamlit" }
Write-Host "  Python: $PythonExe"

$ESC  = [char]27
$GREEN = "$ESC[32m"; $YELLOW = "$ESC[33m"; $RED = "$ESC[31m"
$CYAN = "$ESC[36m"; $RESET = "$ESC[0m"; $BOLD = "$ESC[1m"

function Write-Step   { Write-Host ("{0}{1}{2}--- {3} ---{4}" -f "`n", $CYAN, $BOLD, ($args -join ' '), $RESET) }
function Write-OK     { Write-Host ("{0}  OK: {1}{2}" -f $GREEN, ($args -join ' '), $RESET) }
function Write-Warn   { Write-Host ("{0}  WARN: {1}{2}" -f $YELLOW, ($args -join ' '), $RESET) }
function Write-Err    { Write-Host ("{0}  ERROR: {1}{2}" -f $RED, ($args -join ' '), $RESET) }

# ---- Tool discovery (user-scope winget installs are invisible to old shells) ----
function Find-DockerCli {
    $cmd = Get-Command "docker" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
        (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\resources\bin\docker.exe"),
        "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    )) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Find-DockerDesktopExe {
    foreach ($p in @(
        (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe"),
        "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    )) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Find-CloudflaredExe {
    $cmd = Get-Command "cloudflared" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $p = Join-Path $env:LOCALAPPDATA "Programs\cloudflared\cloudflared.exe"
    if (Test-Path $p) { return $p }
    return $null
}

function Ensure-ProjectDeps {
    # Self-heal: if the venv cannot import the app's dependencies (fresh
    # clone, interrupted bootstrap, empty venv), install them via the
    # bootstrap's install phase instead of failing 30 waits later.
    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    $probePy = if (Test-Path $VenvPython) { $VenvPython } else { "python" }
    & $probePy -c "import fastapi, torch" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-OK "venv dependencies OK (fastapi + torch importable)"
        return $true
    }

    Write-Warn "venv dependencies missing or broken -- running installer (bootstrap_services.py --install-only)..."
    Write-Warn "First run downloads several GB (torch cu128 + pip deps + Ollama models) -- please wait."
    $boot = Join-Path $ProjectRoot "bootstrap_services.py"
    if (-not (Test-Path $boot)) {
        Write-Err "bootstrap_services.py not found -- cannot install dependencies"
        return $false
    }
    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        & py -3.11 $boot --install-only
    } else {
        & python $boot --install-only
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Err ("installer failed (exit code {0})" -f $LASTEXITCODE)
        return $false
    }

    & $VenvPython -c "import fastapi, torch" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "dependencies still not importable after install -- check the output above"
        return $false
    }
    Write-OK "Dependencies installed and importable"
    return $true
}

# ==== Step 0: dependency self-heal =========================================
# start_services.ps1 is the entry point most people run. If the venv cannot
# import the app's dependencies (fresh clone, interrupted bootstrap, empty
# venv), install them here instead of timing out at Step 5.
if (-not (Ensure-ProjectDeps)) {
    Write-Err "Cannot start services without working dependencies -- fix the errors above and re-run."
    exit 1
}
# The installer may have just created/fixed the venv -- prefer it from here on.
if (Test-Path $VenvPython) {
    $PythonExe = $VenvPython
    $StreamlitExe = Join-Path $ProjectRoot ".venv\Scripts\streamlit.exe"
}

# ==== Step 1: Kill stale processes ========================================
Write-Step "Step 1: Killing stale processes"

# Aggressive cleanup -- belt and suspenders
$cloudflaredProcs = Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue
if ($cloudflaredProcs) {
    $cloudflaredProcs | Stop-Process -Force
    Write-OK ("Killed {0} cloudflared process(es) via Stop-Process" -f $cloudflaredProcs.Count)
}
# Also use taskkill to catch any stragglers
cmd /c "taskkill /F /IM cloudflared.exe 2>NUL" | Out-Null
Start-Sleep -Seconds 1
$remaining = Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue
if ($remaining) {
    Write-Warn ("{0} cloudflared process(es) still alive -- forcing..." -f $remaining.Count)
    $remaining | Stop-Process -Force
    Start-Sleep -Seconds 2
}
$finalCheck = Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue
if (-not $finalCheck) {
    Write-OK "All cloudflared processes terminated"
} else {
    Write-Err "Cannot kill cloudflared -- reboot may be needed"
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

# ==== Step 3: GPU / CUDA health check ======================================
Write-Step "Step 3: GPU / CUDA health check"

$gpuOk = $false
$nvidiaSmi = Get-Command "nvidia-smi" -ErrorAction SilentlyContinue
if ($nvidiaSmi) {
    $gpuInfo = cmd /c "nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader 2>&1"
    if ($LASTEXITCODE -eq 0) {
        Write-OK ("GPU detected: {0}" -f ($gpuInfo -replace "`n", " | "))
        $gpuOk = $true
    } else {
        Write-Warn "nvidia-smi found but query failed -- GPU may be unavailable"
    }
} else {
    Write-Warn "nvidia-smi not found -- GPU/CUDA will NOT be available"
    Write-Warn "STT and TTS will fall back to CPU (slow: 15-35s per utterance)"
}

# Verify CUDA is visible to Python / PyTorch
if ($gpuOk) {
    $cudaCheck = cmd /c "$PythonExe -c `"import torch; print(f'CUDA={torch.cuda.is_available()}, Device={torch.cuda.get_device_name(0) if torch.cuda.is_available() else \`"N/A\`"}')`" 2>&1"
    if ($LASTEXITCODE -eq 0) {
        Write-OK ("PyTorch: {0}" -f $cudaCheck)
    } else {
        Write-Warn "PyTorch CUDA check failed -- GPU may not be usable from Python"
        Write-Warn "Fix: .venv\Scripts\pip install torch==2.7.1+cu128 --extra-index-url https://download.pytorch.org/whl/cu128"
    }
}

# ==== Step 4: Docker / PostgreSQL check ===================================
Write-Step "Step 4: Docker / PostgreSQL check"

$dbReady = $false

# Locate the Docker CLI (user-scope winget installs are not on old shells' PATH)
$DockerCli = Find-DockerCli
if ($DockerCli) {
    $env:PATH = "$(Split-Path $DockerCli);$env:PATH"
    Write-OK ("Docker CLI: {0}" -f $DockerCli)
} else {
    Write-Warn "Docker CLI not found (PATH + default install locations)"
}

# Check Docker is running
$dockerRunning = $false
$dockerCheck = if ($DockerCli) { & $DockerCli info 2>$null } else { $null }
if ($DockerCli -and $LASTEXITCODE -eq 0) {
    Write-OK "Docker Desktop is running"
    $dockerRunning = $true
} else {
    Write-Warn "Docker Desktop is NOT running -- attempting to start..."
    $ddExe = Find-DockerDesktopExe
    if ($ddExe) {
        Start-Process -FilePath $ddExe -WindowStyle Hidden -ErrorAction SilentlyContinue
        Write-OK ("Docker Desktop launching ({0}) - this may take 30-60s..." -f $ddExe)
    } else {
        Write-Warn "Docker Desktop.exe not found in known locations -- start it manually"
    }

    # Wait for Docker to become responsive
    if ($DockerCli) {
        $waitAttempt = 0
        while ($waitAttempt -lt 45) {
            Start-Sleep -Seconds 2
            $waitAttempt++
            & $DockerCli info 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-OK ("Docker Desktop ready (after ~{0}s)" -f ($waitAttempt * 2))
                $dockerRunning = $true
                break
            }
        }
    }
}

# Check PostgreSQL is reachable
if ($dockerRunning) {
    $pgCheck = & $DockerCli exec elearning-postgres pg_isready -U elearning -d admissions 2>$null
    if ($LASTEXITCODE -eq 0 -and $pgCheck -match "accepting") {
        Write-OK "PostgreSQL: accepting connections on localhost:5432"
        $dbReady = $true
    } else {
        Write-Warn "PostgreSQL container not running -- attempting docker compose up..."
        & $DockerCli compose -f (Join-Path $ProjectRoot "docker-compose.yml") up -d postgres 2>$null
        if ($LASTEXITCODE -eq 0) {
            Start-Sleep -Seconds 5
            $pgCheck2 = & $DockerCli exec elearning-postgres pg_isready -U elearning -d admissions 2>$null
            if ($LASTEXITCODE -eq 0 -and $pgCheck2 -match "accepting") {
                Write-OK "PostgreSQL started and accepting connections"
                $dbReady = $true
            }
        } else {
            # Try existing e-learning container
            Write-Warn "docker compose failed -- checking for existing e-learning container..."
            $elearningCheck = & $DockerCli ps --filter "name=elearning-postgres" --format "{{.Status}}" 2>$null
            if ($elearningCheck -match "healthy") {
                Write-OK "Found existing elearning-postgres container (healthy)"
                $dbReady = $true
            }
        }
    }
}

if (-not $dbReady) {
    Write-Warn "PostgreSQL is NOT available -- server will start in database-less mode"
    Write-Warn "Leads, calls, and follow-ups will NOT be persisted"
    Write-Warn "Fix: ensure Docker Desktop is running with elearning-postgres container"
} else {
    Write-OK "Database pre-flight: PASSED"
}

# ==== Step 5: Start FastAPI backend ========================================
Write-Step "Step 5: Starting FastAPI backend (port $FastAPIPort)"

$ServerLog = Join-Path $env:TEMP "university_fastapi.log"
$fastApiArgs = @{
    FilePath               = $PythonExe
    ArgumentList           = "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$FastAPIPort"
    WindowStyle            = "Hidden"
    PassThru               = $true
    RedirectStandardOutput = $ServerLog
}
$FastAPIProcess = Start-Process @fastApiArgs

Write-OK ("FastAPI starting (PID {0}) - log: {1}" -f $FastAPIProcess.Id, $ServerLog)

# Wait for server to be ready (use curl -- Invoke-WebRequest unreliable in PS 5.1)
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

# ==== Step 6: Ollama model pre-warming ====================================
Write-Step "Step 6: Ollama model pre-warming"

$ollamaUp = $false
$ollamaCheck = curl.exe -s -o NUL -w "%{http_code}" "http://127.0.0.1:11434/api/tags" 2>$null
if ($ollamaCheck -eq "200") {
    Write-OK "Ollama is running"
    $ollamaUp = $true
} else {
    Write-Warn "Ollama not reachable on port 11434 -- skip pre-warming"
}

if ($ollamaUp) {
    $modelList = curl.exe -s "http://127.0.0.1:11434/api/tags" 2>$null | & $PythonExe -c "import sys,json; models=[m['name'] for m in json.load(sys.stdin).get('models',[])]; print('\n'.join(models))" 2>$null
    if ($modelList) {
        Write-OK ("Found models: {0}" -f ($modelList -split "`n" -join ", "))
        foreach ($model in ($modelList -split "`n" | Where-Object { $_ })) {
            Write-OK ("Pre-warming: {0} ..." -f $model)
            $null = curl.exe -s -X POST "http://127.0.0.1:11434/api/generate" -H "Content-Type: application/json" -d "{`"model`":`"$model`",`"prompt`":`"ping`",`"keep_alive`":`"24h`",`"max_tokens`":1}" 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-OK ("  {0} loaded into GPU (keep_alive=24h)" -f $model)
            } else {
                Write-Warn ("  Pre-warm failed for {0}" -f $model)
            }
        }
    } else {
        Write-Warn "No models found in Ollama -- run: ollama pull qwen2.5:7b"
    }
}

# ==== Step 7: Start Cloudflare tunnel ======================================
Write-Step "Step 7: Starting Cloudflare tunnel"

$cloudflaredPath = Find-CloudflaredExe
if (-not $cloudflaredPath) {
    Write-Err "cloudflared not found! Install with: winget install Cloudflare.cloudflared"
    Write-Err "or download from: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/"
    exit 1
}
Write-OK ("cloudflared: {0}" -f $cloudflaredPath)

$TunnelLog = Join-Path $env:TEMP "university_cloudflared.log"

if ($NamedTunnel) {
    # ---- Named tunnel (permanent URL) ------------------------------------
    Write-OK ("Named tunnel mode: {0}" -f $TunnelName)

    $tunnelList = & $cloudflaredPath tunnel list 2>&1
    $tunnelExists = $tunnelList -match $TunnelName
    if (-not $tunnelExists) {
        Write-OK ("Creating named tunnel: {0} ..." -f $TunnelName)
        $createResult = & $cloudflaredPath tunnel create $TunnelName 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Err ("Failed to create tunnel '{0}': {1}" -f $TunnelName, ($createResult -join " "))
            Write-Warn "Falling back to ephemeral quick tunnel..."
            $NamedTunnel = $false
        } else {
            Write-OK ("Tunnel '{0}' created. Credentials stored in ~/.cloudflared/" -f $TunnelName)
            Write-Warn ("IMPORTANT: Route DNS for '{0}' in Cloudflare Zero Trust dashboard" -f $TunnelName)
        }
    }

    if ($NamedTunnel) {
        $tunnelCmd = "`"$cloudflaredPath`" tunnel run --url http://localhost:{0} {1} 2>&1" -f $FastAPIPort, $TunnelName
        $cfArgs = @{
            FilePath               = "cmd"
            ArgumentList           = "/c", $tunnelCmd
            WindowStyle            = "Hidden"
            PassThru               = $true
            RedirectStandardOutput = $TunnelLog
        }
        $CloudflaredProcess = Start-Process @cfArgs
        Write-OK ("Named tunnel '{0}' starting (PID {1}) - log: {2}" -f $TunnelName, $CloudflaredProcess.Id, $TunnelLog)
    }
}

if (-not $NamedTunnel) {
    # ---- Ephemeral quick tunnel (URL changes every restart) --------------
    Write-Warn "Ephemeral tunnel mode (URL will change on next restart)"
    $tunnelCmd = "`"$cloudflaredPath`" tunnel --url http://localhost:{0} 2>&1" -f $FastAPIPort
    $cfArgs = @{
        FilePath               = "cmd"
        ArgumentList           = "/c", $tunnelCmd
        WindowStyle            = "Hidden"
        PassThru               = $true
        RedirectStandardOutput = $TunnelLog
    }
    $CloudflaredProcess = Start-Process @cfArgs
    Write-OK ("Ephemeral tunnel starting (PID {0}) - log: {1}" -f $CloudflaredProcess.Id, $TunnelLog)
}

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

# ==== Step 8: Write tunnel hostname everywhere =============================
Write-Step "Step 8: Writing tunnel hostname everywhere"

[System.IO.File]::WriteAllText($TunnelFile, $TunnelHost)
Write-OK (".whatsapp_tunnel file updated -> {0}" -f $TunnelHost)

$env:TUNNEL_HOST = $TunnelHost
Write-OK ("Env TUNNEL_HOST = {0}" -f $TunnelHost)

$env:DASHBOARD_API_URL = ("https://{0}" -f $TunnelHost)
Write-OK ("Env DASHBOARD_API_URL = https://{0}" -f $TunnelHost)

# ==== Step 9: Update Twilio webhooks =======================================
Write-Step "Step 9: Updating Twilio webhooks"

if ($SkipTwilio) {
    Write-Warn "Skipping Twilio (SkipTwilio flag set)"
} else {
    $TwilioScript = Join-Path $ProjectRoot "scripts\update_twilio_webhook.py"
    if (Test-Path $TwilioScript) {
        $result = cmd /c "$PythonExe $TwilioScript $TunnelHost 2>&1"
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

# ==== Step 10: Verify URLs ================================================
Write-Step "Step 10: Verifying URLs"

# Verify tunnel is reachable (with warmup delay for Cloudflare)
$tunnelOk = $false
for ($tunnelAttempt = 0; $tunnelAttempt -lt 5; $tunnelAttempt++) {
    if ($tunnelAttempt -gt 0) { Start-Sleep -Seconds 3 }
    $verifyResult = curl.exe -s -o NUL -w "%{http_code}" "https://$TunnelHost/" 2>$null
    if ($verifyResult -eq "200") {
        Write-OK ("Tunnel reachable: https://{0}/ (HTTP 200)" -f $TunnelHost)
        $tunnelOk = $true
        break
    }
    Write-Warn ("Tunnel not ready yet (HTTP {0}) -- retry {1}/5..." -f $verifyResult, ($tunnelAttempt + 1))
}
if (-not $tunnelOk) {
    Write-Err ("Tunnel NOT reachable after 5 attempts: https://{0}/" -f $TunnelHost)
}

# Verify Twilio webhook matches
if (-not $SkipTwilio) {
    $twilioVerify = cmd /c "$PythonExe -c `"from dotenv import load_dotenv; load_dotenv(); import os; from twilio.rest import Client; c=Client(os.environ['TWILIO_ACCOUNT_SID'],os.environ['TWILIO_AUTH_TOKEN']); [print(f'Twilio webhook: {n.voice_url}') for n in c.incoming_phone_numbers.list(phone_number='+19788198953')]`" 2>&1"
    if ($twilioVerify -match $TunnelHost) {
        Write-OK ("Twilio webhook verified: matches {0}" -f $TunnelHost)
    } else {
        Write-Warn ("Twilio webhook may be stale! Current: {0}" -f ($twilioVerify -replace ".*Twilio webhook: ", ""))
        Write-Warn ("Expected: https://{0}/twilio/voice" -f $TunnelHost)
    }
}

# ==== Step 11: Optional Streamlit apps =====================================
if ($WithStreamlit) {
    Write-Step "Step 11: Starting Streamlit apps"

    # Dashboard (port 8502)
    $DashLog = Join-Path $env:TEMP "university_dashboard.log"
    $dashArgs = @{
        FilePath               = $StreamlitExe
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
        FilePath               = $StreamlitExe
        ArgumentList           = "run", "app.py", "--server.port", "$StreamlitMainPort", "--server.headless", "true"
        WindowStyle            = "Hidden"
        PassThru               = $true
        RedirectStandardOutput = $AppLog
        # stderr not redirected (separate from stdout)
    }
    $AppProcess = Start-Process @appArgs
    Write-OK ("Main app starting (PID {0}) -> http://localhost:{1}" -f $AppProcess.Id, $StreamlitMainPort)
}

# ==== Step 12: Summary =====================================================
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
Write-Host ("{0}{1}IMPORTANT:{2}{0} " -f $YELLOW, $BOLD, $RESET) -NoNewline
if ($NamedTunnel) {
    Write-Host ("Named tunnel '{0}' -- URL persists across restarts." -f $TunnelName)
} else {
    Write-Host ("This tunnel is ephemeral. If you close this" -f $YELLOW)
    Write-Host ("{0}terminal, the tunnel URL will CHANGE. Re-run this script to get" -f $YELLOW)
    Write-Host ("{0}a fresh tunnel and update all configs.{1}" -f $YELLOW, $RESET)
    Write-Host ("{0}Tip: Use -NamedTunnel for a permanent URL.{1}" -f $CYAN, $RESET)
}
Write-Host ""
Write-Host ("{0}Press Ctrl+C to stop all services...{1}" -f $CYAN, $RESET)

# Check background processes
$FastAPIProcess, $CloudflaredProcess | ForEach-Object {
    if ($_.HasExited) {
        Write-Err ("{0} (PID {1}) has already exited!" -f $_.ProcessName, $_.Id)
    }
}
