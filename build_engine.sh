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

cargo build --release --manifest-path engine/Cargo.toml
echo "Native engine built at engine/target/release/flowbench-engine"
