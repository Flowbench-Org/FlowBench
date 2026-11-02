# Build the Windows installer in one step:
#   server utilities + GUI (build_all.ps1) -> Inno Setup (installer.iss)
# Output: installer\FlowBench-Setup-<version>.exe
# Needs: Python with requirements.txt and PyInstaller, plus Inno Setup 6.7+.
# Set $env:ISCC to the full path of ISCC.exe if Inno Setup is installed
# somewhere unusual.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$started = Get-Date

function Find-Iscc {
    if ($env:ISCC) {
        if (Test-Path -LiteralPath $env:ISCC) { return $env:ISCC }
        throw "ISCC is set to '$env:ISCC' but that file does not exist."
    }
    $cmd = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $roots = @(${env:ProgramFiles(x86)}, $env:ProgramFiles)
    if ($env:LOCALAPPDATA) { $roots += (Join-Path $env:LOCALAPPDATA "Programs") }
    foreach ($root in ($roots | Where-Object { $_ })) {
        foreach ($name in @("Inno Setup 7", "Inno Setup 6")) {
            $path = Join-Path (Join-Path $root $name) "ISCC.exe"
            if (Test-Path -LiteralPath $path) { return $path }
        }
    }
    return $null
}

function Get-Version($Path, $Pattern) {
    $match = Select-String -LiteralPath $Path -Pattern $Pattern | Select-Object -First 1
    if (-not $match) { throw "Could not read the version from $Path" }
    return $match.Matches[0].Groups[1].Value
}

# Check everything up front so a missing tool fails in seconds, not after
# several minutes of PyInstaller.
Write-Host "==> Checking build tools"
$setupVersion = Get-Version "installer.iss" '#define MyAppVersion "([^"]+)"'
$appVersion = Get-Version (Join-Path (Join-Path "app" "services") "updater.py") '^APP_VERSION = "([^"]+)"'
if ($setupVersion -ne $appVersion) {
    throw "Version mismatch: installer.iss is $setupVersion but app/services/updater.py is $appVersion."
}

python -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed. Run: python -m pip install pyinstaller"
}

$iscc = Find-Iscc
if (-not $iscc) {
    throw "Inno Setup (ISCC.exe) was not found. Install Inno Setup 6.7+ from https://jrsoftware.org/isdl.php, or set `$env:ISCC to the full path of ISCC.exe."
}
Write-Host "    version $setupVersion, Inno Setup: $iscc"

Write-Host "==> Building server utilities and GUI"
& (Join-Path $PSScriptRoot "build_all.ps1")

Write-Host "==> Building installer"
& $iscc "installer.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed (exit code $LASTEXITCODE)." }

$setup = Join-Path (Join-Path $PSScriptRoot "installer") "FlowBench-Setup-$setupVersion.exe"
if (-not (Test-Path -LiteralPath $setup)) { throw "Inno Setup finished but $setup is missing." }
$item = Get-Item -LiteralPath $setup
if ($item.LastWriteTime -lt $started) { throw "$setup was not rebuilt by this run." }
$sizeMb = [math]::Round($item.Length / 1MB, 1)
Write-Host ""
Write-Host "Installer ready: $setup ($sizeMb MB)"
