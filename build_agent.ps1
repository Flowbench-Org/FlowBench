# Build additive server-side utilities for FlowBench.
$ErrorActionPreference = "Stop"

# Agent is onedir because Qt's native DLL/plugin layout is more reliable for a
# long-running server process. Copy the entire dist\FlowBench-Agent directory.
Set-Location -LiteralPath $PSScriptRoot
foreach ($spec in @("Agent.spec", "Hub.spec", "AgentCtl.spec")) {
    python -m PyInstaller --clean --noconfirm $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $spec (exit code $LASTEXITCODE)." }
}

Write-Host "Built server utilities under dist/"
