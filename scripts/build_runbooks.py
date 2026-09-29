"""Build version 1 of every runbook (and the expertise map) from the incident history.

Run from the project root after seeding:  python scripts/build_runbooks.py [--force]
--force adds a new version even if one exists (only stored if the content changed).
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from agent import runbooks  # noqa: E402
from agent.memory import RUNBOOKS, IncidentMemory  # noqa: E402
from agent.models import init_db  # noqa: E402


async def main(force: bool) -> None:
    init_db()
    memory = IncidentMemory()
    try:
        for spec in RUNBOOKS:
            if runbooks.latest(spec.id) and not force:
                print(f"  {spec.id:22} already has v{runbooks.latest(spec.id).version} (use --force to rebuild)")
                continue
            t = time.time()
            reason = "manual rebuild" if force else "initial build from incident history"
            v = await runbooks.refresh(spec.id, reason, memory)
            if v:
                print(f"  {spec.id:22} v{v.version} via {v.source} in {time.time() - t:.0f}s "
                      f"({len(v.content)} chars, cites {v.incident_ids})")
            else:
                print(f"  {spec.id:22} unchanged")
    finally:
        await memory.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    asyncio.run(main(parser.parse_args().force))
