# Build the Windows installer in one step, using FlowBench's own installer
# (flowbench_installer/) - Inno Setup is no longer needed:
#   server utilities + GUI (build_all.ps1) -> uninstaller -> payload -> setup exe
# Output: installer\FlowBench-Setup-<version>.exe
# Needs: Python with requirements.txt and PyInstaller.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$started = Get-Date

function Get-Version($Path, $Pattern) {
    $match = Select-String -LiteralPath $Path -Pattern $Pattern | Select-Object -First 1
    if (-not $match) { throw "Could not read the version from $Path" }
    return $match.Matches[0].Groups[1].Value
}

function Invoke-Checked($Label) {
    if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit code $LASTEXITCODE)." }
}

# Check everything up front so a missing tool fails in seconds, not after
# several minutes of PyInstaller.
Write-Host "==> Checking build tools"
$version = Get-Version (Join-Path (Join-Path "app" "services") "updater.py") '^APP_VERSION = "([^"]+)"'

python -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed. Run: python -m pip install pyinstaller"
}
Write-Host "    version $version"

Write-Host "==> Building server utilities and GUI"
& (Join-Path $PSScriptRoot "build_all.ps1")

Write-Host "==> Building uninstaller"
python -m PyInstaller --clean --noconfirm Uninstall.spec
Invoke-Checked "PyInstaller (Uninstall.spec)"

Write-Host "==> Packing payload"
python (Join-Path "tools" "make_payload.py")
Invoke-Checked "tools/make_payload.py"

Write-Host "==> Building installer"
python -m PyInstaller --clean --noconfirm Setup.spec
Invoke-Checked "PyInstaller (Setup.spec)"

$name = "FlowBench-Setup-$version.exe"
$built = Join-Path "dist" $name
if (-not (Test-Path -LiteralPath $built)) { throw "PyInstaller finished but $built is missing." }
if ((Get-Item -LiteralPath $built).LastWriteTime -lt $started) { throw "$built was not rebuilt by this run." }
$outDir = Join-Path $PSScriptRoot "installer"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$setup = Join-Path $outDir $name
Copy-Item -LiteralPath $built -Destination $setup -Force
$item = Get-Item -LiteralPath $setup
$sizeMb = [math]::Round($item.Length / 1MB, 1)
$hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLower()
Write-Host ""
Write-Host "Installer ready: $setup ($sizeMb MB)"
Write-Host "SHA256: $hash"
Write-Host "Upload it to the GitHub release as $name - the in-app updater looks for that name."
