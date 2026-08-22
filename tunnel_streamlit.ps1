<#
.SYNOPSIS
    Start Cloudflare tunnels for Streamlit Main (8501) and Dashboard (8502).
.DESCRIPTION
    Quick launcher for the two Streamlit public URLs.  FastAPI (8000) tunnel
    is handled by start_services.ps1 — this script only covers the UI ports.
.EXAMPLE
    .\tunnel_streamlit.ps1
#>

$ProjectRoot = $PSScriptRoot
$ESC  = [char]27
$G = "$ESC[92m"; $Y = "$ESC[93m"; $R = "$ESC[91m"; $C = "$ESC[96m"
$B = "$ESC[1m"; $N = "$ESC[0m"

$Tunnels = @(
    @{ Port = 8501; Label = "Streamlit Chat (Main)"; Cache = Join-Path $ProjectRoot ".tunnel_8501" },
    @{ Port = 8502; Label = "Dashboard";            Cache = Join-Path $ProjectRoot ".tunnel_8502" }
)

function Test-Url($url) {
    try { return (curl.exe -s -o NUL -w "%{http_code}" $url 2>$null) -eq "200" } catch { return $false }
}

# cloudflared must exist before we burn 40s timeouts per port
if (-not (Get-Command "cloudflared" -ErrorAction SilentlyContinue)) {
    Write-Host "${R}cloudflared not found! Install from: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/${N}"
    exit 1
}

# A tunnel for a port may already be running (started by check_and_tunnel.ps1
# or bootstrap) with no usable cache — never start a second one.
function Get-TunnelProcessForPort($port) {
    Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "localhost:$port" }
}

Write-Host "${B}${C}Starting Streamlit Tunnels...${N}`n"

foreach ($t in $Tunnels) {
    $port    = $t.Port
    $label   = $t.Label
    $cache   = $t.Cache

    # Check if a tunnel is already alive for this port
    $hostname = $null
    if (Test-Path $cache) {
        $cached = (Get-Content $cache -Raw).Trim()
        if ($cached -and (Test-Url "https://$cached/")) {
            $hostname = $cached
            Write-Host "${G}  [OK]   $label tunnel already alive: $hostname${N}"
        }
    }

    if (-not $hostname) {
        # Never double-start: a live cloudflared for this port may exist
        # without a usable cache URL (e.g. logs were cleaned).
        $existing = Get-TunnelProcessForPort $port
        if ($existing) {
            Write-Host "${Y}  [..]   $label tunnel process is running but its URL is unknown${N}"
            Write-Host "${Y}         Kill it and re-run to recreate: pkill -f cloudflared / taskkill /IM cloudflared.exe${N}"
            continue
        }

        Write-Host "${Y}  [..]   Starting $label tunnel on port $port ...${N}"
        $stdoutLog = Join-Path $env:TEMP "cloudflared_${port}_stdout.log"
        $stderrLog = Join-Path $env:TEMP "cloudflared_${port}_stderr.log"
        foreach ($f in @($stdoutLog, $stderrLog)) { if (Test-Path $f) { Remove-Item $f -Force } }

        Start-Process -FilePath "cloudflared" `
            -ArgumentList "tunnel", "--url", "http://localhost:$port", "--metrics", "localhost:0" `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog `
            -WindowStyle Hidden

        $attempt = 0
        while (-not $hostname -and $attempt -lt 20) {
            Start-Sleep -Seconds 2
            $attempt++
            foreach ($log in @($stdoutLog, $stderrLog)) {
                if (Test-Path $log) {
                    $content = Get-Content $log -Raw -ErrorAction SilentlyContinue
                    if ($content -match 'https://([a-zA-Z0-9\-]+\.trycloudflare\.com)') {
                        $hostname = $matches[1]
                        break
                    }
                }
            }
        }

        if ($hostname) {
            # Verify reachable before declaring OK — fresh quick-tunnel
            # hostnames can take a minute to resolve (DNS propagation).
            $verify = $false
            for ($v = 0; $v -lt 6 -and -not $verify; $v++) {
                if ($v -gt 0) { Start-Sleep -Seconds 3 }
                $verify = Test-Url "https://$hostname/"
            }
            [System.IO.File]::WriteAllText($cache, $hostname)
            if ($verify) {
                Write-Host "${G}  [OK]   $label tunnel started: $hostname${N}"
            } else {
                Write-Host "${Y}  [..]   $label tunnel started but not yet reachable (DNS warm-up): $hostname${N}"
            }
        } else {
            Write-Host "${R}  [FAIL] $label tunnel did not start${N}"
        }
    }
}

Write-Host ""
Write-Host "${B}${C}=== Shareable Links ===${N}"
Write-Host ""

foreach ($t in $Tunnels) {
    $cache = $t.Cache
    $label = $t.Label
    if (Test-Path $cache) {
        $hostname = (Get-Content $cache -Raw).Trim()
        # Re-verify before sharing — a cached URL can outlive its tunnel.
        if ($hostname -and (Test-Url "https://$hostname/")) {
            Write-Host "  ${label}: ${C}https://$hostname${N}"
        } else {
            Write-Host "  ${label}: ${R}tunnel down (re-run to recreate)${N}"
        }
    } else {
        Write-Host "  ${label}: ${R}not running${N}"
    }
}

# Final echo of the Streamlit chat public URL — copy/paste ready.
$chatCache = Join-Path $ProjectRoot ".tunnel_8501"
if (Test-Path $chatCache) {
    $chatHost = (Get-Content $chatCache -Raw).Trim()
    if ($chatHost -and (Test-Url "https://$chatHost/")) {
        Write-Host ""
        Write-Host "${B}${G}Streamlit Chat public URL: ${C}https://$chatHost${N}"
    }
}
Write-Host ""
