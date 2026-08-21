$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

$configuredPython = $env:AERORECORDER_PYTHON
if ($configuredPython -and (Test-Path -LiteralPath $configuredPython)) {
    & $configuredPython (Join-Path $projectRoot "main.py")
    exit $LASTEXITCODE
}

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    & $venvPython (Join-Path $projectRoot "main.py")
    exit $LASTEXITCODE
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) {
    & $pythonCommand.Source (Join-Path $projectRoot "main.py")
    exit $LASTEXITCODE
}

$commonPython = Get-ChildItem -Path "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe","$env:ProgramFiles\Python*\python.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($commonPython) {
    & $commonPython.FullName (Join-Path $projectRoot "main.py")
    exit $LASTEXITCODE
}

$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) {
    & $launcher.Source -3 (Join-Path $projectRoot "main.py")
    exit $LASTEXITCODE
}

throw "Python 3 was not found. Install it from python.org and enable Add Python to PATH."
