"""Controller API used by the local GUI for server agents."""
from __future__ import annotations

import json
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from pathlib import Path
import ssl

from agent.relay import (is_relay_url, parse_relay_url, relay_topic,
                         sign_message, verify_message)
from agent.transport import compact_json, decode_json, endpoint


class AgentControlError(RuntimeError):
    pass


class ControllerClient:
    def __init__(self, hub_url, controller_token, timeout=8, ca_file=""):
        self.hub_url = hub_url
        self.controller_token = controller_token
        self.timeout = float(timeout)
        self.ssl_context = None
        self._unverified_context = None
        self._allow_unverified_fallback = False
        ca_file = str(ca_file or "").strip()
        if ca_file:
            if Path(ca_file).is_file():
                self.ssl_context = ssl.create_default_context(cafile=ca_file)
            else:
                # Older all-in-one bundles may not have saved the generated
                # CA.  Permit the narrowly scoped compatibility retry only
                # in this missing-CA case, never for a trusted CA mismatch.
                self._allow_unverified_fallback = True
        else:
            self._allow_unverified_fallback = True

    def call(self, method, path, payload=None):
        body = compact_json(payload) if payload is not None else None
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.controller_token}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = Request(endpoint(self.hub_url, path), data=body, headers=headers, method=method)
        try:
            context = self.ssl_context
            try:
                with urlopen(req, timeout=self.timeout, context=context) as response:
                    data = decode_json(response.read())
                    if response.status >= 400:
                        raise AgentControlError(data.get("error", f"HTTP {response.status}"))
                    return data
            except (URLError, ssl.SSLError) as exc:
                # One-click bundles use a random self-signed certificate. If the
                # local CA file was moved or an older bundle did not save one,
                # retry the token-authenticated control call once without TLS
                # verification. Regular target traffic is not affected.
                if (not self._allow_unverified_fallback
                        or not self._is_certificate_error(exc)):
                    raise
                if self._unverified_context is None:
                    self._unverified_context = ssl._create_unverified_context()
                with urlopen(req, timeout=self.timeout, context=self._unverified_context) as response:
                    data = decode_json(response.read())
                    if response.status >= 400:
                        raise AgentControlError(data.get("error", f"HTTP {response.status}"))
                    return data
        except HTTPError as exc:
            try:
                detail = decode_json(exc.read()).get("error", str(exc))
            except Exception:
                detail = str(exc)
            raise AgentControlError(detail) from exc
        except URLError as exc:
            raise AgentControlError(str(exc.reason)) from exc

    @staticmethod
    def _is_certificate_error(exc):
        reason = getattr(exc, "reason", exc)
        text = str(reason or exc).lower()
        return "certificate_verify_failed" in text or "self-signed certificate" in text

    def list_agents(self):
        return self.call("GET", "/v1/controller/agents").get("agents", [])

    def send(self, agent_id, command):
        return self.call("POST", f"/v1/controller/agents/{agent_id}/commands", {"command": command})

    def provision_agent(self, name, agent_id=""):
        """Create a persistent agent credential on the Hub."""
        payload = {"name": str(name or "").strip()}
        if str(agent_id or "").strip():
            payload["agent_id"] = str(agent_id).strip()
        return self.call("POST", "/v1/controller/agents/provision", payload)


class RelayControllerClient:
    """Persistent controller connection for public MQTT server-agent relays."""

    def __init__(self, relay_url, controller_token, timeout=8):
        self.relay_url = str(relay_url or "").strip()
        self.controller_token = str(controller_token or "")
        if len(self.controller_token) < 16:
            raise ValueError("controller token must be at least 16 characters")
        self.timeout = float(timeout)
        self.config = parse_relay_url(self.relay_url)
        self._client = None
        self._connected = threading.Event()
        self._connect_lock = threading.RLock()
        self._status_lock = threading.RLock()
        self._agents = {}

    @staticmethod
    def supports(url):
        return is_relay_url(url)

    def _build_client(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise AgentControlError("paho-mqtt is required for relay mode") from exc

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"flowbench-controller-{uuid.uuid4().hex[:12]}",
            transport=self.config["transport"],
        )
        if self.config["tls"]:
            client.tls_set()

        def on_connect(active, userdata, flags, reason_code, properties):
            if getattr(reason_code, "is_failure", False):
                self._connected.clear()
                return
            active.subscribe(relay_topic(self.config["relay_id"], "status"), qos=1)
            self._connected.set()

        def on_disconnect(active, userdata, disconnect_flags, reason_code, properties):
            self._connected.clear()

        def on_message(active, userdata, message):
            try:
                payload = json.loads(message.payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return
            if not verify_message(self.controller_token, payload):
                return
            agent_id = str(payload.get("agent_id") or "").strip()
            if not agent_id or payload.get("type") != "status":
                return
            try:
                reported = float(payload.get("last_seen") or 0)
            except (TypeError, ValueError):
                return
            now = time.time()
            with self._status_lock:
                skew = (self._agents.get(agent_id) or {}).get("clock_skew")
            if getattr(message, "retain", False):
                last_seen = reported        # kept by the broker: sent some time ago
            else:
                # Sent just now: judge it by this computer's clock, so a server
                # whose clock is off is not shown offline, and note how far
                # off it is (relayed commands are refused past 5 minutes).
                last_seen, skew = now, (reported - now if reported > 0 else None)
            item = {
                "agent_id": agent_id,
                "name": str(payload.get("name") or agent_id),
                "metadata": dict(payload.get("metadata") or {}),
                "online": bool(payload.get("online") and now - last_seen < 30),
                "last_seen": last_seen,
                "last_event": dict(payload.get("last_event") or {}),
                "queued_commands": 0,
                "clock_skew": skew,
            }
            with self._status_lock:
                self._agents[agent_id] = item

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message
        client.reconnect_delay_set(min_delay=1, max_delay=10)
        return client

    def _ensure_connected(self):
        with self._connect_lock:
            if self._client is None:
                self._client = self._build_client()
                self._client.connect_async(
                    self.config["broker"], self.config["port"], keepalive=30
                )
                self._client.loop_start()
        if not self._connected.wait(self.timeout):
            raise AgentControlError("relay connection timed out")

    def list_agents(self):
        self._ensure_connected()
        # Give retained status messages a brief chance to arrive after the
        # first subscription. Later polls return immediately from the cache.
        if not self._agents:
            time.sleep(0.2)
        now = time.time()
        with self._status_lock:
            result = []
            for value in self._agents.values():
                item = dict(value)
                item["online"] = bool(item.get("online") and now - float(item.get("last_seen") or 0) < 30)
                result.append(item)
        return sorted(result, key=lambda item: item["agent_id"])

    def send(self, agent_id, command):
        self._ensure_connected()
        clean_id = str(agent_id or "").strip()
        if not clean_id:
            raise ValueError("agent_id is required")
        payload = sign_message(self.controller_token, {
            "type": "command",
            "id": uuid.uuid4().hex,
            "agent_id": clean_id,
            "created_at": time.time(),
            "command": dict(command or {}),
        })
        info = self._client.publish(
            relay_topic(self.config["relay_id"], "commands", clean_id),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        info.wait_for_publish(timeout=self.timeout)
        if not info.is_published():
            raise AgentControlError("relay command could not be published")
        return {"id": payload["id"], "command": payload["command"]}

    def close(self):
        with self._connect_lock:
            client = self._client
            self._client = None
            self._connected.clear()
        if client is not None:
            try:
                client.disconnect()
            finally:
                client.loop_stop()
