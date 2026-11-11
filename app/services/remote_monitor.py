"""System metrics reported by server agents, kept per server for the Monitor page.

The Server Agents page polls the control channel (a Hub or the MQTT relay) and
hands every agent list to ``remote_monitor.ingest``. Each agent's heartbeat
carries its last samples (one per second), so the samples are de-duplicated by
their timestamp and appended to that server's history, in the same format as
the Monitor page's local samples.
"""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime

from PySide6.QtCore import QObject, Signal

MAX_POINTS = 900          # 15 minutes at one sample per second, like the local history
MAX_BATCH = 120           # samples accepted from one heartbeat


def _number(value, low=0.0, high=None) -> float:
    if isinstance(value, bool):
        raise ValueError("not a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("not finite")
    number = max(low, number)
    return number if high is None else min(high, number)


def normalize_sample(raw):
    """(timestamp, sample) in the Monitor page's format, or None when malformed."""
    if not isinstance(raw, dict):
        return None
    try:
        ts = _number(raw["ts"], 1.0)
        sample = {
            "timestamp": datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds"),
            "cpu_percent": _number(raw["cpu"], 0.0, 100.0),
            "memory_percent": _number(raw["mem_percent"], 0.0, 100.0),
            "memory_used_gb": _number(raw["mem_used_gb"]),
            "memory_total_gb": _number(raw["mem_total_gb"]),
            "download_kbs": _number(raw["down_kbs"]),
            "upload_kbs": _number(raw["up_kbs"]),
            "tcp_connections": int(_number(raw.get("tcp_conns", -1), -1.0)),
            "process_count": int(_number(raw.get("proc_count", -1), -1.0)),
        }
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    return ts, sample


class RemoteMonitor(QObject):
    """Per-server metric history; ``changed`` fires after every agent list."""

    changed = Signal()
    refresh_requested = Signal()

    def __init__(self):
        super().__init__()
        self._agents = {}       # agent_id -> state dict
        self._order = []
        self.watching = ""      # the server the Monitor page shows, "" for this PC
        self.page_visible = False
        self.connection = "unknown"     # unknown / unconfigured / ok / error
        self.connection_error = ""

    # ---- polling hints for the Server Agents page ----

    def wants_updates(self) -> bool:
        return bool(self.watching) or self.page_visible

    def request_refresh(self):
        self.refresh_requested.emit()

    def set_connection(self, state: str, error: str = ""):
        error = str(error or "")[:300]
        if (state, error) != (self.connection, self.connection_error):
            self.connection, self.connection_error = state, error
            self.changed.emit()

    # ---- data ----

    def ingest(self, agents):
        """Take an agent list from the Hub or relay and append new samples."""
        order = []
        for agent in agents or []:
            if not isinstance(agent, dict):
                continue
            agent_id = str(agent.get("agent_id") or "").strip()
            if not agent_id or agent_id in order:
                continue
            order.append(agent_id)
            entry = self._agents.get(agent_id)
            if entry is None:
                entry = {"samples": deque(maxlen=MAX_POINTS), "last_ts": 0.0}
                self._agents[agent_id] = entry
            metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
            entry["name"] = str(agent.get("name") or agent_id)
            entry["online"] = bool(agent.get("online"))
            entry["hostname"] = str(metadata.get("hostname") or "")
            event = agent.get("last_event") if isinstance(agent.get("last_event"), dict) else {}
            system = event.get("system")
            entry["error"] = ""
            if not isinstance(system, dict):
                # Heartbeats without "system" come from agents older than this feature.
                entry["status"] = "unsupported" if event else "waiting"
                continue
            if system.get("available") is False:
                entry["status"] = "unavailable"
                entry["error"] = str(system.get("error") or "")[:300]
                continue
            entry["status"] = "ok"
            entry["error"] = str(system.get("error") or "")[:300]
            raw = system.get("samples")
            fresh = []
            for item in (raw if isinstance(raw, list) else [])[-MAX_BATCH:]:
                parsed = normalize_sample(item)
                if parsed is not None:
                    fresh.append(parsed)
            fresh.sort(key=lambda pair: pair[0])
            for ts, sample in fresh:
                if ts > entry["last_ts"]:
                    entry["samples"].append(sample)
                    entry["last_ts"] = ts
        for agent_id in list(self._agents):
            if agent_id not in order:
                del self._agents[agent_id]
        self._order = order
        self.changed.emit()

    def agents(self):
        """[{agent_id, name, online, status, error, hostname}] in the Hub's order."""
        result = []
        for agent_id in self._order:
            entry = self._agents[agent_id]
            result.append({
                "agent_id": agent_id,
                "name": entry["name"],
                "online": entry["online"],
                "status": entry["status"],
                "error": entry["error"],
                "hostname": entry["hostname"],
                "samples": len(entry["samples"]),
            })
        return result

    def agent(self, agent_id):
        return next((item for item in self.agents() if item["agent_id"] == agent_id), None)

    def samples(self, agent_id):
        entry = self._agents.get(agent_id)
        return list(entry["samples"]) if entry else []

    def clear(self, agent_id):
        """Forget a server's history; samples already seen are not taken again."""
        entry = self._agents.get(agent_id)
        if entry is not None:
            entry["samples"].clear()

    def reset(self):
        self._agents.clear()
        self._order = []
        self.changed.emit()


remote_monitor = RemoteMonitor()
