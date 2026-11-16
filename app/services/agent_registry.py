"""Remember servers removed from this computer's lists, per control channel."""
from __future__ import annotations

import copy
import hashlib
import json


class AgentRegistry:
    """Filter automatic discovery without changing the remote server or Hub.

    The control URL and token define a connection. Only their digest is stored
    here, so one connection cannot hide a same-named ID on another connection.
    AppSettings owns both persistence and its rollback on write failure.
    """

    KEY = "agent_removed_servers"

    def __init__(self, settings):
        self.settings = settings

    @staticmethod
    def scope_key(url, token) -> str:
        connection = [str(url or "").strip().rstrip("/"),
                      str(token or "").strip()]
        raw = json.dumps(connection, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _entries(agents):
        """Return fresh, valid ID/name records; damaged records are ignored."""
        if not isinstance(agents, (list, tuple)):
            return []
        entries = {}
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            agent_id = agent.get("agent_id")
            if not isinstance(agent_id, str) or not agent_id.strip():
                continue
            agent_id = agent_id.strip()
            name = agent.get("name")
            name = name.strip() if isinstance(name, str) else ""
            entries[agent_id] = {"agent_id": agent_id, "name": name or agent_id}
        return list(entries.values())

    def _snapshot(self):
        value = getattr(self.settings, self.KEY)
        return copy.deepcopy(value) if isinstance(value, dict) else {}

    def removed(self, url, token) -> list:
        with self.settings._lock:
            value = self._snapshot().get(self.scope_key(url, token))
            return self._entries(value)

    def filter(self, url, token, agents) -> list:
        excluded = {item["agent_id"] for item in self.removed(url, token)}
        return [agent for agent in (agents or [])
                if not isinstance(agent, dict)
                or str(agent.get("agent_id") or "").strip() not in excluded]

    def _save(self, value):
        if not self.settings.set(self.KEY, value):
            raise OSError(self.settings.last_error or "Could not save removed servers")

    def remove(self, url, token, agents) -> None:
        entries = self._entries(agents)
        if not entries:
            return
        scope = self.scope_key(url, token)
        with self.settings._lock:
            value = self._snapshot()
            previous = self._entries(value.get(scope))
            merged = {item["agent_id"]: item for item in previous}
            merged.update((item["agent_id"], item) for item in entries)
            current = list(merged.values())
            if current == previous:
                return
            value[scope] = current
            self._save(value)

    def restore(self, url, token, agent_ids=None) -> None:
        scope = self.scope_key(url, token)
        with self.settings._lock:
            value = self._snapshot()
            if scope not in value:
                return
            previous = self._entries(value.get(scope))
            if agent_ids is None:
                current = []
            else:
                if isinstance(agent_ids, str):
                    agent_ids = [agent_ids]
                restored = {str(item or "").strip() for item in agent_ids}
                current = [item for item in previous if item["agent_id"] not in restored]
                if current == previous:
                    return
            if current:
                value[scope] = current
            else:
                value.pop(scope)
            self._save(value)
