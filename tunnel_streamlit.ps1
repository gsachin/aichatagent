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
    @{ Port = 8501; Label = "Streamlit Main"; Cache = Join-Path $ProjectRoot ".tunnel_8501"; Metrics = 20242 },
    @{ Port = 8502; Label = "Dashboard";      Cache = Join-Path $ProjectRoot ".tunnel_8502"; Metrics = 20243 }
)

function Test-Url($url) {
    try { return (curl.exe -s -o NUL -w "%{http_code}" $url 2>$null) -eq "200" } catch { return $false }
}

Write-Host "${B}${C}Starting Streamlit Tunnels...${N}`n"

foreach ($t in $Tunnels) {
    $port    = $t.Port
    $label   = $t.Label
    $cache   = $t.Cache
    $metrics = $t.Metrics

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
        Write-Host "${Y}  [..]   Starting $label tunnel on port $port ...${N}"
        $stdoutLog = Join-Path $env:TEMP "cloudflared_${port}_stdout.log"
        $stderrLog = Join-Path $env:TEMP "cloudflared_${port}_stderr.log"
        foreach ($f in @($stdoutLog, $stderrLog)) { if (Test-Path $f) { Remove-Item $f -Force } }

        Start-Process -FilePath "cloudflared" `
            -ArgumentList "tunnel", "--url", "http://localhost:$port", "--metrics", "localhost:$metrics" `
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
            [System.IO.File]::WriteAllText($cache, $hostname)
            Write-Host "${G}  [OK]   $label tunnel started: $hostname${N}"
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
        Write-Host "  ${label}: ${C}https://$hostname${N}"
    } else {
        Write-Host "  ${label}: ${R}not running${N}"
    }
}
Write-Host ""
