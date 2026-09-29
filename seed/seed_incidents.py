"""Seed 20 historical incidents into Hindsight (backdated) and dejavu_db, then create the runbooks.

Run from the project root:
    python -m seed.seed_incidents           # idempotent: re-retaining an incident replaces its document
    python -m seed.seed_incidents --reset   # delete the memory bank + seed rows first
"""
import argparse
import asyncio
import time
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, delete

from agent import runbooks
from agent.memory import RUNBOOKS, IncidentMemory, event_text, incident_tags
from agent.models import Incident, RunbookVersion, engine, init_db
from seed.incidents import INCIDENTS

SERVICES = ["catalog-api", "checkout-api", "export-api"]
CONCURRENCY = 4


def opened_at(inc: dict, now: datetime) -> datetime:
    day = now - timedelta(days=inc["days_ago"])
    return day.replace(hour=inc["hour"], minute=7, second=0, microsecond=0)


def build_items(inc: dict, start: datetime) -> list[dict]:
    """One Hindsight item per event, each with its own backdated timestamp."""
    iid, tags = inc["id"], incident_tags(inc["affected"], inc["severity"])
    meta = {"incident_id": iid, "service": inc["service"], "severity": inc["severity"], "source": "seed"}

    def item(minutes: float, kind: str, text: str, outcome=None, actor=None) -> dict:
        return {
            "content": event_text(iid, kind, text, outcome=outcome, actor=actor),
            "timestamp": start + timedelta(minutes=minutes),
            "context": f"{kind} for incident {iid} on {inc['service']}",
            "metadata": meta,
            "tags": tags,
        }

    items = [item(0, "alert", f"{inc['title']}. {inc['severity']} on {', '.join(inc['affected'])}, "
                              f"version {inc['version']}. {inc['alert']}")]
    for minutes, actor, action, outcome in inc["timeline"]:
        items.append(item(minutes, "action" if outcome else "finding", action, outcome, actor))
    items.append(item(
        inc["resolve_min"], "resolution",
        f"Resolved after {inc['resolve_min']} min; correct diagnosis after {inc['diagnose_min']} min. "
        f"Root cause: {inc['root_cause']} Fix that worked: {inc['fix']} "
        f"Expert / owner: {inc['owner']}.",
        actor=inc["owner"],
    ))
    items.append(item(inc["resolve_min"] + 24 * 60, "postmortem",
                      f"Approved postmortem for '{inc['title']}'. {inc['postmortem']}", actor=inc["owner"]))
    return items


def incident_row(inc: dict, start: datetime) -> Incident:
    return Incident(
        id=inc["id"], service=inc["service"], affected_services=inc["affected"], severity=inc["severity"],
        title=inc["title"], signature=inc["signature"], status="resolved", source="seed", owner=inc["owner"],
        opened_at=start,
        diagnosed_at=start + timedelta(minutes=inc["diagnose_min"]),
        resolved_at=start + timedelta(minutes=inc["resolve_min"]),
        time_to_diagnose_s=inc["diagnose_min"] * 60,
        time_to_resolve_s=inc["resolve_min"] * 60,
        memory_used=False,  # all of these happened before DéjàVu existed
        root_cause=inc["root_cause"], fix=inc["fix"],
    )


async def main(reset: bool, skip_models: bool) -> None:
    memory = IncidentMemory()
    now = datetime.now(timezone.utc)
    try:
        init_db()
        if reset:
            print(f"Deleting memory bank '{memory.bank}' and seed rows ...")
            await memory.delete_bank()
            with Session(engine) as session:
                session.exec(delete(Incident).where(Incident.source == "seed"))
                session.exec(delete(RunbookVersion))  # runbooks are rebuilt from the new history below
                session.commit()

        print(f"Configuring bank '{memory.bank}' (mission, skepticism=5, observations) ...")
        await memory.ensure_bank()

        gate = asyncio.Semaphore(CONCURRENCY)
        t0 = time.time()

        async def seed_one(inc: dict) -> None:
            start = opened_at(inc, now)
            async with gate:
                await memory.retain_incident_batch(inc["id"], build_items(inc, start))
            print(f"  retained {inc['id']} ({start:%b %d}) {inc['service']:13} {inc['title']}")

        await asyncio.gather(*(seed_one(inc) for inc in INCIDENTS))
        print(f"Retained {len(INCIDENTS)} incidents in {time.time() - t0:.0f}s")

        with Session(engine) as session:
            for inc in INCIDENTS:
                session.merge(incident_row(inc, opened_at(inc, now)))
            session.commit()
        print(f"Wrote {len(INCIDENTS)} incident rows to dejavu_db")

        if not skip_models:
            created = await memory.ensure_mental_models()
            print(f"Mental models created: {created or 'none (already exist)'} "
                  f"- Hindsight builds their content in the background")
            print("Building runbook v1s from the history (about 1 minute) ...")
            for spec in RUNBOOKS:
                if runbooks.latest(spec.id) is None:
                    v = await runbooks.refresh(spec.id, "initial build from incident history", memory)
                    print(f"  {spec.id:22} v{v.version if v else '-'} via {v.source if v else '-'}")

        print("\nSanity check - recall for the live db_leak signature on checkout-api:")
        hits = await memory.recall_similar(
            "TimeoutError: QueuePool limit of size 5 overflow 0 reached, connection timed out", ["checkout-api"])
        for h in hits[:6]:
            print(f"  {h.incident_id or '(observation)':14} {h.text[:110]}")
    finally:
        await memory.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="delete the bank and seed rows first")
    parser.add_argument("--skip-models", action="store_true", help="don't create mental models")
    args = parser.parse_args()
    asyncio.run(main(args.reset, args.skip_models))
