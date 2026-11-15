"""Shared protocol helpers for the FlowBench server-agent control plane."""
from __future__ import annotations

import decimal
import json
from urllib.parse import urljoin, urlsplit, urlunsplit


class ProtocolError(RuntimeError):
    """Raised when a control-plane response is malformed or rejected."""


def base_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("hub_url is required")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("hub_url must be an http(s) URL")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")) + "/"


def endpoint(root: str, path: str) -> str:
    return urljoin(base_url(root), path.lstrip("/"))


def compact_json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("control-plane returned invalid JSON") from exc


def number_text(value) -> str:
    """A number for a message, written out in full: 1000000 rather than
    1e+06, 100.0000004 rather than 100, and no needless ".0"."""
    text = format(decimal.Decimal(repr(float(value))), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
