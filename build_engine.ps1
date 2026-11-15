# Build the native FlowBench load engine (Rust) and stage it for bundling.
# Requires the Rust toolchain (https://rustup.rs). The agent build calls this
# only when cargo is present; without it FlowBench falls back to the Python
# worker, so this script is optional.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "cargo not found. Install the Rust toolchain from https://rustup.rs"
}

cargo build --release --manifest-path (Join-Path $PSScriptRoot "engine\Cargo.toml")
if ($LASTEXITCODE -ne 0) { throw "cargo build failed (exit code $LASTEXITCODE)." }

$src = Join-Path $PSScriptRoot "engine\target\release\flowbench-engine.exe"
$dst = Join-Path $PSScriptRoot "flowbench-engine.exe"
Copy-Item -LiteralPath $src -Destination $dst -Force
Write-Host "Native engine staged at $dst"
