"""Small in-memory control hub for FlowBench server agents.

Run this behind an HTTPS reverse proxy in production. The hub never sends
traffic to a target itself; it only queues bounded, authenticated jobs for a
registered agent. Agents are outbound-only, which is useful for VPSs that do
not expose an inbound management port.
"""
from __future__ import annotations

import argparse
import json
import secrets
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

try:
    from agent.transport import compact_json, decode_json
except ModuleNotFoundError:  # allow: python agent/hub.py
    from transport import compact_json, decode_json

MAX_BODY = 64 * 1024


class HubState:
    def __init__(self, config: dict, config_path: str | None = None):
        self.config_path = Path(config_path).resolve() if config_path else None
        self.controller_token = str(config.get("controller_token") or "")
        if len(self.controller_token) < 16:
            raise ValueError("controller_token must be at least 16 characters")
        self.agents = {}
        for item in config.get("agents", []):
            agent_id = str(item.get("agent_id") or "").strip()
            token = str(item.get("token") or "")
            if agent_id and len(token) >= 16:
                self.agents[agent_id] = {
                    "token": token,
                    "name": str(item.get("name") or agent_id),
                    "metadata": {},
                    "online": False,
                    "last_seen": 0.0,
                    "last_event": {},
                    "next_seq": 0,
                    "commands": [],
                }
        self.lock = threading.RLock()
        # Commands are numbered 1, 2, 3, ... and kept only in memory, so the
        # numbers start again whenever the hub starts.  Polls carry this
        # epoch, which changes with every start, so an agent knows when to
        # take the numbers from the start again.
        self.epoch = secrets.token_hex(8)

    def _save_config(self):
        if not self.config_path:
            return
        payload = {
            "controller_token": self.controller_token,
            "agents": [
                {"agent_id": agent_id, "name": item["name"], "token": item["token"]}
                for agent_id, item in sorted(self.agents.items())
            ],
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.config_path)

    def provision(self, name, agent_id=None):
        with self.lock:
            clean_name = str(name or "").strip()[:64]
            if not clean_name:
                raise ValueError("agent name is required")
            clean_id = str(agent_id or ("server-" + secrets.token_hex(6))).strip()[:64]
            if not clean_id or clean_id in self.agents:
                raise ValueError("agent_id is empty or already exists")
            token = secrets.token_urlsafe(32)
            self.agents[clean_id] = {
                "token": token,
                "name": clean_name,
                "metadata": {},
                "online": False,
                "last_seen": 0.0,
                "last_event": {},
                "next_seq": 0,
                "commands": [],
            }
            self._save_config()
            return {"agent_id": clean_id, "name": clean_name, "token": token}

    def _agent(self, agent_id):
        agent = self.agents.get(agent_id)
        if not agent:
            raise KeyError("unknown agent")
        return agent

    def authenticate_agent(self, agent_id, token):
        with self.lock:
            agent = self._agent(agent_id)
            if not secrets.compare_digest(str(agent["token"]), str(token or "")):
                raise PermissionError("invalid agent token")
            return agent

    def register(self, agent_id, token, metadata):
        with self.lock:
            agent = self.authenticate_agent(agent_id, token)
            agent["metadata"] = dict(metadata or {})
            agent["online"] = True
            agent["last_seen"] = time.time()
            return self.public_agent(agent_id)

    def heartbeat(self, agent_id, token, event=None):
        with self.lock:
            agent = self.authenticate_agent(agent_id, token)
            agent["online"] = True
            agent["last_seen"] = time.time()
            if event:
                agent["last_event"] = dict(event)
            return self.public_agent(agent_id)

    def public_agent(self, agent_id):
        agent = self._agent(agent_id)
        return {
            "agent_id": agent_id,
            "name": agent["name"],
            "metadata": dict(agent["metadata"]),
            "online": bool(agent["online"] and time.time() - agent["last_seen"] < 30),
            "last_seen": agent["last_seen"],
            "last_event": dict(agent["last_event"]),
            "queued_commands": len(agent["commands"]),
        }

    def poll(self, agent_id, token, after):
        with self.lock:
            agent = self.authenticate_agent(agent_id, token)
            agent["online"] = True
            agent["last_seen"] = time.time()
            if after > agent["next_seq"]:
                # The agent got this far before the hub restarted.  Agents
                # that do not know the epoch keep asking for what comes after
                # their old number, so number the new commands after it.
                for offset, item in enumerate(agent["commands"], 1):
                    item["seq"] = after + offset
                agent["next_seq"] = after + len(agent["commands"])
            for item in agent["commands"]:
                if item["seq"] > after:
                    return item
            return None

    def enqueue(self, agent_id, command):
        with self.lock:
            agent = self._agent(agent_id)
            agent["next_seq"] += 1
            item = {"seq": agent["next_seq"], "command": command}
            agent["commands"] = (agent["commands"] + [item])[-100:]
            return item

    def event(self, agent_id, token, payload):
        with self.lock:
            agent = self.authenticate_agent(agent_id, token)
            agent["last_seen"] = time.time()
            agent["online"] = True
            agent["last_event"] = dict(payload or {})
            return {"ok": True}

    def list_agents(self):
        with self.lock:
            return [self.public_agent(agent_id) for agent_id in sorted(self.agents)]


class HubHandler(BaseHTTPRequestHandler):
    server_version = "FlowBenchHub/1.0"

    def _json(self, status, payload):
        raw = compact_json(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("invalid content length")
        if length <= 0 or length > MAX_BODY:
            raise ValueError("request body is missing or too large")
        return decode_json(self.rfile.read(length))

    def _bearer(self):
        value = self.headers.get("Authorization", "")
        return value[7:] if value.lower().startswith("bearer ") else ""

    @property
    def state(self):
        return self.server.state

    def _require_controller(self):
        if not secrets.compare_digest(self._bearer(), self.state.controller_token):
            raise PermissionError("invalid controller token")

    def _parts(self):
        return [p for p in urlsplit(self.path).path.split("/") if p]

    def do_GET(self):
        try:
            parts = self._parts()
            if parts == ["health"]:
                return self._json(200, {"ok": True, "service": "flowbench-hub"})
            if parts == ["v1", "controller", "agents"]:
                self._require_controller()
                return self._json(200, {"agents": self.state.list_agents()})
            if len(parts) == 4 and parts[:2] == ["v1", "agents"] and parts[3] == "commands": 
                agent_id = parts[2]
                query = parse_qs(urlsplit(self.path).query)
                try:
                    after = int(query.get("after", [0])[0])
                except ValueError:
                    raise ValueError("after must be an integer")
                item = self.state.poll(agent_id, self._bearer(), after)
                return self._json(200, dict(item or {"seq": after, "command": None},
                                            epoch=self.state.epoch))
            return self._json(404, {"error": "not_found"})
        except (KeyError, PermissionError, ValueError) as exc:
            return self._json(403 if isinstance(exc, PermissionError) else 400, {"error": str(exc)})
        except Exception as exc:
            return self._json(500, {"error": type(exc).__name__})

    def do_POST(self):
        try:
            parts = self._parts()
            payload = self._read_json()
            if parts == ["v1", "agents", "register"]:
                result = self.state.register(payload.get("agent_id"), payload.get("token"), payload.get("metadata"))
                return self._json(200, result)
            if len(parts) == 4 and parts[:2] == ["v1", "agents"] and parts[3] == "heartbeat":
                result = self.state.heartbeat(parts[2], self._bearer(), payload)
                return self._json(200, result)
            if len(parts) == 4 and parts[:2] == ["v1", "agents"] and parts[3] == "events":
                result = self.state.event(parts[2], self._bearer(), payload)
                return self._json(200, result)
            if parts == ["v1", "controller", "agents", "provision"]:
                self._require_controller()
                if not isinstance(payload, dict):
                    raise ValueError("provision payload must be an object")
                result = self.state.provision(payload.get("name"), payload.get("agent_id"))
                return self._json(201, result)
            if len(parts) == 5 and parts[:3] == ["v1", "controller", "agents"] and parts[4] == "commands":
                self._require_controller()
                command = payload.get("command") if isinstance(payload, dict) else None
                if not isinstance(command, dict) or command.get("type") not in {"start", "stop", "sync_targets"}:
                    raise ValueError("command must be a start, stop, or sync_targets object")
                item = self.state.enqueue(parts[3], command)
                return self._json(202, item)
            return self._json(404, {"error": "not_found"})
        except (KeyError, PermissionError, ValueError) as exc:
            return self._json(403 if isinstance(exc, PermissionError) else 400, {"error": str(exc)})
        except Exception as exc:
            return self._json(500, {"error": type(exc).__name__})

    def log_message(self, fmt, *args):
        print("[hub] " + (fmt % args), flush=True)

    def handle_one_request(self):
        try:
            return super().handle_one_request()
        except (ssl.SSLError, ConnectionError, BrokenPipeError, TimeoutError):
            # Health scanners, HTTP clients pointed at an HTTPS port, and mobile
            # networks can abort TLS mid-handshake. Do not turn this into a
            # Python traceback; the connection is already dead anyway.
            self.close_connection = True


def load_config(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="FlowBench authenticated agent control hub")
    parser.add_argument("--config", default="hub.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--certfile", default="", help="optional TLS certificate for direct HTTPS access")
    parser.add_argument("--keyfile", default="", help="private key for --certfile")
    parser.add_argument("--wait-ready", type=float, default=0.0,
                        help="seconds to wait after listening is bound before serving requests")
    args = parser.parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), HubHandler)
    if args.certfile and args.keyfile:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.certfile, args.keyfile)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    else:
        scheme = "http"
    server.state = HubState(load_config(args.config), args.config)
    print(f"FlowBench Hub listening on {scheme}://{args.host}:{args.port}", flush=True)
    print("Put this behind HTTPS before exposing it to the Internet.", flush=True)
    if args.wait_ready > 0:
        time.sleep(args.wait_ready)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
