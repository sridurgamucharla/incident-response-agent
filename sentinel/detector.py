"""Spike detection over a sliding window of ShopLite log lines, with alert-storm debouncing.

One outage = one incident: services that break together (overlapping services) are merged
into a single outage, and it only resolves after a quiet cooldown.
"""
import uuid
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

LATENCY_SIGNATURE = "HighLatency"


@dataclass
class ServiceStats:
    service: str
    requests: int
    errors: int
    p95_ms: float
    signature: str  # dominant exception type, or HighLatency when only slow

    @property
    def error_rate(self) -> float:
        return self.errors / self.requests if self.requests else 0.0

    def as_dict(self) -> dict:
        return {"requests": self.requests, "errors": self.errors,
                "error_rate": round(self.error_rate, 3), "p95_ms": self.p95_ms}


@dataclass
class Outage:
    alert_id: str
    signature: str
    services: set[str]
    started_at: datetime
    last_breach: datetime
    detected_at: datetime
    peak_error_rate: float = 0.0
    breaches: int = 0
    last_payload: dict = field(default_factory=dict)


class Detector:
    def __init__(self, window_s: int, min_requests: int, error_rate: float, p95_ms: float, cooldown_s: int):
        self.window = timedelta(seconds=window_s)
        self.min_requests = min_requests
        self.error_rate = error_rate
        self.p95_ms = p95_ms
        self.cooldown = timedelta(seconds=cooldown_s)
        self.entries: deque[dict] = deque()
        self.outages: list[Outage] = []

    def add(self, entry: dict) -> None:
        entry["_ts"] = datetime.fromisoformat(entry["timestamp"])
        self.entries.append(entry)

    # ---------- window stats ----------

    def _stats(self) -> dict[str, ServiceStats]:
        by_service: dict[str, list[dict]] = {}
        for e in self.entries:
            by_service.setdefault(e["service"], []).append(e)
        stats = {}
        for service, rows in by_service.items():
            latencies = sorted(r["latency_ms"] for r in rows)
            errors = [r for r in rows if r["status"] >= 500]
            common = Counter(r.get("exception_type") or "HTTP5xx" for r in errors).most_common(1)
            stats[service] = ServiceStats(
                service=service,
                requests=len(rows),
                errors=len(errors),
                p95_ms=latencies[int(0.95 * (len(latencies) - 1))],
                signature=common[0][0] if common else LATENCY_SIGNATURE,
            )
        return stats

    def _is_breaching(self, s: ServiceStats) -> bool:
        return s.requests >= self.min_requests and (s.error_rate >= self.error_rate or s.p95_ms >= self.p95_ms)

    # ---------- evaluation ----------

    def evaluate(self, now: datetime) -> list[dict]:
        """Return the alert events (firing / updated / resolved) produced by this tick."""
        while self.entries and self.entries[0]["_ts"] < now - self.window:
            self.entries.popleft()

        stats = self._stats()
        breaching = [s for s in stats.values() if self._is_breaching(s)]
        clusters: dict[str, list[ServiceStats]] = {}
        for s in breaching:
            clusters.setdefault(s.signature, []).append(s)

        events = []
        for signature, members in clusters.items():
            services = {m.service for m in members}
            outage = next((o for o in self.outages if o.services & services and self._same_problem(o, signature)), None)
            if outage is None:
                outage = Outage(
                    alert_id=f"alt-{now:%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}",
                    signature=signature,
                    services=set(services),
                    started_at=self._first_error_ts(services) or now,
                    last_breach=now,
                    detected_at=now,
                )
                self.outages.append(outage)
                state = "firing"
            else:
                grew = not services <= outage.services
                # A slow-only outage that starts throwing exceptions is escalation, not a new incident.
                escalated = outage.signature == LATENCY_SIGNATURE and signature != LATENCY_SIGNATURE
                outage.services |= services
                if escalated:
                    outage.signature = signature
                outage.last_breach = now
                state = "updated" if (grew or escalated) else None
            outage.breaches += 1
            outage.peak_error_rate = max([outage.peak_error_rate, *(m.error_rate for m in members)])
            previous_severity = outage.last_payload.get("severity")
            outage.last_payload = self._payload(outage, stats, now)
            if state is None and previous_severity != outage.last_payload["severity"]:
                state = "updated"  # e.g. SEV2 -> SEV1 as the error rate climbs
            if state:
                events.append({**outage.last_payload, "state": state})

        for outage in list(self.outages):
            if now - outage.last_breach >= self.cooldown:
                self.outages.remove(outage)
                events.append({**outage.last_payload, "state": "resolved", "resolved_at": now.isoformat()})
        return events

    @staticmethod
    def _same_problem(outage: Outage, signature: str) -> bool:
        """Same exception, or a slow-only outage escalating to errors (or vice versa).
        A *different* exception on the same service is a new incident, not storm noise."""
        return LATENCY_SIGNATURE in (outage.signature, signature) or outage.signature == signature

    # ---------- payload ----------

    def _first_error_ts(self, services: set[str]) -> datetime | None:
        return next((e["_ts"] for e in self.entries if e["service"] in services and e["status"] >= 500), None)

    def _payload(self, outage: Outage, stats: dict[str, ServiceStats], now: datetime) -> dict:
        rows = [e for e in self.entries if e["service"] in outage.services]
        errors = [e for e in rows if e["status"] >= 500 and (e.get("exception_type") or "HTTP5xx") == outage.signature]
        sample = errors[-1] if errors else (max(rows, key=lambda e: e["latency_ms"]) if rows else {})
        metrics = {s: stats[s].as_dict() for s in sorted(outage.services) if s in stats}
        primary = max(metrics, key=lambda s: (metrics[s]["errors"], metrics[s]["p95_ms"])) if metrics else None
        multi = len(outage.services) > 1
        severity = "SEV1" if multi or outage.peak_error_rate >= 0.5 else "SEV2"
        summary = "; ".join(
            f"{s}: {m['errors']}/{m['requests']} 5xx ({m['error_rate']:.0%}), p95 {m['p95_ms']:.0f} ms"
            for s, m in metrics.items()
        )
        return {
            "alert_id": outage.alert_id,
            "service": primary,
            "affected_services": sorted(outage.services),
            "severity": severity,
            "signature": outage.signature,
            "started_at": outage.started_at.isoformat(),
            "detected_at": outage.detected_at.isoformat(),
            "last_breach_at": outage.last_breach.isoformat(),
            "window_s": int(self.window.total_seconds()),
            "metrics": metrics,
            "summary": summary,
            "error_sample": sample.get("error"),
            "stack_sample": sample.get("stack"),
            # the version the *failing* requests ran (a fresh bad deploy is still a minority of the window)
            "version": Counter(e.get("version") for e in (errors or rows)).most_common(1)[0][0] if rows else None,
            "db_pool_in_use_max": max((e.get("db_pool_in_use", 0) for e in rows), default=0),
        }
