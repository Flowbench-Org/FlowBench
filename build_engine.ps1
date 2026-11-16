# Build the native FlowBench load engine (Rust) and stage it for bundling.
# Requires the Rust toolchain (https://rustup.rs). The agent build calls this
# only when cargo is present; without it FlowBench falls back to the Python
# worker, so this script is optional.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    throw "cargo not found. Install the Rust toolchain from https://rustup.rs"
}

$targetDir = Join-Path $PSScriptRoot "engine\target"
$src = Join-Path $targetDir "release\flowbench-engine.exe"
$dst = Join-Path $PSScriptRoot "flowbench-engine.exe"

# Remove the binaries of an earlier build first: if this build fails, the specs
# (which bundle the staged copy, else the cargo output) must find nothing stale.
foreach ($old in @($dst, $src)) {
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Force }
}

# An explicit --target-dir keeps the output where this script and the specs
# look for it even when CARGO_TARGET_DIR or build.target-dir points elsewhere.
cargo build --release --manifest-path (Join-Path $PSScriptRoot "engine\Cargo.toml") --target-dir $targetDir
if ($LASTEXITCODE -ne 0) { throw "cargo build failed (exit code $LASTEXITCODE)." }

Copy-Item -LiteralPath $src -Destination $dst -Force
Write-Host "Native engine staged at $dst"
