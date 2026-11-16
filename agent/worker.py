"""Bounded, headless stress worker used by the server agent.

This module deliberately has no Qt dependency, so the agent can run on Linux,
Windows Server, and other Python platforms without a desktop environment.
"""
from __future__ import annotations

import socket
import statistics
import threading
import time
from collections import deque

import requests


def percentile(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((p / 100) * (len(ordered) - 1))))
    return float(ordered[index])


def error_code(exc):
    text = str(exc).lower()
    for key, value in {
        "timed out": "timeout", "timeout": "timeout",
        "refused": "refused", "reset": "reset", "unreachable": "unreachable",
        "getaddrinfo": "dns", "name or service not known": "dns",
        "certificate": "cert", "ssl": "tls", "closed": "closed",
    }.items():
        if key in text:
            return value
    return type(exc).__name__


def request_bytes(response):
    try:
        request = response.request
        total = len(request.method) + len(request.url) + 12
        total += sum(len(k) + len(str(v)) + 4 for k, v in (request.headers or {}).items())
        body = request.body
        if body:
            total += len(body.encode() if isinstance(body, str) else body)
        return total
    except Exception:
        return 0


class TokenBucket:
    def __init__(self, rate):
        self.rate = max(0.1, float(rate))
        self.capacity = max(1.0, self.rate)
        # Start with one token, not a full bucket: a full bucket let the first
        # second send the rate *plus* a whole burst, i.e. up to twice the
        # rate the job was authorized for (the desktop limiter starts at one).
        self.tokens = min(self.capacity, 1.0)
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self, stop):
        while not stop.is_set():
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return True
                wait = min(0.1, (1 - self.tokens) / self.rate)
            stop.wait(wait)
        return False


class HeadlessStressJob:
    """One bounded target job with callbacks compatible with the agent."""

    def __init__(self, config, on_snapshot=None, on_report=None):
        self.config = config
        self.on_snapshot = on_snapshot or (lambda data: None)
        self.on_report = on_report or (lambda data: None)
        self.stop_event = threading.Event()
        self.running = False
        self.lock = threading.Lock()
        self.threads = []
        self.t0 = 0.0
        self.end = 0.0
        self.total = 0
        self.success = 0
        self.fail = 0
        self.bytes_tx = 0
        self.latencies = []
        self.recent = deque(maxlen=400)
        self.buckets = deque()
        self.errors = {}
        self.last_error = ""

    def start(self):
        if self.running:
            return False
        self.running = True
        self.t0 = time.monotonic()
        self.end = self.t0 + int(self.config["duration"])
        bucket = TokenBucket(self.config["rate"])
        self.threads = [threading.Thread(target=self._worker, args=(bucket,), daemon=True, name="flowbench-worker")
                        for _ in range(int(self.config["threads"]))]
        for thread in self.threads:
            thread.start()
        threading.Thread(target=self._supervise, daemon=True, name="flowbench-supervisor").start()
        return True

    def stop(self):
        self.stop_event.set()

    def _request(self, session, bucket, payload):
        proto = self.config["protocol"]
        timeout = self.config["timeout"] / 1000.0
        target = self.config["target"]
        port = self.config["port"]
        if proto in {"HTTP", "HTTPS"}:
            # Never follow a redirect: it can point anywhere, and the load must
            # stay on the authorized host (the native engine does the same).
            with session.get(self.config["url"], timeout=timeout,
                             headers=self.config.get("headers"),
                             verify=(proto == "HTTP"),
                             allow_redirects=False) as response:
                ok = response.status_code < 400
                error = None if ok else f"HTTP {response.status_code}"
                return ok, error, request_bytes(response)
        sock = socket.create_connection((target, port), timeout=timeout)
        try:
            sock.sendall(payload)
            sock.settimeout(timeout)
            try:
                sock.recv(1)
            except socket.timeout:
                pass
            return True, None, len(payload)
        finally:
            sock.close()

    def _worker(self, bucket):
        session = requests.Session()
        session.trust_env = False
        payload = b"X" * max(1, int(self.config["packet_size"]))
        try:
            while not self.stop_event.is_set() and time.monotonic() < self.end:
                if not bucket.acquire(self.stop_event):
                    break
                started = time.monotonic()
                ok = False
                err = None
                sent = 0
                try:
                    ok, err, sent = self._request(session, bucket, payload)
                except Exception as exc:
                    err = error_code(exc)
                elapsed = (time.monotonic() - started) * 1000
                with self.lock:
                    self.total += 1
                    self.bytes_tx += int(sent or 0)
                    self.success += int(bool(ok))
                    self.fail += int(not ok)
                    if err:
                        self.errors[err] = self.errors.get(err, 0) + 1
                        self.last_error = err
                    self.recent.append(elapsed)
                    if len(self.latencies) < 100000:
                        self.latencies.append(elapsed)
                    now = time.monotonic()
                    if self.buckets and now - self.buckets[-1][0] < 0.1:
                        self.buckets[-1][1] += 1
                    else:
                        self.buckets.append([now, 1])
                    while self.buckets and self.buckets[0][0] < now - 1:
                        self.buckets.popleft()
        finally:
            session.close()

    def _snapshot(self):
        with self.lock:
            now = time.monotonic()
            while self.buckets and self.buckets[0][0] < now - 1:
                self.buckets.popleft()
            recent = list(self.recent)
            return {
                "running": True, "total": self.total, "success": self.success,
                "fail": self.fail, "tx": self.bytes_tx,
                "qps": float(sum(x[1] for x in self.buckets)),
                "avg": (sum(recent) / len(recent)) if recent else 0.0,
                "active": sum(t.is_alive() for t in self.threads),
                "progress": min(1.0, (now - self.t0) / max(0.1, self.config["duration"])),
                "last_error": self.last_error,
            }

    def _supervise(self):
        while not self.stop_event.is_set() and time.monotonic() < self.end:
            self.on_snapshot(self._snapshot())
            self.stop_event.wait(0.5)  # wakes at once on stop
        # The test window ends here (or at the planned end); time spent waiting
        # for in-flight requests is not test time (as in the desktop and native
        # engines). It used to count, so a stop on a slow target reported a
        # duration several seconds too long.
        ended = min(time.monotonic(), self.end)
        self.stop_event.set()
        # Wait for the workers about 2 s in all, not 1 s each: with many threads
        # stuck on a slow target, a stop held the report for threads x 1 s.
        deadline = time.monotonic() + 2.0
        for thread in self.threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self.lock:
            lats = list(self.latencies)
            duration = max(0.0, ended - self.t0)
            report = {
                "target": self.config["target"], "protocol": self.config["protocol"],
                "duration": duration, "total": self.total, "success": self.success,
                "fail": self.fail, "avg": sum(lats) / len(lats) if lats else 0.0,
                "p50": percentile(lats, 50), "p90": percentile(lats, 90),
                "p99": percentile(lats, 99), "traffic_mb": self.bytes_tx / 1024 / 1024,
                "bytes_tx": self.bytes_tx, "rate_limit": self.config["rate"],
                "errors": dict(self.errors),
            }
        self.running = False
        self.on_report(report)
