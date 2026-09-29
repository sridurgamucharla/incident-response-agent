"""Fault injection. Each fault causes a *real* failure mode, not a fake log line."""
import threading
import time
from datetime import datetime, timezone

from shoplite.db import engine

FAULTS = {
    "db_leak": "Every request leaks a DB connection until the pool (5) is exhausted",
    "memory_leak": "Each request retains 4 MB; latency climbs, then requests fail with MemoryError",
    "slow_payment": "Payment gateway takes 8s to answer; checkout times out after 3s",
    "bad_deploy": "Deploys v2.4.1, where /export reads a renamed column and returns 500",
    "expired_api_key": "The payment provider API key expires; checkout gets 401s",
}

GOOD_VERSION, BAD_VERSION = "v2.4.0", "v2.4.1"
LEAK_BLOCK_MB = 4
MEMORY_LIMIT_MB = 256


class Chaos:
    def __init__(self) -> None:
        self.since: dict[str, str] = {}  # fault -> ISO time it was enabled
        self._leaked_conns: list = []
        self._leaked_blocks: list[bytes] = []
        self._lock = threading.Lock()  # guards the leak lists against in-flight requests

    def is_on(self, fault: str) -> bool:
        return fault in self.since

    @property
    def version(self) -> str:
        return BAD_VERSION if self.is_on("bad_deploy") else GOOD_VERSION

    @property
    def leaked_mb(self) -> int:
        return len(self._leaked_blocks) * LEAK_BLOCK_MB

    def enable(self, fault: str) -> None:
        self.since.setdefault(fault, datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def disable(self, fault: str) -> None:
        with self._lock:
            self.since.pop(fault, None)
            if fault == "db_leak":
                for conn in self._leaked_conns:
                    conn.close()
                self._leaked_conns.clear()
            if fault == "memory_leak":
                self._leaked_blocks.clear()

    def reset(self) -> None:
        for fault in FAULTS:
            self.disable(fault)

    def status(self) -> dict:
        return {
            "version": self.version,
            "leaked_db_connections": len(self._leaked_conns),
            "leaked_memory_mb": self.leaked_mb,
            "faults": [
                {"name": f, "description": d, "active": self.is_on(f), "since": self.since.get(f)}
                for f, d in FAULTS.items()
            ],
        }

    def on_request(self) -> None:
        """Per-request side effects of the active faults. Runs in a worker thread."""
        if self.is_on("db_leak"):
            # Checked out and never returned; once the pool is empty this raises
            # sqlalchemy.exc.TimeoutError ("QueuePool limit of size 5 overflow 0 reached").
            conn = engine.connect()
            with self._lock:
                if self.is_on("db_leak"):
                    self._leaked_conns.append(conn)
                    conn = None
            if conn is not None:  # fault was switched off while we waited for the pool
                conn.close()
        if self.is_on("memory_leak"):
            if self.leaked_mb >= MEMORY_LIMIT_MB:
                raise MemoryError(
                    f"Cannot allocate {LEAK_BLOCK_MB * 1024 * 1024} bytes: worker heap "
                    f"{self.leaked_mb} MB exceeds container limit {MEMORY_LIMIT_MB} MB"
                )
            block = b"x" * (LEAK_BLOCK_MB * 1024 * 1024)
            with self._lock:
                if self.is_on("memory_leak"):
                    self._leaked_blocks.append(block)
            time.sleep(self.leaked_mb / 400)  # GC pauses grow with heap size


chaos = Chaos()
