"""The limits a server agent accepts, and fitting a job to them.

An agent refuses a job that asks for more than its ``agent.json`` allows
(``max_rate``, ``max_duration``, ``max_threads``, ``max_packet_size`` and the
allowed protocols).  Agents from this version on report those limits in their
metadata, so the desktop can check a job before it sends it; older agents do
not, and are sent the job as it is.
"""
from __future__ import annotations

import math
import re
from typing import List, Optional, Tuple

# job field, agent limit, type
FIELDS = (
    ("rate", "max_rate", float),
    ("duration", "max_duration", int),
    ("threads", "max_threads", int),
    ("packet_size", "max_packet_size", int),
)

# What a newly generated node allows unless the user picks other values.
DEFAULT_LIMITS = {"max_rate": 100, "max_duration": 300, "max_threads": 32, "max_packet_size": 65536}


def reported_limits(agent) -> Optional[dict]:
    """The limits an agent reported, or None for an agent that does not
    report them (older than this feature) or reported something unusable."""
    metadata = agent.get("metadata") if isinstance(agent, dict) else None
    raw = metadata.get("limits") if isinstance(metadata, dict) else None
    if not isinstance(raw, dict):
        return None
    limits = {}
    for _field, key, kind in FIELDS:
        value = raw.get(key)
        if isinstance(value, bool):
            return None
        try:
            number = kind(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not (math.isfinite(number) and number > 0):
            return None
        limits[key] = number
    protocols = raw.get("protocols")
    if isinstance(protocols, list):
        limits["protocols"] = [str(p).upper() for p in protocols]
    return limits


def problems(job: dict, limits: Optional[dict]) -> List[Tuple[str, object, object]]:
    """What in ``job`` the agent would refuse: [(field, asked, allowed)].

    ``field`` is a job field ("rate", ...) with the agent's maximum, or
    "protocol" with the protocols the agent supports."""
    if not limits:
        return []
    found = []
    protocols = limits.get("protocols")
    protocol = str(job.get("protocol", "")).upper()
    if protocols is not None and protocol not in protocols:
        found.append(("protocol", protocol, list(protocols)))
    for field, key, _kind in FIELDS:
        asked = job.get(field)
        if isinstance(asked, (int, float)) and asked > limits[key]:
            found.append((field, asked, limits[key]))
    return found


def fit(job: dict, limits: Optional[dict]) -> Optional[dict]:
    """``job`` lowered to the agent's limits; None when no lowering helps
    (the agent does not support the protocol)."""
    if any(field == "protocol" for field, _asked, _allowed in problems(job, limits)):
        return None
    fitted = dict(job)
    for field, key, kind in FIELDS:
        if limits and isinstance(fitted.get(field), (int, float)) and fitted[field] > limits[key]:
            fitted[field] = kind(limits[key])
    return fitted


# The reasons an agent gives when it refuses a command (server_agent.py).
_REFUSALS = (
    (r"^rate exceeds agent limit(?:: (?P<asked>[\d.]+) > (?P<limit>[\d.]+))?", "rate"),
    (r"^duration exceeds agent limit(?:: (?P<asked>\d+) > (?P<limit>\d+))?", "duration"),
    (r"^threads exceed agent limit(?:: (?P<asked>\d+) > (?P<limit>\d+))?", "threads"),
    (r"^packet_size exceeds agent limit(?:: (?P<asked>\d+) > (?P<limit>\d+))?", "packet_size"),
    (r"^protocol is not allowed: (?P<asked>\S+)", "protocol"),
    (r"^target is not in the agent allow-list: (?P<asked>\S+)", "target"),
    (r"^an authorized job is already running", "busy"),
)


def refusal(error: str) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
    """(kind, asked, limit) for a refusal an agent reported, else None."""
    text = str(error or "").strip()
    for pattern, kind in _REFUSALS:
        match = re.match(pattern, text)
        if match:
            groups = match.groupdict()
            return kind, groups.get("asked"), groups.get("limit")
    return None
