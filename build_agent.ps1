# Build additive server-side utilities for FlowBench.
$ErrorActionPreference = "Stop"

# Agent is onedir because Qt's native DLL/plugin layout is more reliable for a
# long-running server process. Copy the entire dist\FlowBench-Agent directory.
Set-Location -LiteralPath $PSScriptRoot

# Build the native engine first when the Rust toolchain is available; otherwise
# the agent still builds and uses the Python worker at runtime.
if (Get-Command cargo -ErrorAction SilentlyContinue) {
    try {
        & (Join-Path $PSScriptRoot "build_engine.ps1")
    } catch {
        Write-Warning "Native engine build failed ($_); building the agent with the Python worker."
    }
} else {
    # Agent.spec bundles an engine left by an earlier build; say so instead of
    # promising the Python worker.
    $prebuilt = @(@("flowbench-engine.exe", "engine\target\release\flowbench-engine.exe") |
        ForEach-Object { Join-Path $PSScriptRoot $_ } |
        Where-Object { Test-Path -LiteralPath $_ })
    if ($prebuilt.Count -gt 0) {
        Write-Warning "cargo not found; bundling the previously built engine $($prebuilt[0]), which may be out of date."
    } else {
        Write-Warning "cargo not found; building without the native engine (Python worker will be used)."
    }
}
foreach ($spec in @("Agent.spec", "Hub.spec", "AgentCtl.spec")) {
    python -m PyInstaller --clean --noconfirm $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $spec (exit code $LASTEXITCODE)." }
}

Write-Host "Built server utilities under dist/"
