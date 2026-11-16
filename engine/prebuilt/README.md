# Prebuilt native engine for Linux nodes

`linux-x86_64/flowbench-engine` is the native engine built for x86-64 Linux,
against **glibc 2.17**, so it runs on CentOS 7, Ubuntu 14.04, Debian 8 and every
later x86-64 distribution. Generated Linux node bundles ship it beside
`server_agent.py`; on a node it cannot run on (ARM, musl), the agent falls back
to the Python worker.

It is here because the desktop app is built on Windows, which cannot easily
build a Linux binary. `FlowBench.spec` and the node-bundle generator prefer, in
order: `FLOWBENCH_LINUX_ENGINE`, a fresh `engine/target/release/flowbench-engine`
(from `build_engine.sh` on Linux), then this file.

`SOURCE.sha256` records the engine sources it was built from
(`tools/engine_source_hash.py`). `tests/test_engine_prebuilt.py` fails when the
sources change and this binary is not rebuilt, so a stale engine is never
shipped.

## Rebuilding

On a Linux machine with Rust and [cargo-zigbuild](https://github.com/rust-cross/cargo-zigbuild)
(it links against the old glibc, whatever the build machine has):

```
cd engine
cargo zigbuild --release --locked --target x86_64-unknown-linux-gnu.2.17
cp target/x86_64-unknown-linux-gnu/release/flowbench-engine prebuilt/linux-x86_64/
cd ..
python tools/engine_source_hash.py --write
```

Check the glibc floor with
`objdump -T engine/prebuilt/linux-x86_64/flowbench-engine | grep -o 'GLIBC_[0-9.]*' | sort -uV | tail -1`
(it must print `GLIBC_2.17` or lower).
