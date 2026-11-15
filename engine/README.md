# flowbench-engine

Native (Rust) load-generation engine for FlowBench. It is a standalone process
the Python agent drives over stdin/stdout; the wire protocol is documented in
[`../ENGINE.md`](../ENGINE.md).

## What it is and is not

- It **only** executes a job that the Python side has already validated:
  authorization allow-list, protocol allow-list and all per-agent limits
  (`validate_job` in `server_agent.py`) run **before** a job reaches the engine.
- The engine refuses to run without a concrete `target`, and stops the moment
  stdin closes (the controlling parent is gone) so it never keeps generating
  load on its own.

## Build

Needs a stable Rust toolchain (<https://rustup.rs>). Build on the same OS you
ship for; for the Windows release just build on Windows:

```
cargo build --release --manifest-path engine/Cargo.toml
```

The binary lands at `engine/target/release/flowbench-engine` (`.exe` on
Windows). `build_engine.ps1` wraps this and copies the binary to the repo root
as `flowbench-engine.exe` so PyInstaller can bundle it.

## Run standalone (for debugging)

```
# Keep stdin open for the job's duration; an immediate EOF stops it (EOF == stop).
(echo '{"type":"start","config":{"target":"localhost","protocol":"HTTP","url":"http://localhost:8000/","port":80,"rate":5,"duration":3,"threads":2,"packet_size":64,"timeout":2000}}'; sleep 4) | ./target/release/flowbench-engine
```

It prints a `hello` line, then `snapshot` lines every 0.5 s, then one `report`.
