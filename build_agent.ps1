# Build additive server-side utilities for FlowBench.
$ErrorActionPreference = "Stop"

# Agent is onedir because Qt's native DLL/plugin layout is more reliable for a
# long-running server process. Copy the entire dist\FlowBench-Agent directory.
Set-Location -LiteralPath $PSScriptRoot

# Build the native engine first when the Rust toolchain is available; otherwise
# the agent still builds and uses the Python worker at runtime.
if (Get-Command cargo -ErrorAction SilentlyContinue) {
    & (Join-Path $PSScriptRoot "build_engine.ps1")
} else {
    Write-Warning "cargo not found; building without the native engine (Python worker will be used)."
}
foreach ($spec in @("Agent.spec", "Hub.spec", "AgentCtl.spec")) {
    python -m PyInstaller --clean --noconfirm $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $spec (exit code $LASTEXITCODE)." }
}

Write-Host "Built server utilities under dist/"
