# FlowBench native engine protocol

FlowBench can run a stress job either with the built-in Python worker
(`agent/worker.py`) or with a native Rust engine (`engine/`, binary
`flowbench-engine`). The agent picks the engine, spawns it as a child process
and talks to it over **stdin/stdout with newline-delimited JSON** (one JSON
object per line, UTF-8).

The split of responsibility is deliberate:

- **Python keeps every gate.** Authorization allow-list, protocol allow-list
  and per-agent limits are enforced by `validate_job` *before* a job is handed
  to the engine. The engine never sees an unvalidated job and has no allow-list
  of its own to bypass.
- **Rust runs the hot path.** Connection pool, the rate-limited send loop and
  per-request statistics.

## Lifecycle

1. The agent spawns `flowbench-engine` with pipes on stdin/stdout.
2. The engine immediately writes one `hello` line.
3. The agent writes one `start` command with the validated job config.
4. The engine streams `snapshot` lines (~every 0.5 s) and finishes with one
   `report` line, then exits.
5. To stop early the agent writes a `stop` line. **Closing stdin also stops the
   job** — if the controlling agent dies, the engine must not keep running, so
   the agent keeps stdin open for the whole job and closes it only to stop.

The engine runs exactly one job per process (mirroring one `HeadlessStressJob`
per job on the Python side).

## Commands (agent -> engine, stdin)

```json
{"type":"start","job_id":"<optional>","config":{ ...validated job... }}
{"type":"stop"}
```

`config` fields (all already validated and clamped by `validate_job`):

| field         | type   | notes                                        |
|---------------|--------|----------------------------------------------|
| `target`      | string | host, required                               |
| `protocol`    | string | `HTTP` \| `HTTPS` \| `TCP`                    |
| `url`         | string | HTTP/HTTPS only                              |
| `port`        | int    | 1..65535                                     |
| `rate`        | number | requests/sec (token bucket)                  |
| `duration`    | number | seconds                                      |
| `threads`     | int    | max concurrent requests                      |
| `packet_size` | int    | TCP payload bytes (HTTP tx is estimated from headers) |
| `timeout`     | int    | per-request timeout, ms                      |
| `headers`     | object | optional HTTP headers                        |

## Events (engine -> agent, stdout)

```json
{"type":"hello","engine":"flowbench-engine","version":"0.1.0","protocol":1}
{"type":"snapshot","running":true,"total":..,"success":..,"fail":..,"tx":..,"qps":..,"avg":..,"active":..,"progress":..,"last_error":".."}
{"type":"report","target":"..","protocol":"..","duration":..,"total":..,"success":..,"fail":..,"avg":..,"p50":..,"p90":..,"p99":..,"traffic_mb":..,"bytes_tx":..,"rate_limit":..,"errors":{"timeout":3}}
{"type":"error","message":".."}
```

`snapshot` and `report` carry exactly the keys the Python callbacks
(`AgentRuntime._on_snapshot` / `_on_report`) already consume, so the native
engine drops into the same aggregator. A few semantics still differ from the
Python worker: `active` is the number of in-flight requests (not live threads),
redirects are not followed and no proxy is used, and on the desktop TCP stays on
the Python engine (its per-thread connection reuse avoids port exhaustion at high
QPS). `protocol` in `hello` is the version of this contract; the bridge refuses an
engine whose number it does not know.

## Choosing the engine

`agent.json` (or `DEFAULTS`) key `engine`:

- `auto` (default) — use the native engine if its binary is found, else the
  Python worker.
- `rust` — require the native engine; fail the job if it is missing.
- `python` — always use the built-in Python worker.

The binary is located, in order: `FLOWBENCH_ENGINE` env var, the `engine_path`
config key, next to the (frozen) executable, the PyInstaller bundle dir, then
`engine/target/release/` or the repo root in a source checkout. Relative
`FLOWBENCH_ENGINE`/`engine_path` values are resolved to absolute paths. The
desktop app reads the same `engine` mode from its settings (`engine` in
`settings.json`, default `auto`) and uses the native engine only for HTTP/HTTPS.

## Shipping the engine to nodes

The engine is a compiled binary, so it is per-OS/arch:

- **Windows nodes** — build with `build_engine.ps1` (needs the Rust toolchain).
  `build_agent.ps1` runs it when `cargo` is present and bundles the binary inside
  `FlowBench-Agent.exe`, so the generated Windows node package already has it.
- **Linux nodes** — build with `build_engine.sh` on a Linux machine (or
  cross-compile) *before* building the FlowBench desktop app. The binary at
  `engine/target/release/flowbench-engine` is embedded by `FlowBench.spec`
  (`agent_template/linux`), and the node-bundle generator ships it beside
  `server_agent.py` in every generated Linux bundle. Set `FLOWBENCH_LINUX_ENGINE`
  to override the path.

A node whose CPU/libc do not match the binary simply falls back to the Python
worker (in the default `auto` mode), so a mismatched or missing binary is never
fatal. Build the Linux binary on the oldest glibc you need to support.
