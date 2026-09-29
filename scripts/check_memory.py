"""Phase 3 check: does Hindsight recall the right past incident for each chaos fault?

Run from the project root:  python scripts/check_memory.py [--reflect]
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # engineer names like Tomás on Windows consoles

from agent.memory import IncidentMemory  # noqa: E402

# live error signature from ShopLite -> the past incidents that should come back
PROBES = [
    ("db_leak", ["checkout-api"], "TimeoutError: QueuePool limit of size 5 overflow 0 reached, connection timed out",
     {"INC-2034", "INC-2044"}),
    ("memory_leak", ["catalog-api"], "MemoryError: Cannot allocate 4194304 bytes: worker heap 256 MB exceeds container limit",
     {"INC-2037", "INC-2047"}),
    ("slow_payment", ["checkout-api"], "PaymentGatewayTimeout: POST https://api.payfast.example/v1/charges timed out after 3.0s",
     {"INC-2032", "INC-2042"}),
    ("bad_deploy", ["export-api"], "KeyError: 'product_sku' in export_orders after deploy v2.4.1",
     {"INC-2040"}),
    ("expired_api_key", ["checkout-api"], "PaymentAuthError: 401 Unauthorized: api key pk_live_****9f2c expired",
     {"INC-2038", "INC-2048"}),
]


async def main(with_reflect: bool) -> None:
    memory = IncidentMemory()
    passed = 0
    try:
        for fault, services, query, expected in PROBES:
            similar = await memory.similar_incidents(query, services)
            top = [f"{s.incident_id} ({s.similarity})" for s in similar]
            ok = bool(expected & {s.incident_id for s in similar})
            passed += ok
            print(f"[{'OK' if ok else 'MISS'}] {fault:16} top incidents {top}  (expected any of {sorted(expected)})")

        if with_reflect:
            r = await memory.reflect(
                "checkout-api, catalog-api and export-api are all returning 503 with TimeoutError: QueuePool limit "
                "of size 5 reached. What is the likely root cause, what fixed it before, what did NOT work, and "
                "who should I page?", services=["checkout-api"])
            print(f"\nreflect used {r.memories_used} memories from {r.incident_ids} "
                  f"and mental models {r.mental_models_used}:\n{r.text[:1500]}\n")

        print("\nMental models:")
        for m in await memory.list_mental_models():
            size = len(m.content or "")
            status = f"{size} chars, refreshed {m.last_refreshed_at}" if size else "still building..."
            print(f"  {m.id:22} {m.name:28} {status}")
    finally:
        await memory.close()
    print(f"\n{passed}/{len(PROBES)} chaos faults recalled their past twin")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reflect", action="store_true", help="also run one reflect call")
    asyncio.run(main(parser.parse_args().reflect))
