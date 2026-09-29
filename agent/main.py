"""DéjàVu agent API: alert intake, triage, actions/outcomes, resolve + postmortem, WebSocket push.

Run from the project root:  python -m uvicorn agent.main:app --port 8000
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from agent.config import get_settings
from agent.llm import GeminiClient
from agent.memory import IncidentMemory
from agent import runbooks
from agent.models import Incident, IncidentEvent, engine, init_db, next_incident_id
from agent.triage import (draft_postmortem, flag_failed_suggestions, human_duration, memory_adds,
                          triage_with_memory, triage_without_memory)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dejavu.agent")


def now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------- WebSocket fan-out ----------------

class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def broadcast(self, kind: str, data: Any) -> None:
        dead = []
        for ws in self.clients:
            try:
                await ws.send_json({"type": kind, "data": data})
            except Exception:  # noqa: BLE001 - a closed tab must not break the agent
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


hub = Hub()
memory: IncidentMemory
llm: GeminiClient
background: set[asyncio.Task] = set()


def spawn(coro) -> None:
    """Fire-and-forget with a strong reference and logged failures."""
    task = asyncio.create_task(coro)
    background.add(task)
    task.add_done_callback(background.discard)
    task.add_done_callback(lambda t: not t.cancelled() and t.exception()
                           and log.error("background task failed", exc_info=t.exception()))


@asynccontextmanager
async def lifespan(_: FastAPI):
    global memory, llm
    init_db()
    memory = IncidentMemory()
    llm = GeminiClient(on_call=lambda entry: hub.broadcast("llm.call", entry))
    yield
    await llm.close()
    await memory.close()


app = FastAPI(title="DéjàVu agent", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[get_settings().web_origin], allow_methods=["*"], allow_headers=["*"])


# ---------------- persistence helpers ----------------

def incident_json(inc: Incident) -> dict:
    return inc.model_dump(mode="json")


def get_incident(incident_id: str) -> Incident:
    with Session(engine) as session:
        inc = session.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(404, f"incident {incident_id} not found")
    return inc


def save(inc: Incident) -> Incident:
    with Session(engine) as session:
        inc = session.merge(inc)
        session.commit()
        session.refresh(inc)
        return inc


async def add_event(incident_id: str, kind: str, text: str, *, actor: str | None = None,
                    outcome: str | None = None, data: dict | None = None) -> dict:
    with Session(engine) as session:
        event = IncidentEvent(incident_id=incident_id, kind=kind, text=text, actor=actor, outcome=outcome, data=data)
        session.add(event)
        session.commit()
        session.refresh(event)
        payload = event.model_dump(mode="json")
    await hub.broadcast("incident.event", payload)
    return payload


def events_for(incident_id: str) -> list[IncidentEvent]:
    with Session(engine) as session:
        return list(session.exec(select(IncidentEvent).where(IncidentEvent.incident_id == incident_id)
                                 .order_by(IncidentEvent.at)))


def open_or_attach(alert: dict) -> tuple[Incident, bool]:
    """Open a new incident, or attach to the service's open one. The partial unique index makes this
    race-safe: if two alerts for the same service arrive together, the loser's INSERT fails and it
    attaches to the winner's incident instead of opening a duplicate."""
    with Session(engine) as session:
        seen = session.exec(select(Incident).where(Incident.alert_id == alert["alert_id"])).first()
        if seen:
            return seen, False
        inc = Incident(
            id=next_incident_id(), service=alert["service"], affected_services=alert.get("affected_services") or [],
            severity=alert["severity"], signature=alert.get("signature"), alert_id=alert["alert_id"], alert=alert,
            title=f"{alert.get('signature')} on {', '.join(alert.get('affected_services') or [alert['service']])}",
        )
        session.add(inc)
        try:
            session.commit()
            session.refresh(inc)
            return inc, True
        except IntegrityError:
            session.rollback()
            existing = session.exec(select(Incident).where(Incident.service == alert["service"],
                                                           Incident.status == "open")).one()
            return existing, False


def incident_for_alert(alert: dict) -> Incident | None:
    with Session(engine) as session:
        return (session.exec(select(Incident).where(Incident.alert_id == alert["alert_id"])).first()
                or session.exec(select(Incident).where(Incident.service == alert.get("service"),
                                                       Incident.status == "open")).first())


# ---------------- alert intake (from Sentinel) ----------------

class Alert(BaseModel):
    model_config = {"extra": "allow"}
    alert_id: str
    state: Literal["firing", "updated", "resolved"]
    service: str
    severity: str
    signature: str | None = None
    affected_services: list[str] = []


@app.post("/alerts", status_code=202)
async def receive_alert(alert: Alert):
    payload = alert.model_dump()
    if alert.state == "firing":
        inc, created = open_or_attach(payload)
        if created:
            await add_event(inc.id, "alert", f"{alert.severity} {alert.signature} on {', '.join(alert.affected_services)}: "
                                             f"{payload.get('summary')}", actor="Sentinel", data=payload)
            await hub.broadcast("incident.opened", incident_json(inc))
            spawn(run_triage(inc.id, payload))
        else:
            await add_event(inc.id, "alert_update", f"Alert {alert.alert_id} merged into open incident {inc.id}",
                            actor="Sentinel", data=payload)
        return {"incident_id": inc.id, "created": created}

    inc = incident_for_alert(payload)
    if inc is None:
        return {"incident_id": None, "ignored": "no matching incident"}
    if alert.state == "updated":
        inc.alert = payload
        inc.affected_services = sorted(set(inc.affected_services) | set(alert.affected_services))
        if alert.severity < inc.severity:  # "SEV1" < "SEV2": escalate, never downgrade
            inc.severity = alert.severity
        inc = save(inc)
        await add_event(inc.id, "alert_update", f"Now {inc.severity} on {', '.join(inc.affected_services)}: "
                                                f"{payload.get('summary')}", actor="Sentinel", data=payload)
        await hub.broadcast("incident.updated", incident_json(inc))
    else:  # resolved: metrics recovered; a human still closes the incident
        inc.recovered_at = now()
        inc = save(inc)
        await add_event(inc.id, "recovered", "Error rate and latency back to normal", actor="Sentinel")
        await hub.broadcast("incident.updated", incident_json(inc))
    return {"incident_id": inc.id}


async def run_triage(incident_id: str, alert: dict) -> None:
    try:
        result = await triage_with_memory(
            alert, incident_id, memory, llm,
            progress=lambda stage: hub.broadcast("triage.progress", {"incident_id": incident_id, "stage": stage}))
    except Exception as e:  # noqa: BLE001 - never leave an incident without a card
        log.exception("triage failed")
        await add_event(incident_id, "triage", f"Triage failed: {e}", actor="DéjàVu")
        return
    inc = get_incident(incident_id)
    inc.triage = result.model_dump(mode="json")
    inc.memory_used = result.memory_used
    inc.owner = result.card.owner.name if result.card.owner else None
    inc = save(inc)
    await add_event(incident_id, "triage",
                    f"Triage via {result.path} ({result.model or 'no LLM'}, {result.llm_calls} LLM call(s)): "
                    f"{result.card.likely_cause}", actor="DéjàVu", data={"latency_ms": result.latency_ms})
    await hub.broadcast("triage.ready", incident_json(inc))
    # Retain the alert *after* recall so the incident can't match itself as a "past" incident.
    await memory.retain(incident_id, f"{alert.get('severity')} {alert.get('signature')} on "
                                     f"{', '.join(alert.get('affected_services') or [])}, version {alert.get('version')}. "
                                     f"{alert.get('summary')}. Sample error: {alert.get('error_sample')}",
                        service=alert["service"], severity=alert["severity"], kind="alert", timestamp=inc.opened_at,
                        affected_services=alert.get("affected_services"), new_document=True)


# ---------------- incidents ----------------

@app.get("/incidents")
def list_incidents(status: str | None = None, source: str | None = None, limit: int = 50):
    with Session(engine) as session:
        q = select(Incident).order_by(col(Incident.opened_at).desc()).limit(limit)
        if status:
            q = q.where(Incident.status == status)
        if source:
            q = q.where(Incident.source == source)
        return [incident_json(i) for i in session.exec(q)]


@app.get("/incidents/{incident_id}")
def incident_detail(incident_id: str):
    inc = get_incident(incident_id)
    return {**incident_json(inc), "events": [e.model_dump(mode="json") for e in events_for(incident_id)]}


class ActionIn(BaseModel):
    action: str
    actor: str
    outcome: Literal["worked", "failed"] | None = None


@app.post("/incidents/{incident_id}/actions")
async def record_action(incident_id: str, body: ActionIn):
    inc = get_incident(incident_id)
    event = await add_event(incident_id, "action", body.action, actor=body.actor, outcome=body.outcome)
    await memory.retain(incident_id, body.action, service=inc.service, severity=inc.severity,
                        kind="action", outcome=body.outcome, actor=body.actor, timestamp=now(),
                        affected_services=inc.affected_services)
    return event


class DiagnosisIn(BaseModel):
    actor: str
    note: str | None = None  # defaults to the triage card's likely cause


@app.post("/incidents/{incident_id}/diagnosis")
async def confirm_diagnosis(incident_id: str, body: DiagnosisIn):
    inc = get_incident(incident_id)
    cause = body.note or ((inc.triage or {}).get("card") or {}).get("likely_cause") or "confirmed"
    inc.diagnosed_at = now()
    inc.time_to_diagnose_s = int((inc.diagnosed_at - inc.opened_at).total_seconds())
    inc = save(inc)
    await add_event(incident_id, "diagnosis", f"Diagnosis confirmed: {cause}", actor=body.actor)
    await memory.retain(incident_id, f"Diagnosis confirmed after {inc.time_to_diagnose_s}s: {cause}",
                        service=inc.service, severity=inc.severity, kind="finding", actor=body.actor,
                        timestamp=inc.diagnosed_at, affected_services=inc.affected_services)
    await hub.broadcast("incident.updated", incident_json(inc))
    return incident_json(inc)


class ResolveIn(BaseModel):
    actor: str
    root_cause: str | None = None  # default: the triage card's likely cause
    fix: str | None = None  # default: the action(s) marked "worked"


@app.post("/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str, body: ResolveIn):
    inc = get_incident(incident_id)
    if inc.status == "resolved":
        raise HTTPException(409, "already resolved")
    worked = [e.text for e in events_for(incident_id) if e.kind == "action" and e.outcome == "worked"]
    card = (inc.triage or {}).get("card") or {}
    inc.status, inc.resolved_at = "resolved", now()
    inc.time_to_resolve_s = int((inc.resolved_at - inc.opened_at).total_seconds())
    inc.root_cause = body.root_cause or card.get("likely_cause")
    inc.fix = body.fix or "; ".join(worked) or "; ".join(card.get("suggested_fix") or [])
    inc.owner = inc.owner or body.actor
    inc = save(inc)
    await add_event(incident_id, "resolved", f"Resolved by {body.actor}: {inc.fix}", actor=body.actor)
    await hub.broadcast("incident.updated", incident_json(inc))
    spawn(write_postmortem_draft(incident_id))
    return incident_json(inc)


async def write_postmortem_draft(incident_id: str) -> None:
    inc = get_incident(incident_id)
    draft, model = await draft_postmortem(inc, events_for(incident_id), llm)
    inc.postmortem_draft = draft
    inc = save(inc)
    await add_event(incident_id, "postmortem", f"Postmortem draft ready ({model or 'template'}); awaiting approval",
                    actor="DéjàVu")
    await hub.broadcast("postmortem.draft", incident_json(inc))


class ApproveIn(BaseModel):
    approver: str
    text: str | None = None  # the human-edited version; defaults to the draft as-is


@app.post("/incidents/{incident_id}/postmortem/approve")
async def approve_postmortem(incident_id: str, body: ApproveIn):
    inc = get_incident(incident_id)
    text = body.text or inc.postmortem_draft
    if not text:
        raise HTTPException(409, "no postmortem draft yet")
    inc.postmortem = text
    inc = save(inc)
    # Only the human-approved postmortem ever reaches long-term memory.
    await memory.retain(incident_id,
                        f"Approved postmortem (by {body.approver}). Root cause: {inc.root_cause}. Fix that worked: "
                        f"{inc.fix}. Owner: {inc.owner}. Time to resolve {human_duration(inc.time_to_resolve_s)}.\n\n{text}",
                        service=inc.service, severity=inc.severity, kind="postmortem", actor=body.approver,
                        timestamp=now(), affected_services=inc.affected_services)
    await add_event(incident_id, "postmortem", f"Postmortem approved by {body.approver} and retained to memory",
                    actor=body.approver)
    await hub.broadcast("incident.updated", incident_json(inc))
    spawn(refresh_runbooks(runbooks.for_services(inc.affected_services or [inc.service]),
                           f"{incident_id} postmortem approved", incident_id))
    return incident_json(inc)


# ---------------- runbooks (versioned, self-updating) ----------------

refresh_lock = asyncio.Lock()  # one refresh at a time; reflect calls are heavy


async def refresh_runbooks(runbook_ids: list[str], reason: str, incident_id: str | None = None) -> list[dict]:
    """Refresh runbooks after new knowledge lands. Short delay first so Hindsight has processed the retain."""
    await asyncio.sleep(3)
    results = []
    async with refresh_lock:
        for runbook_id in runbook_ids:
            await hub.broadcast("runbook.refreshing", {"id": runbook_id, "reason": reason})
            try:
                version = await runbooks.refresh(runbook_id, reason, memory)
            except Exception as e:  # noqa: BLE001
                log.exception("runbook refresh failed")
                results.append({"id": runbook_id, "error": str(e)})
                await hub.broadcast("runbook.unchanged", {"id": runbook_id, "reason": reason, "error": str(e)})
                continue
            if version is None:
                results.append({"id": runbook_id, "changed": False})
                await hub.broadcast("runbook.unchanged", {"id": runbook_id, "reason": reason})
                continue
            d = runbooks.diff(runbook_id)
            summary = {"id": runbook_id, "changed": True, "version": version.version, "reason": reason,
                       "added": d["added"], "removed": d["removed"]}
            results.append(summary)
            await hub.broadcast("runbook.updated", summary)
            if incident_id:
                await add_event(incident_id, "runbook", f"{runbooks.SPECS[runbook_id].name} updated to "
                                                        f"v{version.version} (+{d['added']} / -{d['removed']} lines)",
                                actor="DéjàVu")
    return results


@app.get("/runbooks")
def list_runbooks():
    return runbooks.summaries()


@app.get("/runbooks/{runbook_id}")
def runbook_detail(runbook_id: str):
    if runbook_id not in runbooks.SPECS:
        raise HTTPException(404, "unknown runbook")
    head = runbooks.latest(runbook_id)
    return {"id": runbook_id, "name": runbooks.SPECS[runbook_id].name,
            "latest": head.model_dump(mode="json") if head else None,
            "versions": [{k: v for k, v in r.model_dump(mode="json").items() if k != "content"}
                         for r in runbooks.history(runbook_id)]}


@app.get("/runbooks/{runbook_id}/history")
def runbook_history(runbook_id: str):
    return [r.model_dump(mode="json") for r in runbooks.history(runbook_id)]


@app.get("/runbooks/{runbook_id}/diff")
def runbook_diff(runbook_id: str, from_version: int | None = None, to_version: int | None = None):
    if runbook_id not in runbooks.SPECS:
        raise HTTPException(404, "unknown runbook")
    return runbooks.diff(runbook_id, from_version, to_version)


@app.post("/runbooks/{runbook_id}/refresh")
async def runbook_refresh(runbook_id: str):
    if runbook_id not in runbooks.SPECS:
        raise HTTPException(404, "unknown runbook")
    return (await refresh_runbooks([runbook_id], "manual refresh"))[0]


@app.post("/incidents/{incident_id}/compare")
async def compare_memory(incident_id: str, refresh: bool = False):
    """Memory ON vs OFF for the same alert. The OFF card costs one extra LLM call, so it's on demand + cached."""
    inc = get_incident(incident_id)
    if not inc.alert:
        raise HTTPException(409, "incident has no alert payload")
    if inc.triage_no_memory is None or refresh:
        result = await triage_without_memory(inc.alert, incident_id, llm)
        off = result.model_dump(mode="json")
        # Flag every Memory-OFF suggestion that already failed in a past incident ("This failed in INC-2034").
        off["failed_before"] = await flag_failed_suggestions(
            off["card"], (inc.triage or {}).get("card") or {}, inc.affected_services or [inc.service], memory)
        inc.triage_no_memory = off
        inc = save(inc)
    on_card = (inc.triage or {}).get("card") or {}
    off_card = inc.triage_no_memory["card"]
    return {"memory_on": inc.triage, "memory_off": inc.triage_no_memory,
            "memory_adds": memory_adds(on_card, off_card, inc.triage_no_memory.get("failed_before") or [])}


# ---------------- stats / status ----------------

@app.get("/stats/iq")
def agent_iq():
    """Time-to-correct-diagnosis per incident, oldest first (seed history, then live incidents)."""
    with Session(engine) as session:
        rows = session.exec(select(Incident).where(Incident.time_to_diagnose_s.is_not(None))
                            .order_by(Incident.opened_at))
        return [{"id": i.id, "opened_at": i.opened_at.isoformat(), "service": i.service, "source": i.source,
                 "memory_used": i.memory_used, "time_to_diagnose_s": i.time_to_diagnose_s,
                 "time_to_resolve_s": i.time_to_resolve_s} for i in rows]


@app.get("/llm/status")
def llm_status():
    return llm.status()


@app.get("/health")
def health():
    return {"status": "ok", "bank": memory.bank, "models": llm.models}


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # keepalive / ignore client messages
    except WebSocketDisconnect:
        hub.clients.discard(ws)
