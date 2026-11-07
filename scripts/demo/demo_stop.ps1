<#
.SYNOPSIS
    Stop the UniversityBotDemoAPI demo and restore the Twilio voice webhook.
.DESCRIPTION
    - Restores the voice webhook and status callback from
      .twilio_repoint_backup.json before stopping the processes.
    - Prints the development WhatsApp Sandbox URL to restore manually.
    - Stops only processes recorded in .demo_pids.json whose command line
      belongs to this demo directory (or the CRM API it started).
    - Never kills whatever happens to own port 8000.
.PARAMETER KeepCrm
    Leave the Salesforce Admission API running even if the demo started it.
#>
[CmdletBinding()]
param(
    [switch]$KeepCrm
)

$ErrorActionPreference = 'Stop'
$DemoRoot = $PSScriptRoot
Set-Location $DemoRoot

function Write-Step([string]$Text) { Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "  [OK] $Text" -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host "  [WARN] $Text" -ForegroundColor Yellow }

function Stop-ProcessTree([int]$ProcessId) {
    $children = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.ParentProcessId -eq $ProcessId }
    foreach ($child in $children) {
        Stop-ProcessTree -ProcessId ([int]$child.ProcessId)
    }
    Stop-Process -Id $ProcessId -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 300
    if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-DemoProcess([int]$ProcessId, [string]$ExpectedHint) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) {
        return
    }
    $commandLine = [string]$process.CommandLine
    if ($ExpectedHint -and $commandLine -notlike "*$ExpectedHint*") {
        Write-Warn "PID $ProcessId does not match '$ExpectedHint'; leaving it alone."
        return
    }
    Stop-ProcessTree -ProcessId $ProcessId
    Write-Ok "Stopped process tree rooted at PID $ProcessId"
}

# ---------------------------------------------------------------------------
# Restore Twilio voice first
# ---------------------------------------------------------------------------
$backupPath = Join-Path $DemoRoot '.twilio_repoint_backup.json'
if (Test-Path -LiteralPath $backupPath) {
    Write-Step "Restoring Twilio voice webhook"
    try {
        & (Join-Path $DemoRoot 'demo_twilio.ps1') -Restore
        Write-Ok "Twilio voice webhook restored"
    } catch {
        Write-Warn "Twilio restore failed: $($_.Exception.Message)"
        Write-Warn "Run .\demo_twilio.ps1 -Restore manually before restarting development."
    }
}

# ---------------------------------------------------------------------------
# Print the WhatsApp restore URL
# ---------------------------------------------------------------------------
$whatsappUrl = $null
if (Test-Path -LiteralPath $backupPath) {
    try {
        $backup = Get-Content -LiteralPath $backupPath -Raw | ConvertFrom-Json
        if ($backup.whatsapp_dev_url) { $whatsappUrl = [string]$backup.whatsapp_dev_url }
    } catch {
        # fall through to the development tunnel file
    }
}
if (-not $whatsappUrl) {
    $devTunnelFile = 'D:\project\universityDemo\.whatsapp_tunnel'
    if (Test-Path -LiteralPath $devTunnelFile) {
        $devHostRaw = Get-Content -LiteralPath $devTunnelFile -Raw
        $devHost = if ($null -eq $devHostRaw) { "" } else { ([string]$devHostRaw).Trim() }
        if ($devHost) { $whatsappUrl = "https://$devHost/twilio/whatsapp" }
    }
}
Write-Host ""
if ($whatsappUrl) {
    Write-Host "RESTORE THE WHATSAPP SANDBOX WEBHOOK TO:" -ForegroundColor Yellow
    Write-Host "  $whatsappUrl  (HTTP POST)" -ForegroundColor Yellow
} else {
    Write-Host "RESTORE THE WHATSAPP SANDBOX WEBHOOK TO THE DEVELOPMENT TUNNEL URL." -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# Stop the recorded processes
# ---------------------------------------------------------------------------
$statePath = Join-Path $DemoRoot '.demo_pids.json'
if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Warn "No .demo_pids.json; no demo processes to stop."
    exit 0
}

$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json

Write-Step "Stopping the demo stack"

if ($state.tunnel_pid) {
    Stop-DemoProcess -ProcessId ([int]$state.tunnel_pid) -ExpectedHint 'cloudflared'
}
if ($state.ui_tunnel_pid) {
    Stop-DemoProcess -ProcessId ([int]$state.ui_tunnel_pid) -ExpectedHint 'cloudflared'
}
if ($state.dashboard_tunnel_pid) {
    Stop-DemoProcess -ProcessId ([int]$state.dashboard_tunnel_pid) -ExpectedHint 'cloudflared'
}

if ($state.processes) {
    foreach ($property in $state.processes.PSObject.Properties) {
        Stop-DemoProcess -ProcessId ([int]$property.Value) -ExpectedHint $DemoRoot
    }
}

if ($state.crm_started_by_demo -and $state.crm_pid -and -not $KeepCrm) {
    Stop-DemoProcess -ProcessId ([int]$state.crm_pid) -ExpectedHint 'salesforce-admission-api'
}

Remove-Item -LiteralPath $statePath -Force
$tunnelFile = Join-Path $DemoRoot '.whatsapp_tunnel'
if (Test-Path -LiteralPath $tunnelFile) {
    Remove-Item -LiteralPath $tunnelFile -Force
}

Write-Host ""
Write-Ok "Demo stopped."
