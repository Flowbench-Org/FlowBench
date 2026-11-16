#!/usr/bin/env bash
# Build the native FlowBench load engine (Rust) on Linux.
#
# The binary is left at engine/target/release/flowbench-engine, where both
# FlowBench.spec (which embeds it under agent_template/linux) and the node-bundle
# generator pick it up, so generated Linux node bundles ship it. Build it before
# building the FlowBench desktop app if you want Linux nodes to use it.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v cargo >/dev/null 2>&1; then
    echo "cargo not found; install the Rust toolchain from https://rustup.rs" >&2
    exit 1
fi

# An explicit --target-dir keeps the binary where the specs look for it even
# when CARGO_TARGET_DIR or build.target-dir points elsewhere. Remove the old
# binary first, so a failed build cannot leave a stale one to be bundled.
rm -f engine/target/release/flowbench-engine
cargo build --release --manifest-path engine/Cargo.toml --target-dir engine/target
echo "Native engine built at engine/target/release/flowbench-engine"
