# Build all distributables in dependency order.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
& (Join-Path $PSScriptRoot "build_agent.ps1")
python -m PyInstaller --clean --noconfirm FlowBench.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for FlowBench.spec (exit code $LASTEXITCODE)." }
Write-Host "Main GUI and server utilities built under dist/"
