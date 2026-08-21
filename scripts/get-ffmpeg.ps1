$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$toolsDirectory = Join-Path $projectRoot "tools"
$archivePath = Join-Path ([System.IO.Path]::GetTempPath()) "aerorecorder-ffmpeg.zip"
$extractDirectory = Join-Path ([System.IO.Path]::GetTempPath()) "aerorecorder-ffmpeg"
$downloadUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$resolvedExtractDirectory = [System.IO.Path]::GetFullPath($extractDirectory)

if (-not $resolvedExtractDirectory.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a temporary extraction path outside the system temporary directory."
}

New-Item -ItemType Directory -Force -Path $toolsDirectory | Out-Null
if (Test-Path -LiteralPath $extractDirectory) {
    Remove-Item -LiteralPath $extractDirectory -Recurse -Force
}

Write-Host "Downloading the portable FFmpeg essentials build..."
Invoke-WebRequest -Uri $downloadUrl -OutFile $archivePath
Expand-Archive -LiteralPath $archivePath -DestinationPath $extractDirectory -Force

$ffmpeg = Get-ChildItem -LiteralPath $extractDirectory -Filter "ffmpeg.exe" -Recurse | Select-Object -First 1
if (-not $ffmpeg) {
    throw "The downloaded archive did not contain ffmpeg.exe."
}

Copy-Item -LiteralPath $ffmpeg.FullName -Destination (Join-Path $toolsDirectory "ffmpeg.exe") -Force
$license = Get-ChildItem -LiteralPath $extractDirectory -File -Recurse | Where-Object {
    $_.Name -match "^(LICENSE|COPYING)(\..+)?$"
} | Select-Object -First 1
if ($license) {
    Copy-Item -LiteralPath $license.FullName -Destination (Join-Path $toolsDirectory "FFMPEG-LICENSE.txt") -Force
}

Remove-Item -LiteralPath $archivePath -Force
Remove-Item -LiteralPath $extractDirectory -Recurse -Force
Write-Host "FFmpeg is ready in $toolsDirectory"
