"""Sentinel: tails ShopLite's JSON logs, detects spikes, and POSTs one alert per outage to the agent.

Run from the project root:  python -m sentinel.watcher
"""
import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import httpx

from agent.config import get_settings
from sentinel.detector import Detector


class LogTail:
    """Reads lines appended to a file since the last call; survives the file being created or truncated."""

    def __init__(self, path: Path):
        self.path = path
        self.pos = path.stat().st_size if path.exists() else 0  # start at the end: only new traffic
        self.partial = ""

    def read_new(self) -> list[str]:
        if not self.path.exists():
            return []
        size = self.path.stat().st_size
        if size < self.pos:  # truncated / recreated
            self.pos, self.partial = 0, ""
        if size == self.pos:
            return []
        with self.path.open("r", encoding="utf-8") as f:
            f.seek(self.pos)
            chunk = f.read()
            self.pos = f.tell()
        lines = (self.partial + chunk).split("\n")
        self.partial = lines.pop()  # last piece may be a half-written line
        return [line for line in lines if line.strip()]


class AlertSender:
    """Delivers events to the agent in order; keeps undelivered ones and retries next tick."""

    def __init__(self, agent_url: str, record_path: Path):
        self.url = agent_url.rstrip("/") + "/alerts"
        self.record_path = record_path
        self.outbox: deque[dict] = deque(maxlen=100)
        self.client = httpx.Client(timeout=5)
        self.agent_down = False

    def send(self, event: dict) -> None:
        with self.record_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
        self.outbox.append(event)

    def flush(self) -> None:
        while self.outbox:
            try:
                self.client.post(self.url, json=self.outbox[0]).raise_for_status()
            except httpx.HTTPError as e:
                if not self.agent_down:
                    print(f"  agent unreachable at {self.url} ({type(e).__name__}); "
                          f"{len(self.outbox)} event(s) queued, will retry", flush=True)
                self.agent_down = True
                return
            self.outbox.popleft()
            if self.agent_down:
                print("  agent reachable again; queued events delivered", flush=True)
            self.agent_down = False


def describe(event: dict) -> str:
    head = f"[{event['state'].upper():8}] {event['alert_id']} {event['severity']} {event['signature']}"
    label = "last breach: " if event["state"] == "resolved" else ""
    return f"{head} on {', '.join(event['affected_services'])}\n           {label}{event['summary']}"


def main() -> None:
    s = get_settings()
    log_file = s.log_path / "shoplite.log"
    s.log_path.mkdir(parents=True, exist_ok=True)
    tail = LogTail(log_file)
    detector = Detector(s.sentinel_window_s, s.sentinel_min_requests, s.sentinel_error_rate,
                        s.sentinel_p95_ms, s.sentinel_cooldown_s)
    sender = AlertSender(s.agent_url, s.log_path / "alerts.jsonl")

    print(f"Sentinel watching {log_file}\n  window {s.sentinel_window_s}s, spike = >= {s.sentinel_error_rate:.0%} 5xx "
          f"or p95 >= {s.sentinel_p95_ms:.0f} ms (min {s.sentinel_min_requests} req), "
          f"resolve after {s.sentinel_cooldown_s}s quiet\n  alerts -> {sender.url}", flush=True)

    next_tick = time.monotonic() + s.sentinel_tick_s
    while True:
        for line in tail.read_new():
            try:
                detector.add(json.loads(line))
            except (json.JSONDecodeError, KeyError, ValueError):
                continue  # not a request log line
        if time.monotonic() >= next_tick:
            next_tick += s.sentinel_tick_s
            for event in detector.evaluate(datetime.now(timezone.utc)):
                print(describe(event), flush=True)
                sender.send(event)
            sender.flush()
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
