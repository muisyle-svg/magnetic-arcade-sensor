param(
    [string]$PythonPath = "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe",
    [string]$AssetDirectory = "",
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"
$ProjectDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDirectory

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python was not found at $PythonPath. Pass -PythonPath with the full path to python.exe."
}

if (-not $AssetDirectory) {
    $AssetDirectory = Join-Path (Split-Path -Parent $ProjectDirectory) "..\Emerald"
}
$AssetDirectory = [System.IO.Path]::GetFullPath($AssetDirectory)
if (-not (Test-Path -LiteralPath $AssetDirectory -PathType Container)) {
    throw "ChaosHeist media was not found at $AssetDirectory. Pass -AssetDirectory with the media folder."
}
$env:CHAOS_HEIST_ASSET_DIR = $AssetDirectory

& $PythonPath -c "import ctypes,sys; raise SystemExit(0 if sys.version_info[:2] == (3, 14) and ctypes.sizeof(ctypes.c_void_p) == 8 else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "ChaosHeist production builds require 64-bit Python 3.14."
}

if ($InstallDependencies) {
    & $PythonPath -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
}

& $PythonPath -m pip check
if ($LASTEXITCODE -ne 0) { throw "The Python environment has broken dependencies." }

$InstalledPackages = @(& $PythonPath -m pip freeze)
foreach ($Requirement in Get-Content -LiteralPath requirements.txt) {
    $Requirement = $Requirement.Trim()
    if (-not $Requirement -or $Requirement.StartsWith("#")) { continue }
    if ($InstalledPackages -notcontains $Requirement) {
        throw "Pinned dependency is missing or has the wrong version: $Requirement"
    }
}

& $PythonPath -m py_compile chaos_heist.py test_chaos_heist.py
if ($LASTEXITCODE -ne 0) { throw "Python compilation failed; the EXE was not rebuilt." }

& $PythonPath -m unittest -v test_chaos_heist.py
if ($LASTEXITCODE -ne 0) { throw "Tests failed; the EXE was not rebuilt." }

foreach ($BuildOutput in @("dist-chaos-heist", "build-chaos-heist")) {
    if (Test-Path -LiteralPath $BuildOutput) {
        Remove-Item -LiteralPath $BuildOutput -Recurse -Force
    }
}

& $PythonPath -m PyInstaller --noconfirm --clean `
    --distpath dist-chaos-heist `
    --workpath build-chaos-heist `
    ChaosHeist.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

Copy-Item -LiteralPath chaos-heist-config.json -Destination dist-chaos-heist\chaos-heist-config.json -Force
Copy-Item -LiteralPath README.md -Destination dist-chaos-heist\README-ChaosHeist.md -Force
Copy-Item -LiteralPath VERSION.txt -Destination dist-chaos-heist\VERSION.txt -Force
Copy-Item -LiteralPath PRODUCTION-REVIEW.md -Destination dist-chaos-heist\PRODUCTION-REVIEW.md -Force
Copy-Item -LiteralPath ChaosHeistController\ChaosHeistController.ino -Destination dist-chaos-heist\ChaosHeistController.ino -Force

$SelfTestOutput = Join-Path $ProjectDirectory "dist-chaos-heist\self-test-output.txt"
$SelfTestError = Join-Path $ProjectDirectory "dist-chaos-heist\self-test-error.txt"
Remove-Item -LiteralPath $SelfTestOutput, $SelfTestError -Force -ErrorAction SilentlyContinue
$SelfTest = Start-Process `
    -FilePath .\dist-chaos-heist\ChaosHeist.exe `
    -ArgumentList "--self-test" `
    -WindowStyle Hidden `
    -RedirectStandardOutput $SelfTestOutput `
    -RedirectStandardError $SelfTestError `
    -Wait `
    -PassThru
if ($SelfTest.ExitCode -ne 0) {
    $SelfTestDetails = @(
        Get-Content -LiteralPath $SelfTestOutput -ErrorAction SilentlyContinue
        Get-Content -LiteralPath $SelfTestError -ErrorAction SilentlyContinue
    ) -join [Environment]::NewLine
    throw "The packaged ChaosHeist self-test failed.$([Environment]::NewLine)$SelfTestDetails"
}
Remove-Item -LiteralPath $SelfTestOutput, $SelfTestError -Force -ErrorAction SilentlyContinue

$ReleaseFiles = @(
    "dist-chaos-heist\ChaosHeist.exe",
    "dist-chaos-heist\chaos-heist-config.json",
    "dist-chaos-heist\README-ChaosHeist.md",
    "dist-chaos-heist\VERSION.txt",
    "dist-chaos-heist\PRODUCTION-REVIEW.md",
    "dist-chaos-heist\ChaosHeistController.ino"
)

$HashLines = foreach ($ReleaseFile in $ReleaseFiles) {
    $Hash = Get-FileHash -Algorithm SHA256 -LiteralPath $ReleaseFile
    "{0}  {1}" -f $Hash.Hash.ToLowerInvariant(), (Split-Path -Leaf $ReleaseFile)
}
$HashLines | Set-Content -LiteralPath dist-chaos-heist\SHA256SUMS.txt -Encoding ascii
$ReleaseFiles += "dist-chaos-heist\SHA256SUMS.txt"

$VersionedArchive = "dist-chaos-heist\ChaosHeist-1.0.0-windows-x64.zip"
Compress-Archive -LiteralPath $ReleaseFiles `
    -DestinationPath $VersionedArchive `
    -Force
Copy-Item -LiteralPath $VersionedArchive -Destination dist-chaos-heist\ChaosHeist-production.zip -Force

$ArchiveHash = Get-FileHash -Algorithm SHA256 -LiteralPath $VersionedArchive
("{0}  {1}" -f $ArchiveHash.Hash.ToLowerInvariant(), (Split-Path -Leaf $VersionedArchive)) | `
    Set-Content -LiteralPath "$VersionedArchive.sha256" -Encoding ascii
Write-Host "Built dist-chaos-heist\ChaosHeist.exe"
Write-Host "Packed $VersionedArchive"
Write-Host "Updated dist-chaos-heist\ChaosHeist-production.zip"
