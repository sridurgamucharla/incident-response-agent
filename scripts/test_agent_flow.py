"""Phase 4 end-to-end check against a running agent (python -m uvicorn agent.main:app --port 8000).

Sends a db_leak alert twice at the same moment (race test), waits for the triage card, then walks the
whole lifecycle: failed action -> working action -> confirm diagnosis -> resolve -> postmortem -> approve,
plus the Memory ON/OFF comparison. Everything it creates is a real (resolved) incident.

Run from the project root:  python scripts/test_agent_flow.py
"""
import asyncio
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agent.config import get_settings  # noqa: E402

BASE = get_settings().agent_url
WS = BASE.replace("http", "ws", 1) + "/ws"


def alert(alert_id: str) -> dict:
    ts = datetime.now(timezone.utc).isoformat()
    return {
        "alert_id": alert_id, "state": "firing", "service": "checkout-api", "severity": "SEV1",
        "affected_services": ["catalog-api", "checkout-api", "export-api"], "signature": "TimeoutError",
        "started_at": ts, "detected_at": ts, "window_s": 30, "version": "v2.4.0", "db_pool_in_use_max": 5,
        "summary": "catalog-api: 18/33 5xx (55%), p95 2013 ms; checkout-api: 12/20 5xx (60%), p95 2015 ms",
        "error_sample": "TimeoutError: QueuePool limit of size 5 overflow 0 reached, connection timed out, timeout 2.00",
        "stack_sample": 'File "shoplite/chaos.py", line 69, in on_request\n    conn = engine.connect()\n'
                        "sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 0 reached",
    }


async def listen(events: Counter, stop: asyncio.Event) -> None:
    async with websockets.connect(WS) as ws:
        while not stop.is_set():
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 1))
                events[msg["type"]] += 1
                if msg["type"] == "llm.call":
                    d = msg["data"]
                    print(f"   [ws] llm.call {d['label']} -> {d['model']} {d['status']} ({d['latency_ms']} ms)")
            except asyncio.TimeoutError:
                pass


async def wait_for(client: httpx.AsyncClient, incident_id: str, field: str, timeout: int = 150) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        inc = (await client.get(f"/incidents/{incident_id}")).json()
        if inc.get(field):
            return inc
        await asyncio.sleep(2)
    raise TimeoutError(f"{field} not ready after {timeout}s")


async def main() -> None:
    events: Counter = Counter()
    stop = asyncio.Event()
    listener = asyncio.create_task(listen(events, stop))
    await asyncio.sleep(1)
    stamp = int(time.time())
    async with httpx.AsyncClient(base_url=BASE, timeout=180) as c:
        print("1. Two alerts for checkout-api at the same instant (race):")
        r1, r2 = await asyncio.gather(c.post("/alerts", json=alert(f"alt-test-{stamp}-a")),
                                      c.post("/alerts", json=alert(f"alt-test-{stamp}-b")))
        a, b = r1.json(), r2.json()
        print(f"   -> {a}  |  {b}")
        assert a["incident_id"] == b["incident_id"] and a["created"] != b["created"], "duplicate incident opened!"
        iid = a["incident_id"]
        print(f"   OK: one incident {iid}, the second alert was attached to it\n")

        print("2. Waiting for the triage card ...")
        t = time.time()
        inc = await wait_for(c, iid, "triage")
        tri = inc["triage"]
        card = tri["card"]
        print(f"   ready in {time.time() - t:.0f}s via {tri['path']} / model {tri['model']} / "
              f"{tri['llm_calls']} LLM call(s), {tri['repairs']} repair(s), memory used: {tri['memory_used']}")
        print(f"   likely cause: {card['likely_cause']}")
        print(f"   fix: {card['suggested_fix']}")
        print(f"   don't try: {[d['action'] for d in card['do_not_try']]}")
        print(f"   matched: {[(m['incident_id'], m['date'], m['similarity']) for m in card['matched_incidents']]}")
        print(f"   owner: {card['owner']}  confidence: {card['confidence']}")
        if tri["notes"]:
            print(f"   notes: {tri['notes']}")

        print("\n3. Lifecycle: actions, diagnosis, resolve")
        await c.post(f"/incidents/{iid}/actions", json={"action": "Restarted all ShopLite pods", "actor": "Test Engineer", "outcome": "failed"})
        await c.post(f"/incidents/{iid}/diagnosis", json={"actor": "Test Engineer"})
        await c.post(f"/incidents/{iid}/actions", json={"action": "Closed leaked sessions via chaos reset (fix deployed)", "actor": "Test Engineer", "outcome": "worked"})
        res = (await c.post(f"/incidents/{iid}/resolve", json={"actor": "Test Engineer"})).json()
        print(f"   diagnosed in {res['time_to_diagnose_s']}s, resolved in {res['time_to_resolve_s']}s")

        print("4. Postmortem draft -> approve")
        inc = await wait_for(c, iid, "postmortem_draft")
        print("   draft: " + inc["postmortem_draft"][:300].replace("\n", " | "))
        inc = (await c.post(f"/incidents/{iid}/postmortem/approve", json={"approver": "Test Engineer"})).json()
        print(f"   approved: {bool(inc['postmortem'])}")

        print("5. Memory OFF comparison")
        cmp = (await c.post(f"/incidents/{iid}/compare")).json()
        off = cmp["memory_off"]
        print(f"   OFF ({off['model']}): {off['card']['likely_cause'][:160]}  confidence {off['card']['confidence']}")
        print(f"   ON  ({cmp['memory_on']['model']}): {cmp['memory_on']['card']['likely_cause'][:160]}  "
              f"confidence {cmp['memory_on']['card']['confidence']}")
        for f in off.get("failed_before") or []:
            print(f"   ! OFF step '{f['suggestion'][:60]}' -> This failed in {f['failed_in']}")
        print(f"   memory adds: {cmp['memory_adds']}")

        detail = (await c.get(f"/incidents/{iid}")).json()
        print(f"\n6. Timeline has {len(detail['events'])} events: {[e['kind'] for e in detail['events']]}")
        iq = (await c.get("/stats/iq")).json()
        print(f"   Agent IQ series: {len(iq)} points; last = {iq[-1]['id']} {iq[-1]['time_to_diagnose_s']}s "
              f"(memory_used={iq[-1]['memory_used']})")
    await asyncio.sleep(1)
    stop.set()
    await listener
    print(f"\nWebSocket events received: {dict(events)}")


if __name__ == "__main__":
    asyncio.run(main())
