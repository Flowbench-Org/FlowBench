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
from agent.transport import compact_json, decode_json, endpoint
from agent.worker import HeadlessStressJob

DEFAULTS = {
    "transport": "http",
    "poll_interval": 2.0, "heartbeat_interval": 10.0, "job_heartbeat_interval": 2.0, "request_timeout": 8.0,
    "max_rate": 100.0, "max_duration": 300, "max_threads": 32,
    "max_packet_size": 64 * 1024, "allowed_protocols": ["HTTP", "HTTPS", "TCP"],
    # CPU, memory, network, TCP connections and processes for the desktop's
    # Monitor page; sampled every second and sent at least this often.
    "metrics": True, "metrics_interval": 2.0,
}
SUPPORTED_PROTOCOLS = {"HTTP", "HTTPS", "TCP"}


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
    if not (0 < rate <= float(config["max_rate"])): raise ValueError("rate exceeds agent limit")
    if not (0 < duration <= int(config["max_duration"])): raise ValueError("duration exceeds agent limit")
    if not (0 < threads <= int(config["max_threads"])): raise ValueError("threads exceed agent limit")
    if not (0 < packet_size <= int(config["max_packet_size"])): raise ValueError("packet_size exceeds agent limit")
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
        self.job = HeadlessStressJob(job, self._on_snapshot, self._on_report)
        if not self.job.start():
            self.job = None
            self.job_id = None
            raise RuntimeError("job could not start")
        print(f"[agent] job started: {self.job_id}")


def metadata():
    return {"hostname": socket.gethostname(), "platform": platform.platform(), "python": platform.python_version(), "pid": os.getpid()}


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
    agent_metadata = metadata()
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
        if not verify_message(secret, payload):
            return
        if payload.get("type") != "command" or str(payload.get("agent_id") or "") != agent_id:
            return
        try:
            created_at = float(payload.get("created_at") or 0)
        except (TypeError, ValueError):
            return
        if abs(time.time() - created_at) > 300:
            return
        command_id = str(payload.get("id") or "")
        if not command_id or command_id in processed_ids:
            return
        command_queue.put(payload)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.connect_async(relay["broker"], relay["port"], keepalive=30)
    client.loop_start()

    last_heartbeat = 0.0
    try:
        while True:
            try:
                payload = command_queue.get(timeout=0.25)
            except queue.Empty:
                payload = None
            if payload is not None:
                command_id = str(payload["id"])
                if command_id not in processed_ids:
                    try:
                        runtime.handle(dict(payload.get("command") or {}))
                    except Exception as exc:
                        runtime._set_event({"state": "error", "error": str(exc), "ts": time.time()})
                        print(f"[agent] command rejected: {exc}", flush=True)
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


def run_agent(config, config_path):
    if config.get("transport") == "mqtt_relay":
        return run_relay_agent(config, config_path)
    state_path = Path(config.get("state_file") or (str(config_path) + ".state.json")).expanduser().resolve()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        cursor = max(0, int(state.get("cursor", 0)))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        cursor = 0
    hub = HubClient(config, config_path)
    runtime = AgentRuntime(config)
    sampler = start_sampler(config)
    registered = False
    last_heartbeat = 0.0
    while True:
        try:
            now = time.monotonic()
            if not registered:
                hub.register(metadata()); registered = True
                print(f"[agent] online as {config['agent_id']} (hub={config['hub_url']})", flush=True)
            event = runtime.current_event()
            if now - last_heartbeat >= heartbeat_interval(config, event, sampler is not None):
                hub.heartbeat(with_system({"cursor": cursor, **event}, sampler))
                last_heartbeat = now
            item = hub.poll(cursor)
            seq = int(item.get("seq", cursor))
            if seq > cursor:
                cursor = seq
                state_path.parent.mkdir(parents=True, exist_ok=True)
                temp = state_path.with_suffix(state_path.suffix + ".tmp")
                temp.write_text(json.dumps({"cursor": cursor}), encoding="utf-8")
                os.replace(temp, state_path)
                if item.get("command"):
                    try:
                        runtime.handle(item["command"])
                    except Exception as exc:
                        runtime._set_event({"state": "error", "error": str(exc), "ts": time.time()})
                        print(f"[agent] command rejected: {exc}")
        except Exception as exc:
            registered = False
            print(f"[agent] control-plane retry: {exc}", flush=True)
        time.sleep(max(0.5, float(config["poll_interval"])))


def main(argv=None):
    parser = argparse.ArgumentParser(description="FlowBench headless server agent")
    parser.add_argument("--config", default="agent.json")
    args = parser.parse_args(argv)
    path = Path(args.config).expanduser().resolve()
    return run_agent(load_config(path), path)


if __name__ == "__main__":
    raise SystemExit(main())
