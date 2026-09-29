"""Remove LIVE incidents (tests, rehearsals) so the demo starts from the clean 20-incident history.

Deletes live rows + timeline events from dejavu_db, their documents from Hindsight, every runbook
version after v1 (v1 = built from the seed history), and restarts numbering at INC-2051.
Seed incidents are untouched. Stop the agent first.

Run from the project root:  python scripts/reset_live.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from sqlmodel import Session, delete, select  # noqa: E402

from agent.memory import IncidentMemory  # noqa: E402
from agent.models import INCIDENT_NUMBER_START, Incident, IncidentEvent, RunbookVersion, engine, init_db  # noqa: E402


async def main() -> None:
    init_db()
    with Session(engine) as session:
        ids = list(session.exec(select(Incident.id).where(Incident.source == "live")))
    print(f"Live incidents to remove: {ids or 'none'}")
    memory = IncidentMemory()
    try:
        for incident_id in ids:
            await memory.delete_incident(incident_id)
    finally:
        await memory.close()
    with Session(engine) as session:
        session.exec(delete(IncidentEvent).where(IncidentEvent.incident_id.in_(ids)))
        session.exec(delete(Incident).where(Incident.source == "live"))
        removed = session.exec(delete(RunbookVersion).where(RunbookVersion.version > 1)).rowcount
        session.commit()
    print(f"Removed {removed} runbook version(s) after v1")
    with engine.begin() as conn:
        conn.execute(text(f"ALTER SEQUENCE incident_number_seq RESTART WITH {INCIDENT_NUMBER_START}"))
    print(f"Done. Hindsight documents and rows removed; next incident will be INC-{INCIDENT_NUMBER_START}.")


if __name__ == "__main__":
    asyncio.run(main())
