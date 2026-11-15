"""Headless FlowBench server agent.

The agent is a separate deployment target and does not alter the existing GUI.
It works on Windows Server and Linux because it has no Qt/desktop dependency.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import queue
import socket
import threading
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import ssl

from agent.relay import (parse_relay_url, relay_topic, sign_message,
                         verify_message)
from agent.sysmetrics import SystemSampler
from agent.transport import compact_json, decode_json, endpoint, number_text
from agent.engine_bridge import make_stress_job

DEFAULTS = {
    "transport": "http",
    "engine": "auto",  # auto | rust | python (native load engine, see ENGINE.md)
    "poll_interval": 2.0, "heartbeat_interval": 10.0, "job_heartbeat_interval": 2.0, "request_timeout": 8.0,
    "max_rate": 100.0, "max_duration": 300, "max_threads": 32,
    "max_packet_size": 64 * 1024, "allowed_protocols": ["HTTP", "HTTPS", "TCP"],
    # CPU, memory, network, TCP connections and processes for the desktop's
    # Monitor page; sampled every second and sent at least this often.
    "metrics": True, "metrics_interval": 2.0,
}
SUPPORTED_PROTOCOLS = {"HTTP", "HTTPS", "TCP"}
# The most a job may ask for; the desktop reads them from the agent's
# metadata and checks a job before sending it.
LIMIT_TYPES = {"max_rate": float, "max_duration": int, "max_threads": int, "max_packet_size": int}
# Seconds; metrics_interval has its own range.
INTERVALS = ("poll_interval", "heartbeat_interval", "job_heartbeat_interval", "request_timeout")


class AgentHTTPError(RuntimeError):
    pass


class HubClient:
    def __init__(self, config, config_path=None):
        self.root = config["hub_url"]
        self.agent_id = config["agent_id"]
        self.token = config["token"]
        self.timeout = float(config["request_timeout"])
        self.ssl_context = None
        raw_ca = str(config.get("ca_file") or "").strip()
        if raw_ca:
            ca_path = Path(raw_ca)
            if not ca_path.is_absolute() and config_path is not None:
                ca_path = Path(config_path).resolve().parent / ca_path
            if ca_path.is_file():
                self.ssl_context = ssl.create_default_context(cafile=str(ca_path.resolve()))
            elif bool(config.get("insecure_control_tls", False)):
                self.ssl_context = ssl._create_unverified_context()
            else:
                raise ValueError(f"control CA file not found: {ca_path}")
        # Self-signed control-plane certificates are accepted only through an
        # explicit opt-in in the generated bundle. Never disable verification
        # for arbitrary targets; test traffic has its own TLS handling.
        if bool(config.get("insecure_control_tls", False)) and self.ssl_context is None:
            self.ssl_context = ssl._create_unverified_context()

    def _request(self, method, path, payload=None, auth=True):
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = compact_json(payload)
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        req = Request(endpoint(self.root, path), data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout, context=self.ssl_context) as response:
                data = decode_json(response.read())
                if response.status >= 400:
                    raise AgentHTTPError(data.get("error", f"HTTP {response.status}"))
                return data
        except HTTPError as exc:
            try:
                detail = decode_json(exc.read()).get("error", str(exc))
            except Exception:
                detail = str(exc)
            raise AgentHTTPError(detail) from exc
        except URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise AgentHTTPError(f"{reason} (hub={self.root})") from exc

    def register(self, metadata):
        return self._request("POST", "/v1/agents/register", {
            "agent_id": self.agent_id, "token": self.token, "metadata": metadata,
        }, auth=False)

    def heartbeat(self, event):
        return self._request("POST", f"/v1/agents/{self.agent_id}/heartbeat", event)

    def poll(self, after):
        return self._request("GET", f"/v1/agents/{self.agent_id}/commands?after={int(after)}")

    def event(self, payload):
        return self._request("POST", f"/v1/agents/{self.agent_id}/events", payload)


def _host_from_target(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw if "://" in raw else f"//{raw}")
    return (parsed.hostname or "").rstrip(".").lower()


def load_config(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    merged = dict(DEFAULTS)
    merged.update(data)
    transport = str(merged.get("transport") or "http").strip().lower()
    merged["transport"] = transport
    if transport == "mqtt_relay":
        for key in ("relay_url", "controller_token", "agent_id"):
            if not str(merged.get(key) or "").strip():
                raise ValueError(f"{key} is required")
        if len(str(merged["controller_token"])) < 16:
            raise ValueError("controller_token must be at least 16 characters")
        merged["relay"] = parse_relay_url(str(merged["relay_url"]))
        merged["name"] = str(merged.get("name") or merged["agent_id"]).strip()[:64]
    elif transport == "http":
        for key in ("hub_url", "agent_id", "token"):
            if not str(merged.get(key) or "").strip():
                raise ValueError(f"{key} is required")
        if len(str(merged["token"])) < 16:
            raise ValueError("token must be at least 16 characters")
        hub = urlsplit(str(merged["hub_url"]))
        if hub.scheme not in {"http", "https"} or not hub.netloc:
            raise ValueError("hub_url must be an http(s) URL")
        try:
            hub_port = hub.port
        except ValueError as exc:
            raise ValueError("hub_url port is invalid") from exc
        if hub_port is not None and not 1 <= hub_port <= 65535:
            raise ValueError("hub_url port must be between 1 and 65535")
        if hub.scheme == "http" and (hub.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("hub_url must use HTTPS unless it points to localhost")
    else:
        raise ValueError("transport must be http or mqtt_relay")
    # An empty list is valid for a newly provisioned node. The controller
    # sends a signed-by-token sync_targets command before the first job.
    raw_targets = merged.get("allowed_targets", [])
    if not isinstance(raw_targets, (list, tuple, set)):
        raw_targets = []
    merged["allowed_targets"] = sorted({
        _host_from_target(x) for x in raw_targets if _host_from_target(x)
    })
    raw_protocols = merged.get("allowed_protocols", DEFAULTS["allowed_protocols"])
    if not isinstance(raw_protocols, (list, tuple, set)):
        raw_protocols = DEFAULTS["allowed_protocols"]
    merged["allowed_protocols"] = [str(x).upper() for x in raw_protocols]
    engine_mode = str(merged.get("engine") or "auto").strip().lower()
    if engine_mode not in {"auto", "rust", "python"}:
        raise ValueError("engine must be auto, rust or python")
    merged["engine"] = engine_mode
    for key, kind in LIMIT_TYPES.items():
        value = merged.get(key)
        try:
            if isinstance(value, bool):
                raise TypeError
            number = kind(value)
            usable = math.isfinite(number) and number > 0   # a huge int overflows here
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{key} must be a positive number") from exc
        if not usable:
            raise ValueError(f"{key} must be a positive number")
        merged[key] = number
    # Checked here so a typo such as "8s" stops the agent at start with this
    # message, instead of a traceback or "control-plane retry" over and over.
    for key in INTERVALS:
        value = merged.get(key)
        try:
            if isinstance(value, bool):
                raise TypeError
            seconds = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{key} must be a number of seconds") from exc
        if not (math.isfinite(seconds) and 0 < seconds <= 3600):
            raise ValueError(f"{key} must be more than 0 and at most 3600 seconds")
        merged[key] = seconds
    merged["metrics"] = merged.get("metrics", True) is not False
    try:
        metrics_interval = float(merged.get("metrics_interval", DEFAULTS["metrics_interval"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("metrics_interval must be a number of seconds") from exc
    if not (math.isfinite(metrics_interval) and 1 <= metrics_interval <= 60):
        raise ValueError("metrics_interval must be between 1 and 60 seconds")
    merged["metrics_interval"] = metrics_interval
    return merged


def heartbeat_interval(config, event, metrics_on=False):
    """Seconds between heartbeats: faster during a job and while sending metrics."""
    interval = float(config.get("heartbeat_interval", 10))
    if str((event or {}).get("state")) in {"starting", "running", "stopping"}:
        interval = float(config.get("job_heartbeat_interval", 2))
    if metrics_on:
        interval = min(interval, float(config.get("metrics_interval", 2)))
    return interval


def start_sampler(config):
    """The system-metrics sampler for this agent, or None when metrics are off."""
    if not config.get("metrics", True):
        return None
    return SystemSampler.create().start()


def with_system(event, sampler):
    """The heartbeat event plus the latest system samples.

    The samples ride inside the event so an older Hub, which stores whatever
    the heartbeat carries, passes them on to the desktop unchanged.
    """
    payload = dict(event or {})
    if sampler is not None:
        payload["system"] = sampler.snapshot()
    return payload


def relay_status_message(secret, agent_id, name, agent_metadata, online, event):
    """A signed status message for the MQTT relay (retained, one per agent)."""
    return sign_message(secret, {
        "type": "status",
        "agent_id": agent_id,
        "name": name,
        "metadata": agent_metadata,
        "online": bool(online),
        "last_seen": time.time(),
        "last_event": dict(event or {}),
    })


def validate_job(raw: dict, config: dict):
    if not isinstance(raw, dict):
        raise ValueError("job config must be an object")
    job = dict(raw)
    target = _host_from_target(job.get("target") or job.get("url"))
    if not target or target not in config["allowed_targets"]:
        raise ValueError(f"target is not in the agent allow-list: {target or '<empty>'}")
    proto = str(job.get("protocol", "HTTP")).upper()
    if proto not in SUPPORTED_PROTOCOLS or proto not in config["allowed_protocols"]:
        raise ValueError(f"protocol is not allowed: {proto}")
    try:
        raw_values = {
            "rate": job.get("rate", 1),
            "duration": job.get("duration", 1),
            "threads": job.get("threads", 1),
            "packet_size": job.get("packet_size", 64),
            "port": job.get("port", 443 if proto == "HTTPS" else 80),
            "timeout": job.get("timeout", 5000),
        }
        if any(isinstance(value, bool) for value in raw_values.values()):
            raise ValueError("boolean numeric job fields are invalid")
        rate = float(raw_values["rate"]); duration = int(raw_values["duration"])
        threads = int(raw_values["threads"]); packet_size = int(raw_values["packet_size"])
        port = int(raw_values["port"]); timeout = int(raw_values["timeout"])
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric job fields are invalid") from exc
    if not math.isfinite(rate): raise ValueError("rate must be finite")
    # The messages start the same as before, so older desktops still read them.
    for name, value in (("rate", rate), ("duration", duration), ("threads", threads),
                        ("packet_size", packet_size)):
        if value <= 0:
            raise ValueError(f"{name} must be positive")
    if rate > float(config["max_rate"]):
        raise ValueError(f"rate exceeds agent limit: {number_text(rate)} > {number_text(config['max_rate'])}")
    if duration > int(config["max_duration"]):
        raise ValueError(f"duration exceeds agent limit: {duration} > {int(config['max_duration'])}")
    if threads > int(config["max_threads"]):
        raise ValueError(f"threads exceed agent limit: {threads} > {int(config['max_threads'])}")
    if packet_size > int(config["max_packet_size"]):
        raise ValueError(f"packet_size exceeds agent limit: {packet_size} > {int(config['max_packet_size'])}")
    if not (1 <= port <= 65535): raise ValueError("port must be between 1 and 65535")
    if not (100 <= timeout <= 60000): raise ValueError("timeout must be between 100 and 60000 ms")
    if proto in {"HTTP", "HTTPS"}:
        url = str(job.get("url") or "").strip()
        if not url:
            scheme = "https" if proto == "HTTPS" else "http"
            default_port = 443 if proto == "HTTPS" else 80
            url = f"{scheme}://{target}{'' if port == default_port else ':' + str(port)}"
        parsed = urlsplit(url)
        try:
            explicit_port = parsed.port
        except ValueError as exc:
            raise ValueError("HTTP URL port is invalid") from exc
        expected_scheme = "https" if proto == "HTTPS" else "http"
        if (parsed.scheme.lower() not in {"http", "https"}
                or parsed.scheme.lower() != expected_scheme
                or _host_from_target(url) != target
                or (explicit_port is not None and not 1 <= explicit_port <= 65535)):
            raise ValueError("HTTP URL scheme, host, or port is invalid")
        job["url"] = url
    job.update({"target": target, "protocol": proto, "rate": rate, "duration": duration,
                "threads": threads, "packet_size": packet_size, "port": port, "timeout": timeout})
    return job


class AgentRuntime:
    def __init__(self, config):
        self.config = config
        self.job = None
        self.job_id = None
        self.latest = {"state": "idle"}
        self.lock = threading.RLock()

    def current_event(self):
        with self.lock:
            return dict(self.latest)

    def _set_event(self, value):
        with self.lock:
            self.latest = dict(value)

    def _on_snapshot(self, data):
        event = dict(data or {})
        event.update({"state": "running", "job_id": self.job_id, "ts": time.time()})
        self._set_event(event)

    def _on_report(self, data):
        event = dict(data or {})
        event.update({"state": "completed", "job_id": self.job_id, "ts": time.time()})
        with self.lock:
            self.latest = event
            self.job = None
            self.job_id = None
        print("[agent] job completed")

    def handle(self, command):
        ctype = command.get("type") if isinstance(command, dict) else None
        if ctype == "stop":
            if self.job and self.job.running:
                self.job.stop()
                self._set_event({"state": "stopping", "job_id": self.job_id, "ts": time.time()})
            return
        if ctype == "sync_targets":
            targets = sorted({_host_from_target(x) for x in command.get("targets", []) if _host_from_target(x)})
            if not targets:
                raise ValueError("target sync is empty; authorize a target in the desktop app first")
            self.config["allowed_targets"] = targets
            self._set_event({"state": "ready", "allowed_targets": targets, "ts": time.time()})
            return
        if ctype != "start":
            raise ValueError("unsupported command")
        if self.job and self.job.running:
            raise ValueError("an authorized job is already running")
        job = validate_job(command.get("config") or {}, self.config)
        self.job_id = str(command.get("job_id") or uuid.uuid4().hex)
        self._set_event({"state": "starting", "job_id": self.job_id, "target": job["target"], "protocol": job["protocol"], "ts": time.time()})
        self.job = make_stress_job(job, self._on_snapshot, self._on_report, self.config)
        if not self.job.start():
            self.job = None
            self.job_id = None
            raise RuntimeError("job could not start")
        print(f"[agent] job started: {self.job_id}")


def agent_limits(config) -> dict:
    """What this agent accepts, reported to the desktop with its metadata."""
    limits = {key: kind(config.get(key, DEFAULTS[key])) for key, kind in LIMIT_TYPES.items()}
    limits["protocols"] = [p for p in config.get("allowed_protocols", DEFAULTS["allowed_protocols"])
                           if p in SUPPORTED_PROTOCOLS]
    return limits


def metadata(config=None):
    data = {"hostname": socket.gethostname(), "platform": platform.platform(),
            "python": platform.python_version(), "pid": os.getpid()}
    if config is not None:
        data["limits"] = agent_limits(config)
    return data


# How far a relayed command's time may be from the agent's clock.  Older
# commands could be replays, so they are refused.
COMMAND_MAX_AGE = 300


def relay_command(secret, agent_id, payload, now=None):
    """What a relayed message asks of this agent: None to ignore it, else
    (command_id, command, off_by).  ``off_by`` is how many seconds the
    command's time is from this agent's clock when that is more than
    COMMAND_MAX_AGE (the command is then refused), else None."""
    if not verify_message(secret, payload):
        return None
    if payload.get("type") != "command" or str(payload.get("agent_id") or "") != agent_id:
        return None
    try:
        created_at = float(payload.get("created_at") or 0)
    except (TypeError, ValueError):
        return None
    command_id = str(payload.get("id") or "")
    if not command_id or not (math.isfinite(created_at) and created_at > 0):
        return None
    skew = (time.time() if now is None else now) - created_at
    return command_id, dict(payload.get("command") or {}), (skew if abs(skew) > COMMAND_MAX_AGE else None)


def run_relay_command(runtime, command, off_by=None):
    """Run a relayed command, or refuse one whose time is off (see
    relay_command) where the desktop can see why: a clock that is wrong on
    one side drops every command, which used to happen without a word."""
    if off_by is not None:
        message = f"command time is {int(round(abs(off_by)))} s off the agent's clock"
        runtime._set_event({"state": "error", "error": message, "ts": time.time()})
        print(f"[agent] command rejected: {message}", flush=True)
        return
    try:
        runtime.handle(command)
    except Exception as exc:
        runtime._set_event({"state": "error", "error": str(exc), "ts": time.time()})
        print(f"[agent] command rejected: {exc}", flush=True)


def run_relay_agent(config, config_path):
    """Run an Agent over the authenticated public MQTT relay."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise RuntimeError("paho-mqtt is required for mqtt_relay transport") from exc

    relay = dict(config["relay"])
    secret = str(config["controller_token"])
    agent_id = str(config["agent_id"])
    agent_name = str(config.get("name") or agent_id)
    status_topic = relay_topic(relay["relay_id"], "status", agent_id)
    command_topic = relay_topic(relay["relay_id"], "commands", agent_id)
    runtime = AgentRuntime(config)
    command_queue = queue.Queue()
    connected = threading.Event()
    processed_ids = set()
    processed_order = []
    agent_metadata = metadata(config)
    sampler = start_sampler(config)

    def status_message(online=True, event=None):
        event = dict(event if event is not None else runtime.current_event())
        if online:
            event = with_system(event, sampler)
        return relay_status_message(secret, agent_id, agent_name, agent_metadata, online, event)

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"flowbench-agent-{agent_id}-{uuid.uuid4().hex[:8]}",
        transport=relay["transport"],
    )
    if relay["tls"]:
        client.tls_set()
    client.reconnect_delay_set(min_delay=1, max_delay=15)
    client.will_set(
        status_topic,
        json.dumps(status_message(False, {"state": "offline"}), ensure_ascii=False, separators=(",", ":")),
        qos=1,
        retain=True,
    )

    def publish_status(event=None):
        if not connected.is_set():
            return
        client.publish(
            status_topic,
            json.dumps(status_message(True, event), ensure_ascii=False, separators=(",", ":")),
            qos=1,
            retain=True,
        )

    def on_connect(active, userdata, flags, reason_code, properties):
        if getattr(reason_code, "is_failure", False):
            connected.clear()
            return
        active.subscribe(command_topic, qos=1)
        connected.set()
        publish_status()
        print(f"[agent] online as {agent_id} (relay={relay['broker']})", flush=True)

    def on_disconnect(active, userdata, disconnect_flags, reason_code, properties):
        connected.clear()

    def on_message(active, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        found = relay_command(secret, agent_id, payload)
        if found is not None and found[0] not in processed_ids:
            command_queue.put(found)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.connect_async(relay["broker"], relay["port"], keepalive=30)
    client.loop_start()

    last_heartbeat = 0.0
    try:
        while True:
            try:
                found = command_queue.get(timeout=0.25)
            except queue.Empty:
                found = None
            if found is not None:
                command_id, command, off_by = found
                if command_id not in processed_ids:
                    run_relay_command(runtime, command, off_by)
                    processed_ids.add(command_id)
                    processed_order.append(command_id)
                    if len(processed_order) > 100:
                        processed_ids.discard(processed_order.pop(0))
                    publish_status()

            now = time.monotonic()
            event = runtime.current_event()
            if now - last_heartbeat >= heartbeat_interval(config, event, sampler is not None):
                publish_status(event)
                last_heartbeat = now
    finally:
        if sampler is not None:
            sampler.stop()
        if connected.is_set():
            client.publish(
                status_topic,
                json.dumps(status_message(False, {"state": "offline"}), ensure_ascii=False, separators=(",", ":")),
                qos=1,
                retain=True,
            ).wait_for_publish(timeout=2)
        client.disconnect()
        client.loop_stop()


class CommandCursor:
    """The last hub command this agent took, kept in a small state file so a
    restarted agent does not run old commands again.

    The hub numbers commands from 1 every time it starts and sends an epoch
    that changes with each start; on a new epoch the cursor starts from 0.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.cursor = 0
        self.epoch = ""
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            self.cursor = max(0, int(state.get("cursor", 0)))
            self.epoch = str(state.get("epoch") or "")
        except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
            pass

    def save(self):
        """Write the state file.  Failing to write it (a read-only folder, a
        full disk) must not cost the command: it is logged and the agent
        goes on."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(json.dumps({"cursor": self.cursor, "epoch": self.epoch}), encoding="utf-8")
            os.replace(temp, self.path)
            return True
        except OSError as exc:
            print(f"[agent] cannot save {self.path}: {exc}", flush=True)
            return False

    def hub_restarted(self, epoch) -> bool:
        """Note the hub's epoch; True when the hub restarted since the cursor
        was taken (then the cursor starts again from 0)."""
        epoch = str(epoch or "")
        if not epoch or epoch == self.epoch:
            return False
        restarted = bool(self.epoch)
        self.epoch = epoch
        if restarted:
            self.cursor = 0
        self.save()
        return restarted

    def advance(self, seq) -> bool:
        """Take command ``seq``; False when it was taken already."""
        if seq <= self.cursor:
            return False
        self.cursor = seq
        self.save()
        return True


class HubLoop:
    """The agent's side of the HTTP hub: register, send heartbeats, take commands."""

    def __init__(self, config, hub, runtime, cursor, sampler=None):
        self.config = config
        self.hub = hub
        self.runtime = runtime
        self.cursor = cursor
        self.sampler = sampler
        self.registered = False
        self.last_heartbeat = 0.0

    def step(self):
        try:
            now = time.monotonic()
            if not self.registered:
                self.hub.register(metadata(self.config))
                self.registered = True
                print(f"[agent] online as {self.config['agent_id']} (hub={self.config['hub_url']})", flush=True)
            event = self.runtime.current_event()
            if now - self.last_heartbeat >= heartbeat_interval(self.config, event, self.sampler is not None):
                self.hub.heartbeat(with_system({"cursor": self.cursor.cursor, **event}, self.sampler))
                self.last_heartbeat = now
            item = self.hub.poll(self.cursor.cursor)
            if self.cursor.hub_restarted(item.get("epoch")):
                # What the hub knew about this agent (its limits, its state)
                # is gone too: register again and report at once, then take
                # the hub's commands from the start.
                self.registered = False
                self.last_heartbeat = 0.0
                print("[agent] hub restarted; taking its commands from the start", flush=True)
                return
            seq = int(item.get("seq", self.cursor.cursor))
            if self.cursor.advance(seq) and item.get("command"):
                try:
                    self.runtime.handle(item["command"])
                except Exception as exc:
                    self.runtime._set_event({"state": "error", "error": str(exc), "ts": time.time()})
                    print(f"[agent] command rejected: {exc}", flush=True)
        except Exception as exc:
            self.registered = False
            print(f"[agent] control-plane retry: {exc}", flush=True)


def run_agent(config, config_path):
    if config.get("transport") == "mqtt_relay":
        return run_relay_agent(config, config_path)
    state_path = Path(config.get("state_file") or (str(config_path) + ".state.json")).expanduser().resolve()
    loop = HubLoop(config, HubClient(config, config_path), AgentRuntime(config),
                   CommandCursor(state_path), start_sampler(config))
    while True:
        loop.step()
        time.sleep(max(0.5, float(config["poll_interval"])))


def main(argv=None):
    parser = argparse.ArgumentParser(description="FlowBench headless server agent")
    parser.add_argument("--config", default="agent.json")
    args = parser.parse_args(argv)
    path = Path(args.config).expanduser().resolve()
    return run_agent(load_config(path), path)


if __name__ == "__main__":
    raise SystemExit(main())
