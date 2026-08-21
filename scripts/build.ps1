$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvDirectory = Join-Path $projectRoot ".venv"
$ffmpegPath = Join-Path $projectRoot "tools\ffmpeg.exe"

if (-not (Test-Path -LiteralPath $ffmpegPath)) {
    throw "FFmpeg is missing. Run scripts\get-ffmpeg.ps1 before building."
}

$configuredPython = $env:AERORECORDER_PYTHON
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($configuredPython -and (Test-Path -LiteralPath $configuredPython)) {
    $pythonExecutable = $configuredPython
} elseif ($pythonCommand) {
    $pythonExecutable = $pythonCommand.Source
} else {
    $commonPython = Get-ChildItem -Path "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe","$env:ProgramFiles\Python*\python.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($commonPython) {
        $pythonExecutable = $commonPython.FullName
    } else {
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if (-not $launcher) {
            throw "Python 3 was not found."
        }
        $pythonExecutable = $launcher.Source
    }
}

if (-not (Test-Path -LiteralPath $venvDirectory)) {
    if ((Split-Path -Leaf $pythonExecutable) -ieq "py.exe") {
        & $pythonExecutable -3 -m venv $venvDirectory
    } else {
        & $pythonExecutable -m venv $venvDirectory
    }
}

$venvPython = Join-Path $venvDirectory "Scripts\python.exe"
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $projectRoot "requirements-dev.txt")
& $venvPython (Join-Path $projectRoot "scripts\create_icon.py")
Push-Location $projectRoot
try {
    & $venvPython -m PyInstaller --noconfirm --clean "AeroRecorder.spec"
} finally {
    Pop-Location
}

$innoCandidates = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$innoCompiler = $innoCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($innoCompiler) {
    & $innoCompiler (Join-Path $projectRoot "installer\AeroRecorder.iss")
    Write-Host "Installer created in installer\output\AeroRecorder-Setup.exe"
} else {
    Write-Host "Portable application created in dist\AeroRecorder"
    Write-Host "Install Inno Setup 6 and run this script again to produce AeroRecorder-Setup.exe."
}
