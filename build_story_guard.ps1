param(
    [string]$PythonPath = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe",
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"
$ProjectDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDirectory

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python was not found at $PythonPath. Pass -PythonPath with the full path to python.exe."
}

if ($InstallDependencies) {
    & $PythonPath -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
}

& $PythonPath -m unittest -v test_magnet_arcade_guard.py
if ($LASTEXITCODE -ne 0) { throw "Tests failed; the EXE was not rebuilt." }

& $PythonPath -m PyInstaller --noconfirm --clean `
    --distpath dist-ring-enabled `
    --workpath build-ring-enabled `
    MagnetArcadeGuardStoryMode.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

Copy-Item -LiteralPath guard-config.json -Destination dist-ring-enabled\guard-config.json -Force
Copy-Item -LiteralPath README.md -Destination dist-ring-enabled\README-RingEnabled.md -Force
Copy-Item -LiteralPath VERSION.txt -Destination dist-ring-enabled\VERSION.txt -Force
Copy-Item -LiteralPath magnet_test\magnet_test.ino -Destination dist-ring-enabled\magnet_test-ring-enabled.ino -Force

$ReleaseFiles = @(
    "dist-ring-enabled\MagnetArcadeGuardRings.exe",
    "dist-ring-enabled\guard-config.json",
    "dist-ring-enabled\README-RingEnabled.md",
    "dist-ring-enabled\VERSION.txt",
    "dist-ring-enabled\magnet_test-ring-enabled.ino"
)
Compress-Archive -LiteralPath $ReleaseFiles `
    -DestinationPath dist-ring-enabled\MagnetArcadeGuardRings-test.zip `
    -Force
Write-Host "Built dist-ring-enabled\MagnetArcadeGuardRings.exe"
Write-Host "Packed dist-ring-enabled\MagnetArcadeGuardRings-test.zip"
