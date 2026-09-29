"""Steady fake-customer traffic against ShopLite so Sentinel has something to watch.

Run from the project root:  python -m shoplite.traffic --rps 5
"""
import argparse
import asyncio
import random
import time
from collections import Counter

import httpx

from agent.config import get_settings

ROUTES = [("GET", "/products", 0.6), ("POST", "/checkout", 0.3), ("GET", "/export", 0.1)]


async def hit(client: httpx.AsyncClient, stats: Counter) -> None:
    method, path, _ = random.choices(ROUTES, weights=[w for *_, w in ROUTES])[0]
    body = {"product_id": random.randint(1, 12), "quantity": random.randint(1, 3)} if method == "POST" else None
    try:
        r = await client.request(method, path, json=body)
        stats[f"{path} {r.status_code}"] += 1
    except httpx.HTTPError as e:
        stats[f"{path} {type(e).__name__}"] += 1


async def main(rps: float) -> None:
    base = get_settings().shoplite_url
    stats: Counter = Counter()
    last_report = time.monotonic()
    print(f"sending ~{rps} req/s to {base} (Ctrl+C to stop)", flush=True)
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        tasks: set[asyncio.Task] = set()
        while True:
            task = asyncio.create_task(hit(client, stats))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            await asyncio.sleep(random.expovariate(rps))
            if time.monotonic() - last_report >= 5:
                print("last 5s:", dict(sorted(stats.items())), flush=True)
                stats.clear()
                last_report = time.monotonic()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rps", type=float, default=5.0)
    try:
        asyncio.run(main(parser.parse_args().rps))
    except KeyboardInterrupt:
        pass
