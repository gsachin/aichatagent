<#
.SYNOPSIS
    Health check + tunnel manager for the University Admissions Assistant.
.DESCRIPTION
    - GPU / CUDA health check (nvidia-smi + PyTorch)
    - Docker / PostgreSQL readiness check
    - Checks FastAPI (8000), Streamlit main (8501), Streamlit dashboard (8502)
    - Checks / starts a Cloudflare tunnel for EACH port
    - Prints a shareable status card with all local + public URLs
    - Does NOT kill anything -- safe to run while the app is in use
.EXAMPLE
    .\check_and_tunnel.ps1
#>

param(
    [switch]$SkipTunnel = $false
)

# ---- Config ---------------------------------------------------------------
$ProjectRoot = $PSScriptRoot
$FastAPIPort = 8000
$StreamlitMainPort = 8501
$StreamlitDashboardPort = 8502

$ESC  = [char]27
$G = "$ESC[92m"; $Y = "$ESC[93m"; $R = "$ESC[91m"; $C = "$ESC[96m"
$B = "$ESC[1m"; $N = "$ESC[0m"

function Write-Step   { Write-Host ("${C}${B}--- $($args -join ' ') ---${N}") }
function Write-OK     { Write-Host ("${G}  [OK]   $($args -join ' ')${N}") }
function Write-Warn   { Write-Host ("${Y}  [WARN] $($args -join ' ')${N}") }
function Write-Err    { Write-Host ("${R}  [DOWN] $($args -join ' ')${N}") }

# Tunnel definitions: port, label, cache file, metrics port
$Tunnels = @(
    @{ Port = $FastAPIPort;           Label = "FastAPI";    CacheFile = Join-Path $ProjectRoot ".tunnel_8000"; MetricsPort = 20241 },
    @{ Port = $StreamlitMainPort;     Label = "Streamlit";  CacheFile = Join-Path $ProjectRoot ".tunnel_8501"; MetricsPort = 20242 },
    @{ Port = $StreamlitDashboardPort; Label = "Dashboard"; CacheFile = Join-Path $ProjectRoot ".tunnel_8502"; MetricsPort = 20243 }
)

# ---- Helper ----------------------------------------------------------------
function Test-Endpoint($url) {
    try {
        $code = curl.exe -s -o NUL -w "%{http_code}" $url 2>$null
        return ($code -eq "200")
    } catch { return $false }
}

function Get-TunnelHost($tunnelDef) {
    $cacheFile = $tunnelDef.CacheFile
    $port      = $tunnelDef.Port

    # 1) Check cache file (primary source, verified reachable)
    if (Test-Path $cacheFile) {
        $cached = (Get-Content $cacheFile -Raw).Trim()
        if ($cached -and (Test-Endpoint "https://$cached/")) {
            return $cached
        }
    }

    # 2) Scan ALL cloudflared process log/output for a URL pointing to this port.
    #    cloudflared on Windows writes to stderr, so we check every temp log we know.
    $knownLogs = @(
        (Join-Path $env:TEMP "university_cloudflared.log"),          # start_services.ps1
        (Join-Path $env:TEMP "cloudflared_${port}_stdout.log"),      # our own stdout
        (Join-Path $env:TEMP "cloudflared_${port}_stderr.log")       # our own stderr (Windows default)
    )

    foreach ($logPath in $knownLogs) {
        if (Test-Path $logPath) {
            $logContent = Get-Content $logPath -Raw -ErrorAction SilentlyContinue
            if ($logContent -match 'https://([a-zA-Z0-9\-]+\.trycloudflare\.com)') {
                foreach ($m in [regex]::Matches($logContent, 'https://([a-zA-Z0-9\-]+\.trycloudflare\.com)')) {
                    $hostname = $m.Groups[1].Value
                    if (Test-Endpoint "https://$hostname/") {
                        [System.IO.File]::WriteAllText($cacheFile, $hostname)
                        return $hostname
                    }
                }
            }
        }
    }

    return $null
}

function Start-SingleTunnel($tunnelDef) {
    $port      = $tunnelDef.Port
    $label     = $tunnelDef.Label
    $cacheFile = $tunnelDef.CacheFile
    $metrics   = $tunnelDef.MetricsPort

    $stdoutLog = Join-Path $env:TEMP "cloudflared_${port}_stdout.log"
    $stderrLog = Join-Path $env:TEMP "cloudflared_${port}_stderr.log"

    # Clear old logs
    foreach ($f in @($stdoutLog, $stderrLog)) {
        if (Test-Path $f) { Remove-Item $f -Force }
    }

    Start-Process -FilePath "cloudflared" `
        -ArgumentList "tunnel", "--url", "http://localhost:$port", "--metrics", "localhost:$metrics" `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden

    # Wait for URL to appear in EITHER log (cloudflared logs to stderr on Windows)
    $tunnelHost = $null
    $attempt = 0
    while (-not $tunnelHost -and $attempt -lt 20) {
        Start-Sleep -Seconds 2
        $attempt++
        foreach ($logPath in @($stdoutLog, $stderrLog)) {
            if (Test-Path $logPath) {
                $logContent = Get-Content $logPath -Raw -ErrorAction SilentlyContinue
                if ($logContent -match 'https://([a-zA-Z0-9\-]+\.trycloudflare\.com)') {
                    $tunnelHost = $matches[1]
                    break
                }
            }
        }
    }

    if ($tunnelHost) {
        [System.IO.File]::WriteAllText($cacheFile, $tunnelHost)
        Write-OK "$label tunnel started: $tunnelHost"
    } else {
        Write-Err "$label tunnel did not start within timeout"
    }
    return $tunnelHost
}

# ============================================================================
Write-Host "${B}${C}University Admissions -- Service Status${N}`n"

# ==== Step 1: GPU / CUDA health check ======================================
Write-Step "GPU / CUDA Health Check"

$gpuOk = $false
$nvidiaSmi = Get-Command "nvidia-smi" -ErrorAction SilentlyContinue
if ($nvidiaSmi) {
    $gpuInfo = cmd /c "nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader 2>&1"
    if ($LASTEXITCODE -eq 0) {
        Write-OK "GPU: $($gpuInfo -replace '\r?\n', ' | ')"
        $gpuOk = $true
    } else {
        Write-Warn "nvidia-smi found but query failed -- GPU may be unavailable"
    }
} else {
    Write-Warn "nvidia-smi not found -- GPU/CUDA NOT available"
}

if ($gpuOk) {
    $cudaCheck = cmd /c "python -c `"import torch; print(f'CUDA={torch.cuda.is_available()}, Device={torch.cuda.get_device_name(0) if torch.cuda.is_available() else \`"N/A\`"}')`" 2>&1"
    if ($LASTEXITCODE -eq 0) {
        Write-OK "PyTorch: $cudaCheck"
    } else {
        Write-Warn "PyTorch CUDA check failed"
    }
}

# ==== Step 2: Docker / PostgreSQL check ====================================
Write-Step "Docker / PostgreSQL Check"

$dbReady = $false
$dockerCheck = docker info 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-OK "Docker Desktop is running"

    $pgCheck = docker exec elearning-postgres pg_isready -U elearning -d admissions 2>$null
    if ($LASTEXITCODE -eq 0 -and $pgCheck -match "accepting") {
        Write-OK "PostgreSQL: accepting connections on localhost:5432"
        $dbReady = $true
    } else {
        Write-Warn "PostgreSQL container not responding -- checking alternative..."
        $elearningCheck = docker ps --filter "name=elearning-postgres" --format "{{.Status}}" 2>$null
        if ($elearningCheck -match "healthy") {
            Write-OK "Found existing elearning-postgres container (healthy)"
            $dbReady = $true
        } else {
            Write-Warn "PostgreSQL is NOT available -- database features will fail"
        }
    }
} else {
    Write-Warn "Docker Desktop is NOT running -- database features will fail"
}

if ($dbReady) {
    Write-OK "Database pre-flight: PASSED"
}

# ==== Step 3: Check local services =========================================
Write-Step "Local Services"

$fastApiUp   = Test-Endpoint "http://localhost:$FastAPIPort/"
$streamlitUp = Test-Endpoint "http://localhost:$StreamlitMainPort/"
$dashUp      = Test-Endpoint "http://localhost:$StreamlitDashboardPort/"

if ($fastApiUp)   { Write-OK "FastAPI backend      http://localhost:$FastAPIPort" }
else              { Write-Err "FastAPI backend      http://localhost:$FastAPIPort" }

if ($streamlitUp) { Write-OK "Streamlit Main       http://localhost:$StreamlitMainPort" }
else              { Write-Err "Streamlit Main       http://localhost:$StreamlitMainPort" }

if ($dashUp)      { Write-OK "Streamlit Dashboard  http://localhost:$StreamlitDashboardPort" }
else              { Write-Err "Streamlit Dashboard  http://localhost:$StreamlitDashboardPort" }

# ==== Step 4: Check / start Cloudflare tunnels =============================
Write-Step "Cloudflare Tunnels"

$TunnelHosts = @{}  # port -> hostname

foreach ($t in $Tunnels) {
    $port  = $t.Port
    $label = $t.Label
    $hostname = Get-TunnelHost $t

    if ($hostname) {
        Write-OK "$label tunnel alive: $hostname"
        $TunnelHosts[$port] = $hostname
    } elseif (-not $SkipTunnel) {
        Write-Warn "$label tunnel missing -- starting..."
        $hostname = Start-SingleTunnel $t
        if ($hostname) {
            $TunnelHosts[$port] = $hostname
        }
    } else {
        Write-Err "$label tunnel DOWN (SkipTunnel set)"
    }
}

# ==== Step 5: Status table =================================================
Write-Host ""
Write-Host "${B}+---------------------+-----------------------------------------------+-----------+${N}"
Write-Host "${B}| Service             | URL                                           | Status    |${N}"
Write-Host "${B}+---------------------+-----------------------------------------------+-----------+${N}"

$fStatus = if ($fastApiUp)   { "${G}UP${N}  " } else { "${R}DOWN${N}" }
$sStatus = if ($streamlitUp) { "${G}UP${N}  " } else { "${R}DOWN${N}" }
$dStatus = if ($dashUp)      { "${G}UP${N}  " } else { "${R}DOWN${N}" }

Write-Host ("  FastAPI Backend       http://localhost:$FastAPIPort`t`t`t   $fStatus")
Write-Host ("  Streamlit Main        http://localhost:$StreamlitMainPort`t`t`t   $sStatus")
Write-Host ("  Streamlit Dashboard   http://localhost:$StreamlitDashboardPort`t`t`t   $dStatus")

Write-Host "${B}+---------------------+-----------------------------------------------+-----------+${N}"

# FastAPI tunnel
$fTunnel = $TunnelHosts[$FastAPIPort]
if ($fTunnel) {
    Write-Host ("  FastAPI Tunnel        https://$fTunnel`t`t   ${G}UP${N}")
} else {
    Write-Host ("  FastAPI Tunnel        (not running)`t`t`t`t   ${R}DOWN${N}")
}

# Streamlit main tunnel
$sTunnel = $TunnelHosts[$StreamlitMainPort]
if ($sTunnel) {
    Write-Host ("  Streamlit Tunnel      https://$sTunnel`t`t   ${G}UP${N}")
} else {
    Write-Host ("  Streamlit Tunnel      (not running)`t`t`t`t   ${R}DOWN${N}")
}

# Dashboard tunnel
$dTunnel = $TunnelHosts[$StreamlitDashboardPort]
if ($dTunnel) {
    Write-Host ("  Dashboard Tunnel      https://$dTunnel`t`t   ${G}UP${N}")
} else {
    Write-Host ("  Dashboard Tunnel      (not running)`t`t`t`t   ${R}DOWN${N}")
}

Write-Host "${B}+---------------------+-----------------------------------------------+-----------+${N}"

# GPU / DB summary row
$gpuLabel = if ($gpuOk) { "${G}OK${N} " } else { "${R}N/A${N}" }
$dbLabel  = if ($dbReady) { "${G}OK${N} " } else { "${R}N/A${N}" }
Write-Host ""
Write-Host ("  GPU (CUDA): $gpuLabel     PostgreSQL: $dbLabel")

# ==== Step 6: Twilio setup instructions ====================================
if ($fTunnel) {
    Write-Host ""
    Write-Host "${B}${C}=== Twilio Configuration -- Copy & Paste ===${N}"
    Write-Host ""

    # -- Voice webhook (auto-updated by start_services.ps1, shown for reference)
    Write-Host "${B}1. Voice Webhook (Phone Number)${N}"
    Write-Host "   URL:  ${G}https://$fTunnel/twilio/voice${N}"
    Write-Host "   Where: Twilio Console -> Phone Numbers -> +19788198953 -> Voice & Fax"
    Write-Host "          Set 'A call comes in' to this URL (HTTP GET)"
    Write-Host "   Auto:  start_services.ps1 updates this via API -- no manual step needed"
    Write-Host ""

    # -- WhatsApp sandbox (MUST be manual)
    Write-Host "${B}2. WhatsApp Sandbox (Manual -- the one you need to do)${N}"
    Write-Host "   URL:  ${G}https://$fTunnel/twilio/whatsapp${N}"
    Write-Host "   Where: Twilio Console -> Messaging -> Try it out -> WhatsApp Sandbox"
    Write-Host "          Paste in the 'When a message comes in' field (HTTP POST)"
    Write-Host "   ${Y}>> THIS is the one you must update manually each time the tunnel changes${N}"
    Write-Host ""

    # -- Status callback (for reference)
    Write-Host "${B}3. Outbound Call Status (optional)${N}"
    Write-Host "   URL:  ${C}https://$fTunnel/twilio/outbound/status${N}"
    Write-Host "   Where: Twilio Console -> Phone Numbers -> +19788198953 -> Voice & Fax"
    Write-Host "          Set 'Call status changes' to this URL (HTTP POST)"
    Write-Host ""
}

# ==== Step 7: Shareable quick-links ========================================
Write-Host ""
Write-Host "${B}${C}=== Shareable Links ===${N}"
Write-Host ""
if ($sTunnel) {
    Write-Host "  Chatbot:   ${C}https://$sTunnel${N}"
} else {
    Write-Host "  Chatbot:   ${R}http://localhost:$StreamlitMainPort${N} (local only)"
}
if ($dTunnel) {
    Write-Host "  Dashboard: ${C}https://$dTunnel${N}"
} else {
    Write-Host "  Dashboard: ${R}http://localhost:$StreamlitDashboardPort${N} (local only)"
}

# ==== Step 8: Help if things are down ======================================
if (-not ($fastApiUp -and $streamlitUp -and $dashUp -and $fTunnel -and $sTunnel -and $dTunnel)) {
    Write-Host ""
    Write-Host "${Y}Some services are DOWN. To restart everything:${N}"
    Write-Host "  ${C}.\start_services.ps1 -WithStreamlit${N}"
}

Write-Host ""
