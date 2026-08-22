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
    --distpath dist-story-mode `
    --workpath build-story-mode `
    MagnetArcadeGuardStoryMode.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

Copy-Item -LiteralPath guard-config.json -Destination dist-story-mode\guard-config.json -Force
Copy-Item -LiteralPath README.md -Destination dist-story-mode\README-StoryMode.md -Force
Copy-Item -LiteralPath VERSION.txt -Destination dist-story-mode\VERSION.txt -Force
Copy-Item -LiteralPath magnet_test\magnet_test.ino -Destination dist-story-mode\magnet_test-story-mode.ino -Force

$ReleaseFiles = @(
    "dist-story-mode\MagnetArcadeGuardStoryMode.exe",
    "dist-story-mode\guard-config.json",
    "dist-story-mode\README-StoryMode.md",
    "dist-story-mode\VERSION.txt",
    "dist-story-mode\magnet_test-story-mode.ino"
)
Compress-Archive -LiteralPath $ReleaseFiles `
    -DestinationPath dist-story-mode\MagnetArcadeGuardStoryMode-test.zip `
    -Force
Write-Host "Built dist-story-mode\MagnetArcadeGuardStoryMode.exe"
Write-Host "Packed dist-story-mode\MagnetArcadeGuardStoryMode-test.zip"
