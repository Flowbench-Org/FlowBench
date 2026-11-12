# FlowBench Server Agent

This is a **new server node deployment method** that does not modify the existing desktop GUI. The server runs `server_agent.py` (or the packaged `FlowBench-Agent.exe`), which connects to the Hub via outbound HTTPS polling; local operators use `agentctl.py` to send start/stop commands to a specified node.

```text
Local Operator / Console
        │ HTTPS (control commands, status)
        ▼
FlowBench Hub (self-hosted)
        ▲
        │ Outbound HTTPS (server does not need inbound ports open)
        │
FlowBench-Agent.exe (your server) ───► Authorized test targets
```

## Why this approach

- The existing `FlowBench.exe`, GUI, stress-test page, and collaboration page remain fully intact.
- The Agent is an independent process; copy it to the server and run it, no need to move the desktop GUI to the server.
- The server actively establishes a connection to the Hub, no need to open management ports on the server.
- The Agent's target whitelist, protocols, rate, duration, thread count, and packet size are all capped by local configuration on the server.
- One Agent accepts only one task at a time; by default only HTTP / HTTPS / TCP remote tasks are enabled.

> Only for use against test targets that you own or have written authorization for. The Hub/Agent does not provide open forwarding capabilities to arbitrary public targets.

## One‑click generation (default for open‑source users)

The generation dialog offers two control transports:

- **Direct Hub** keeps the existing HTTPS Hub workflow. Use an existing Hub, or generate an all-in-one Hub + Agent package and expose TCP 8787.
- **Public MQTT relay** connects the desktop controller and server Agent through `broker.hivemq.com`. The server needs outbound internet access only; no inbound control port is opened. The generated relay URL and controller token are saved back to the Server Nodes page so additional nodes can join the same private relay channel.

If you have not yet deployed a Hub, leave the "Hub address" and "control token" fields blank, then click "Generate Server Node":

1. Enter a node name.
2. Enter the public IP or domain name of this server.
3. Save the ZIP.

The GUI will automatically generate:

- `FlowBench-Hub`
- `FlowBench-Agent`
- A random control token
- A random node token
- A random self‑signed HTTPS certificate
- Windows one‑click startup script
- Linux one‑click startup script
- Pre‑configured `hub.json` and `agent.json`

After extracting on a Windows server, go to the `windows` folder, run `start-all.cmd` as Administrator, and allow TCP `8787`. On a Linux server, extract, go to the `linux` folder, run `./start-all.sh`, and allow TCP `8787`. After startup, return to the GUI and click "Refresh Nodes".

When generating the ZIP, a file `FlowBench-<node name>.ca.pem` is also created next to it. This file is used by the GUI to trust the one‑click Hub certificate; do not delete or rename it.

## 0. Generate the node package using the GUI (recommended)

Prerequisite: first deploy and start `FlowBench-Hub`, and the local GUI must be able to access its HTTPS address.

1. In the local FlowBench "Server Nodes" page, fill in the Hub address and control token.
2. Click "Generate Server Node".
3. Enter a node name and save the ZIP.
4. Copy the ZIP to the server and extract:
   - Windows Server: go to `windows` and double‑click `start-agent.cmd`.
   - Linux: go to `linux`, install `requests` first, then run `./start-agent.sh`.
5. Return to the local GUI, click "Refresh Nodes", and the node will appear online.

The generated ZIP already includes the node‑specific token, Hub address, Windows executable, and Linux source code; no need to manually edit `agent.json`. The target whitelist can be empty at initial generation; before each task is started from the GUI, the control side will synchronise the targets already authorised locally.

## 1. Generate random tokens

Generate tokens in PowerShell:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32)); print(secrets.token_urlsafe(32))"
```

The first line is for the Hub's `controller_token`, the second for the Agent's `token`. Do not commit tokens to Git or post them in chat groups.

## 2. Configure the Hub

Copy `agent/hub.example.json` to `hub.json`, and fill in the same Agent ID/token pair and the controller token:

```json
{
  "controller_token": "random value at least 16 characters",
  "agents": [
    {
      "agent_id": "server-01",
      "name": "My Server",
      "token": "random value at least 16 characters"
    }
  ]
}
```

Start in development:

```powershell
python agent/hub.py --config hub.json --host 127.0.0.1 --port 8787
```

For production, put the Hub behind your own HTTPS reverse proxy (e.g. Nginx/Caddy) and let the Agent use the `https://...` address. Do not expose the built‑in HTTP listener directly to the public internet.

## 3. Configure the server Agent

Copy `agent/agent.example.json` to `agent.json` on the server:

```json
{
  "hub_url": "https://control.example.com",
  "agent_id": "server-01",
  "token": "same as in hub.json for server-01",
  "allowed_targets": ["your-authorized-test-host.example"],
  "allowed_protocols": ["HTTP", "HTTPS", "TCP"],
  "max_rate": 100,
  "max_duration": 300,
  "max_threads": 32,
  "max_packet_size": 65536,
  "poll_interval": 2,
  "heartbeat_interval": 10
}
```

`allowed_targets` must be exact hostnames/IPs. Tasks with targets not in this list will be rejected by the Agent. Remote tasks are also subject to all caps set in the Agent configuration.

Test‑run on the server:

```powershell
python server_agent.py --config agent.json
```

Seeing `online as server-01` means the Agent is online. The server only needs outbound HTTPS access to the Hub; no inbound ports need to be opened for the Agent.

### Limits

`max_rate` (requests per second), `max_duration` (seconds per job), `max_threads` and `max_packet_size` (bytes) are the most a job may ask for, and `allowed_protocols` the protocols it may use. The Agent refuses anything above them and says by how much, for example `command rejected: rate exceeds agent limit: 500 > 100`. When it starts, it stops with a clear message if one of the limits is not a positive number.

The **Generate Server Node** dialog sets the rate, duration and thread limits (100 QPS, 300 s and 32 threads unless you change them); the bundle's `README.txt` repeats them. To change them later, edit `agent.json` on the server and restart the Agent.

The Agent reports its limits to the desktop when it comes online. Before **Start on server**, the desktop checks the job from the Stress page against each selected server: if the job is over a server's limits, it lists what is over and offers to run it within each server's limits (each server gets the job lowered to its own limits) or to cancel. Servers that do not support the job's protocol are skipped. An Agent from an older FlowBench does not report its limits and is sent the job as it is. When a server refuses a job, the Server Agents page shows why next to the server and in its log.

### System metrics

The Agent samples its server's CPU usage, memory, network throughput (loopback left out), TCP connections and processes once a second and sends the last 15 seconds with every heartbeat. While metrics are on, a heartbeat goes out every `metrics_interval` seconds (default `2`, from 1 to 60). Add `"metrics": false` to turn them off and go back to `heartbeat_interval`.

On the desktop, open **Monitor** and choose the server under **Source**. On Linux the Agent reads `/proc` and needs no extra package; inside a container, CPU and memory are the host's figures unless the container has its own `/proc` (for example with LXCFS). The Windows `FlowBench-Agent.exe` uses psutil, which is bundled. An Agent from an older FlowBench does not send metrics: replace its files (or the exe) with a newly generated node bundle and restart it.

## 4. View and send tasks from the control side

Copy `agent/controller.example.json` to `controller.json`, filling in the Hub address and `controller_token`.

List nodes:

```powershell
python agentctl.py --config controller.json list
```

Copy `agent/job.example.json` to `job.json`, fill in an authorised test host, then send a task:

```powershell
python agentctl.py --config controller.json run server-01 job.json
```

Stop a task:

```powershell
python agentctl.py --config controller.json stop server-01
```

## 5. Package the client

On the development machine, run:

```powershell
.\build_agent.ps1
```

The output is `dist\FlowBench-Agent.exe`. The Agent is a cross‑platform pure‑Python network process that does not depend on Qt or a desktop environment; for Windows, copy the exe directly; for Linux, use the same `server_agent.py` (or repackage with PyInstaller on Linux) and `agent.json`. `hub.py` and `agentctl.py` are also built as standalone exes; see `Agent.spec`, `Hub.spec`, and `AgentCtl.spec`.

## 6. Autostart on server boot

On Windows Server, use Task Scheduler:

- Trigger: At system startup
- Action: `FlowBench-Agent.exe --config C:\FlowBench\agent.json`
- Check "Run whether user is logged on or not"
- Restart on failure every 1 minute
- Run under a dedicated low‑privilege account

Linux servers cannot run the Windows exe, but can run `server_agent.py` directly: install Python 3.10+ and `requests` (`pip install requests`), then execute `python3 server_agent.py --config agent.json`. Alternatively, on Linux you can run `pyinstaller Agent.spec` to produce a Linux binary.

## Docker / Railway notes

The generated Linux bundle is now safe for container startup. It starts Hub first, polls `https://127.0.0.1:8787/health`, and only starts Agent after the health check succeeds.

With TCP proxies (Railway, ngrok, frp, etc.), remember there are two URLs:

```text
Public Hub URL for GUI / remote agents: https://proxy.example.com:23915
Internal Hub bind port on the server/container: 8787
```

The GUI should use the public URL. A co‑located Agent should use `https://127.0.0.1:8787`; only a remote Agent should use the public proxy URL. The current one‑click bundle generates the internal bind port from the address you enter, so enter the public proxy address only if the Agent will connect through the proxy. For a co‑located Agent with a proxy, edit `agent/agent.json` after generation.

Build an extracted bundle:

```bash
cp deploy/docker/Dockerfile /path/to/extracted/linux/
docker build -t flowbench-node /path/to/extracted/linux/
docker run --rm -p 8787:8787 flowbench-node
```

The Hub listens on `0.0.0.0:8787`.

For Railway, prefer the official HTTPS proxy and run Hub in plain HTTP mode:

```bash
python3 hub/hub.py --config hub/hub.json --host 0.0.0.0 --port 8787
```

In that mode the local Agent can use `http://127.0.0.1:8787`, while the local GUI should use the public `https://...` URL provided by Railway. Do not put a self‑signed HTTPS backend behind Railway's ordinary HTTP reverse proxy.
