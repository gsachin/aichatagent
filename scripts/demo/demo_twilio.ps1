<#
.SYNOPSIS
    Back up, repoint, restore, or inspect the Twilio voice webhook for the demo.
.DESCRIPTION
    Twilio has one voice webhook and one status callback per phone number.
    -Enable saves the current values to .twilio_repoint_backup.json and points
    them at the demo tunnel. -Restore puts the saved values back.

    The WhatsApp Sandbox webhook has no Twilio API; the script prints the URL
    to set manually in the Twilio Console.
.PARAMETER Enable
    Save the current voice settings (first run) and repoint to -TunnelUrl.
.PARAMETER Restore
    Restore the saved voice settings.
.PARAMETER Status
    Show current Twilio values vs the backup.
.PARAMETER TunnelUrl
    Demo tunnel host, with or without https://.
#>
[CmdletBinding()]
param(
    [switch]$Enable,
    [switch]$Restore,
    [switch]$Status,
    [string]$TunnelUrl
)

$ErrorActionPreference = 'Stop'
$DemoRoot = $PSScriptRoot
Set-Location $DemoRoot

function Write-Ok([string]$Text) { Write-Host "  [OK] $Text" -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host "  [WARN] $Text" -ForegroundColor Yellow }

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

$actions = @($Enable, $Restore, $Status) | Where-Object { $_ }
if ($actions.Count -eq 0) {
    Write-Host "Usage:"
    Write-Host "  .\demo_twilio.ps1 -Enable -TunnelUrl <host>"
    Write-Host "  .\demo_twilio.ps1 -Restore"
    Write-Host "  .\demo_twilio.ps1 -Status"
    exit 1
}
if ($actions.Count -gt 1) {
    throw "Use only one of -Enable, -Restore, -Status."
}

$envPath = Join-Path $DemoRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    throw ".env is missing. Run .\demo_setup.ps1 first."
}
$envMap = Read-DotEnv $envPath

$sid = [string]$envMap['TWILIO_ACCOUNT_SID']
$token = [string]$envMap['TWILIO_AUTH_TOKEN']
$phone = [string]$envMap['TWILIO_PHONE_NUMBER']
if (-not $sid -or -not $token) {
    throw "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set in .env."
}
if (-not $phone) {
    throw "TWILIO_PHONE_NUMBER must be set in .env for voice webhook switching."
}

$auth = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("${sid}:${token}"))
$headers = @{ Authorization = "Basic $auth" }
$apiBase = "https://api.twilio.com/2010-04-01/Accounts/$sid"
$backupPath = Join-Path $DemoRoot '.twilio_repoint_backup.json'

function Get-TwilioNumber {
    $encoded = [uri]::EscapeDataString($phone)
    $response = Invoke-RestMethod -Uri "$apiBase/IncomingPhoneNumbers.json?PhoneNumber=$encoded" -Headers $headers -Method Get
    if (-not $response.incoming_phone_numbers -or $response.incoming_phone_numbers.Count -eq 0) {
        throw "Twilio number $phone was not found in account $sid."
    }
    return $response.incoming_phone_numbers[0]
}

function Update-TwilioNumber([object]$Number, [string]$VoiceUrl, [string]$VoiceMethod, [string]$StatusUrl, [string]$StatusMethod) {
    $body = @{
        VoiceUrl             = $VoiceUrl
        VoiceMethod          = $VoiceMethod
        StatusCallback       = $StatusUrl
        StatusCallbackMethod = $StatusMethod
    }
    return Invoke-RestMethod -Uri "$apiBase/IncomingPhoneNumbers/$($Number.sid).json" -Headers $headers -Method Post -Body $body
}

function Show-TwilioNumber([object]$Number, [string]$Label) {
    Write-Host ""
    Write-Host "$Label" -ForegroundColor Cyan
    Write-Host "  phone:               $($Number.phone_number)"
    Write-Host "  voice_url:           $($Number.voice_url)"
    Write-Host "  voice_method:        $($Number.voice_method)"
    Write-Host "  status_callback:     $($Number.status_callback)"
    Write-Host "  status_callback_m:   $($Number.status_callback_method)"
}

function Get-DevelopmentWhatsAppUrl {
    $devTunnelFile = 'D:\project\universityDemo\.whatsapp_tunnel'
    if (-not (Test-Path -LiteralPath $devTunnelFile)) { return $null }
    $hostNameRaw = Get-Content -LiteralPath $devTunnelFile -Raw
    $hostName = if ($null -eq $hostNameRaw) { "" } else { ([string]$hostNameRaw).Trim() }
    if (-not $hostName) { return $null }
    $hostName = $hostName -replace '^https?://', ''
    return "https://$hostName/twilio/whatsapp"
}

function Save-Backup([object]$Number) {
    if (Test-Path -LiteralPath $backupPath) {
        Write-Warn "Using existing backup: $backupPath"
        return
    }
    $payload = [ordered]@{
        phone_number              = $Number.phone_number
        incoming_phone_number_sid = $Number.sid
        voice_url                 = $Number.voice_url
        voice_method              = $Number.voice_method
        status_callback           = $Number.status_callback
        status_callback_method    = $Number.status_callback_method
        whatsapp_dev_url          = Get-DevelopmentWhatsAppUrl
        saved_at_utc              = (Get-Date).ToUniversalTime().ToString('o')
    }
    $payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $backupPath -Encoding UTF8
    Write-Ok "Saved current voice settings to $backupPath"
}

$number = Get-TwilioNumber

if ($Enable) {
    if (-not $TunnelUrl) {
        throw "-Enable requires -TunnelUrl <host>."
    }
    $hostName = $TunnelUrl.Trim()
    $hostName = $hostName -replace '^https?://', ''
    $hostName = $hostName.TrimEnd('/')
    if (-not $hostName) { throw "-TunnelUrl is empty." }

    Save-Backup $number

    $voiceUrl = "https://$hostName/twilio/voice"
    $statusUrl = "https://$hostName/twilio/outbound/status"
    Update-TwilioNumber $number $voiceUrl 'GET' $statusUrl 'POST' | Out-Null

    $after = Get-TwilioNumber
    if ($after.voice_url -ne $voiceUrl) {
        throw "Twilio read-back mismatch: voice_url is '$($after.voice_url)', expected '$voiceUrl'."
    }
    if ($after.status_callback -ne $statusUrl) {
        throw "Twilio read-back mismatch: status_callback is '$($after.status_callback)', expected '$statusUrl'."
    }

    Show-TwilioNumber $after "Twilio voice webhook now points at the demo"
    Write-Host ""
    Write-Host "WHATSAPP SANDBOX - set 'When a message comes in' to:" -ForegroundColor Yellow
    Write-Host "  https://$hostName/twilio/whatsapp  (HTTP POST)" -ForegroundColor Yellow
    $devWhatsApp = Get-DevelopmentWhatsAppUrl
    if ($devWhatsApp) {
        Write-Host "Restore it to: $devWhatsApp" -ForegroundColor Yellow
    }
    exit 0
}

if ($Restore) {
    if (-not (Test-Path -LiteralPath $backupPath)) {
        throw "No backup found at $backupPath. Nothing to restore."
    }
    $backup = Get-Content -LiteralPath $backupPath -Raw | ConvertFrom-Json
    $voiceUrl = [string]$backup.voice_url
    $voiceMethod = [string]$backup.voice_method
    $statusUrl = [string]$backup.status_callback
    $statusMethod = [string]$backup.status_callback_method
    if (-not $voiceMethod) { $voiceMethod = 'GET' }
    if (-not $statusMethod) { $statusMethod = 'POST' }

    Update-TwilioNumber $number $voiceUrl $voiceMethod $statusUrl $statusMethod | Out-Null
    $after = Get-TwilioNumber
    if ($after.voice_url -ne $voiceUrl) {
        throw "Twilio restore read-back mismatch: voice_url is '$($after.voice_url)', expected '$voiceUrl'."
    }

    Show-TwilioNumber $after "Twilio voice webhook restored"
    if ($backup.whatsapp_dev_url) {
        Write-Host ""
        Write-Host "Restore the WhatsApp Sandbox webhook to:" -ForegroundColor Yellow
        Write-Host "  $($backup.whatsapp_dev_url)  (HTTP POST)" -ForegroundColor Yellow
    }
    exit 0
}

if ($Status) {
    Show-TwilioNumber $number "Current Twilio values"
    $waCheck = Join-Path $DemoRoot 'check_whatsapp_webhook.py'
    $venvPy = Join-Path $DemoRoot '.venv\Scripts\python.exe'
    if ((Test-Path -LiteralPath $waCheck) -and (Test-Path -LiteralPath $venvPy)) {
        Write-Host ""
        & $venvPy $waCheck --minutes 60
    }
    if (Test-Path -LiteralPath $backupPath) {
        $backup = Get-Content -LiteralPath $backupPath -Raw | ConvertFrom-Json
        Write-Host ""
        Write-Host "Backup ($backupPath)" -ForegroundColor Cyan
        Write-Host "  voice_url:           $($backup.voice_url)"
        Write-Host "  voice_method:        $($backup.voice_method)"
        Write-Host "  status_callback:     $($backup.status_callback)"
        Write-Host "  status_callback_m:   $($backup.status_callback_method)"
        if ($backup.whatsapp_dev_url) {
            Write-Host "  whatsapp_dev_url:    $($backup.whatsapp_dev_url)"
        }
    } else {
        Write-Host ""
        Write-Warn "No backup file exists."
    }
    exit 0
}
