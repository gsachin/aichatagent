param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("as","bn","brx","gu","hi","kn","ml","mni","mr","or","raj","ta","te")]
    [string]$Language
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$checkpoints = Join-Path $PSScriptRoot "checkpoints"
$zip = Join-Path $checkpoints "$Language.zip"
$url = "https://github.com/AI4Bharat/Indic-TTS/releases/download/v1-checkpoints-release/$Language.zip"

New-Item -ItemType Directory -Force -Path $checkpoints | Out-Null

if (-not (Test-Path $zip)) {
    Write-Host "Downloading $Language checkpoints (~1.5 GB)..."
    Invoke-WebRequest -Uri $url -OutFile $zip
} else {
    Write-Host "Using existing $zip"
}

Write-Host "Extracting to $checkpoints ..."
Expand-Archive -Path $zip -DestinationPath $checkpoints -Force

Write-Host "Done. Expected layout: $checkpoints\$Language\fastpitch\best_model.pth"
