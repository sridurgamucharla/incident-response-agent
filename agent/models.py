"""Incident records in dejavu_db (the system of record; Hindsight holds the knowledge)."""
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, Index, inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel, create_engine

from agent.config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Incident(SQLModel, table=True):
    __tablename__ = "incidents"
    __table_args__ = (
        # At most one OPEN incident per service, enforced by Postgres itself, so two alerts
        # racing in at the same moment can't both open an incident.
        Index("uq_one_open_incident_per_service", "service", unique=True,
              postgresql_where=text("status = 'open'")),
    )

    id: str = Field(primary_key=True)  # e.g. INC-2041; also the Hindsight document_id
    service: str = Field(index=True)
    affected_services: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    severity: str  # SEV1 | SEV2 | SEV3
    title: str
    signature: str | None = None  # dominant exception type from Sentinel
    status: str = Field(default="open", index=True)  # open | resolved
    source: str = "live"  # live | seed
    alert_id: str | None = Field(default=None, index=True)
    owner: str | None = None
    opened_at: datetime = Field(default_factory=utcnow)
    diagnosed_at: datetime | None = None  # when the engineer confirmed the diagnosis
    recovered_at: datetime | None = None  # when Sentinel saw metrics return to normal
    resolved_at: datetime | None = None
    time_to_diagnose_s: int | None = None
    time_to_resolve_s: int | None = None
    memory_used: bool = False  # did the triage draw on recalled past incidents?
    root_cause: str | None = None
    fix: str | None = None
    alert: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))  # latest Sentinel payload
    triage: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))  # memory-ON card + provenance
    triage_no_memory: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))  # memory-OFF card
    postmortem_draft: str | None = None
    postmortem: str | None = None  # human-approved; the only version retained to Hindsight


class IncidentEvent(SQLModel, table=True):
    """Timeline of everything that happened on an incident (alerts, triage, actions, LLM calls ...)."""
    __tablename__ = "incident_events"

    id: int | None = Field(default=None, primary_key=True)
    incident_id: str = Field(foreign_key="incidents.id", index=True)
    at: datetime = Field(default_factory=utcnow)
    kind: str  # alert | alert_update | triage | action | diagnosis | recovered | resolved | postmortem
    actor: str | None = None
    text: str
    outcome: str | None = None  # worked | failed (actions)
    data: dict[str, Any] | None = Field(default=None, sa_column=Column(JSONB))


class RunbookVersion(SQLModel, table=True):
    """Every version of every runbook, so the UI can show how the agent's knowledge evolved."""
    __tablename__ = "runbook_versions"

    id: int | None = Field(default=None, primary_key=True)
    runbook_id: str = Field(index=True)  # runbook-<service> | team-expertise-map
    version: int
    content: str  # Markdown
    created_at: datetime = Field(default_factory=utcnow)
    reason: str  # "initial build from history" | "INC-2051 postmortem approved" | "manual refresh"
    source: str  # hindsight-mental-model | hindsight-reflect
    incident_ids: list[str] = Field(default_factory=list, sa_column=Column(JSONB, nullable=False))
    memories_used: int = 0


engine = create_engine(get_settings().dejavu_database_url, pool_pre_ping=True)

INCIDENT_NUMBER_START = 2051  # the seed covers INC-2031 .. INC-2050


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _add_missing_columns()
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SEQUENCE IF NOT EXISTS incident_number_seq START {INCIDENT_NUMBER_START}"))


def next_incident_id() -> str:
    with engine.begin() as conn:
        return f"INC-{conn.execute(text('SELECT nextval(\'incident_number_seq\')')).scalar()}"


def _add_missing_columns() -> None:
    """Tiny forward-only migration: add columns that exist on the models but not yet in the tables."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing:
                    ddl_type = column.type.compile(dialect=engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {ddl_type}'))
