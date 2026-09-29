"""Every Hindsight call DéjàVu makes lives here.

Layout of the memory bank:
- One bank (HINDSIGHT_BANK_ID) with an SRE reflect mission and maximum skepticism.
- One Hindsight *document* per incident: document_id = incident ID. The alert is retained first;
  each action/outcome, the resolution and the approved postmortem are appended to it.
- Tags/metadata are incident-level only (service:<name>, severity:<SEVn>, incident_id). Verified
  against Hindsight Cloud: with update_mode="append", metadata is per *document*, so the last
  append overwrites it for every event. Event kind and outcome therefore live in the text itself
  ("[INC-2034] ACTION (FAILED) by Priya Raman: ..."), which also survives into observations
  (consolidated facts have no document_id/metadata at all).
- Mental models: "Runbook: <service>" per service and a "Team expertise map", auto-refreshed
  in delta mode after consolidation, so their history shows how the runbook evolved.
"""
import asyncio
import logging
import re
from datetime import datetime
from typing import Any, Literal

from hindsight_client import Hindsight
from hindsight_client_api.exceptions import ApiException
from pydantic import BaseModel

from agent.config import get_settings

log = logging.getLogger("dejavu.memory")

REFLECT_MISSION = (
    "You are a senior SRE who has been on call for ShopLite for years. You track every incident: "
    "symptoms and error signatures, the confirmed root cause, the fix that actually resolved it, "
    "the fixes that were tried and did NOT work, how long it took, and which engineer has "
    "expertise with each service and failure mode. Be skeptical: only call something a root cause "
    "or a working fix when an incident record confirms it, and always say what failed before."
)
RETAIN_MISSION = (
    "Extract incident-response knowledge: affected service, error signature and symptoms, version "
    "deployed, confirmed root cause, each remediation action with who performed it and whether it "
    "worked or failed, time to detect/diagnose/resolve, and follow-up actions from postmortems."
)

Kind = Literal["alert", "action", "finding", "resolution", "postmortem"]
Outcome = Literal["worked", "failed"]

EXPERTISE_MODEL_ID = "team-expertise-map"


def runbook_id(service: str) -> str:
    return f"runbook-{service}"


SERVICES = ["catalog-api", "checkout-api", "export-api"]
RUNBOOK_FORMAT = (
    "Format: Markdown. Start with '# <title>'. One '## <failure mode>' section per failure mode, each with "
    "bullets: **Symptoms**, **Root cause**, **Fix that worked**, **Did NOT work**, **Expert**, "
    "**Seen in** (incident IDs with dates), **Typical time to resolve**."
)


class RunbookSpec(BaseModel):
    id: str
    name: str
    service: str | None  # None = cross-service (expertise map)
    query: str

    @property
    def tags(self) -> list[str] | None:
        return [f"service:{self.service}"] if self.service else None


RUNBOOKS = [
    RunbookSpec(
        id=runbook_id(s), name=f"Runbook: {s}", service=s,
        query=(f"Write the operational runbook for the {s} service. For each known failure mode give: "
               f"symptoms and error signatures, confirmed root cause, the fix that worked (step by step), "
               f"fixes that were tried and did NOT work (and why), the engineer who knows it best, and "
               f"typical time to resolve. Order failure modes by how often they recur. {RUNBOOK_FORMAT}"),
    )
    for s in SERVICES
] + [RunbookSpec(
    id=EXPERTISE_MODEL_ID, name="Team expertise map", service=None,
    query=("Build a map of engineer expertise from past incidents: for each engineer, the services and "
           "failure modes they have diagnosed or fixed, with incident IDs and dates as evidence, and who to "
           "page first for each service. Format: Markdown. Start with '# Team expertise map'. One "
           "'## <engineer>' section per engineer, then a '## Who to page' table (service | first | backup)."),
)]


class MemoryHit(BaseModel):
    text: str
    type: str | None = None
    incident_id: str | None = None
    occurred_at: str | None = None
    tags: list[str] = []
    metadata: dict[str, Any] = {}
    semantic: float = 0.0
    keyword: float = 0.0


class SimilarIncident(BaseModel):
    incident_id: str
    similarity: float  # best semantic score among its memories
    evidence: list[MemoryHit]


class Reflection(BaseModel):
    text: str
    structured: dict[str, Any] | None = None
    memories_used: int = 0  # how many memories the answer drew on
    incident_ids: list[str] = []  # past incidents those memories came from
    mental_models_used: list[str] = []


INCIDENT_ID = re.compile(r"\bINC-\d+\b")


def incident_ids_in(texts: list[str]) -> list[str]:
    """Incident IDs mentioned in memory texts, in order (observations carry no document_id)."""
    return list(dict.fromkeys(i for t in texts for i in INCIDENT_ID.findall(t or "")))


def incident_tags(services: list[str], severity: str) -> list[str]:
    return [*(f"service:{s}" for s in dict.fromkeys(services)), f"severity:{severity}"]


def event_text(incident_id: str, kind: Kind, text: str, *, outcome: Outcome | None = None,
               actor: str | None = None) -> str:
    """Self-describing memory text: '[INC-2034] ACTION (FAILED) by Priya Raman: restarted pods ...'."""
    label = kind.upper() + (f" ({'WORKED' if outcome == 'worked' else 'FAILED'})" if outcome else "")
    by = f" by {actor}" if actor else ""
    return f"[{incident_id}] {label}{by}: {text}"


async def _with_retry(call, *args, attempts: int = 3, **kwargs):
    """Hindsight Cloud occasionally returns a transient 5xx (e.g. on a freshly created bank)."""
    for attempt in range(attempts):
        try:
            return await call(*args, **kwargs)
        except ApiException as e:
            if (e.status or 0) < 500 or attempt == attempts - 1:
                raise
            delay = 2 ** attempt
            log.warning("hindsight %s failed with %s; retrying in %ss", call.__name__, e.status, delay)
            await asyncio.sleep(delay)


class IncidentMemory:
    def __init__(self) -> None:
        s = get_settings()
        self.bank = s.hindsight_bank_id
        self.client = Hindsight(base_url=s.hindsight_base_url, api_key=s.hindsight_api_key)

    async def close(self) -> None:
        await self.client.aclose()

    # ---------------- bank ----------------

    async def ensure_bank(self) -> None:
        await _with_retry(self.client.acreate_bank, self.bank, retain_mission=RETAIN_MISSION,
                          enable_observations=True)
        await _with_retry(
            self.client.aupdate_bank_config, self.bank,
            reflect_mission=REFLECT_MISSION,
            retain_mission=RETAIN_MISSION,
            disposition_skepticism=5,  # 1 = trusting, 5 = skeptical
            disposition_literalism=4,
            disposition_empathy=1,
            enable_observations=True,
            enable_auto_consolidation=True,  # consolidation is what triggers runbook refreshes
        )

    async def delete_bank(self) -> None:
        try:
            await self.client.adelete_bank(self.bank)
        except ApiException as e:
            # Hindsight Cloud answers 500 "has no manifest" (not 404) for a bank that was never written.
            if e.status != 404 and "no manifest" not in str(e.body):
                raise

    async def delete_incident(self, incident_id: str) -> None:
        """Remove one incident's document (and the facts extracted from it)."""
        try:
            await self.client.documents.delete_document(self.bank, incident_id)
        except ApiException as e:
            if e.status != 404:
                raise

    # ---------------- retain ----------------

    async def retain(self, incident_id: str, text: str, *, service: str, severity: str, kind: Kind,
                     timestamp: datetime, outcome: Outcome | None = None, actor: str | None = None,
                     affected_services: list[str] | None = None, new_document: bool = False) -> None:
        """Retain one event of an incident. The first event creates the document; later ones append."""
        await _with_retry(
            self.client.aretain, self.bank, event_text(incident_id, kind, text, outcome=outcome, actor=actor),
            timestamp=timestamp,
            context=f"{kind} for incident {incident_id} on {service}",
            document_id=incident_id,
            metadata={"incident_id": incident_id, "service": service, "severity": severity},
            tags=incident_tags([service, *(affected_services or [])], severity),
            update_mode=None if new_document else "append",
        )

    async def retain_incident_batch(self, incident_id: str, items: list[dict]) -> None:
        """Retain a whole (historical) incident in one call; items carry their own timestamp/tags."""
        await _with_retry(self.client.aretain_batch, self.bank, items, document_id=incident_id)

    # ---------------- recall / reflect ----------------

    async def recall_similar(self, query: str, services: list[str] | None = None,
                             max_tokens: int = 2048) -> list[MemoryHit]:
        tags = [f"service:{s}" for s in services] if services else None
        resp = await _with_retry(self.client.arecall, self.bank, query, max_tokens=max_tokens,
                                 budget="mid", tags=tags, tags_match="any")
        hits = []
        for r in resp.results:
            scores = r.scores.model_dump() if hasattr(r.scores, "model_dump") else (r.scores or {})
            ids = incident_ids_in([r.text])
            hits.append(MemoryHit(
                text=r.text, type=r.type, incident_id=r.document_id or (ids[0] if ids else None),
                occurred_at=r.occurred_start, tags=r.tags or [], metadata=r.metadata or {},
                semantic=scores.get("semantic") or 0.0, keyword=scores.get("keyword") or 0.0,
            ))
        return hits

    async def similar_incidents(self, query: str, services: list[str] | None = None, limit: int = 3,
                                exclude: set[str] | None = None) -> list[SimilarIncident]:
        """Past incidents most similar to `query`, best first.

        Hindsight's `final` score blends in recency (so a 2-day-old unrelated incident can outrank the
        real twin); for "have we seen this before?" we rank by semantic match, keyword as tie-break.
        """
        grouped: dict[str, list[MemoryHit]] = {}
        for hit in await self.recall_similar(query, services):
            if hit.incident_id and hit.incident_id not in (exclude or set()):
                grouped.setdefault(hit.incident_id, []).append(hit)
        ranked = sorted(grouped.items(),
                        key=lambda kv: max((h.semantic, h.keyword) for h in kv[1]), reverse=True)
        return [
            SimilarIncident(incident_id=iid, similarity=round(max(h.semantic for h in hits), 3),
                            evidence=sorted(hits, key=lambda h: h.semantic, reverse=True))
            for iid, hits in ranked[:limit]
        ]

    async def reflect(self, query: str, *, services: list[str] | None = None, context: str | None = None,
                      response_schema: dict | None = None, budget: str = "low") -> Reflection:
        tags = [f"service:{s}" for s in services] if services else None
        resp = await _with_retry(self.client.areflect, self.bank, query, budget=budget, context=context,
                                 response_schema=response_schema, tags=tags, tags_match="any",
                                 include_facts=True)
        memories = (resp.based_on.memories or []) if resp.based_on else []
        models = (resp.based_on.mental_models or []) if resp.based_on else []
        texts = [getattr(m, "text", None) or (m.get("text") if isinstance(m, dict) else "") for m in memories]
        return Reflection(
            text=resp.text or "",
            structured=resp.structured_output,
            memories_used=len(memories),
            incident_ids=incident_ids_in(texts),
            mental_models_used=[getattr(m, "id", None) or (m.get("id") if isinstance(m, dict) else "") for m in models],
        )

    # ---------------- mental models ----------------

    async def ensure_mental_models(self) -> list[str]:
        """Create any missing runbook / expertise-map mental models. Returns the IDs created."""
        existing = await _with_retry(self.client.alist_mental_models, self.bank)
        have = {m.id for m in existing.items}
        # tags_match "any": a tagged model otherwise defaults to all_strict and matches nothing
        trigger = {"mode": "delta", "refresh_after_consolidation": True, "tags_match": "any"}
        created = []
        for spec in RUNBOOKS:
            if spec.id in have:
                continue
            await _with_retry(self.client.acreate_mental_model, self.bank, name=spec.name, source_query=spec.query,
                              tags=spec.tags, trigger=trigger, id=spec.id)
            created.append(spec.id)
        return created

    async def list_mental_models(self) -> list:
        resp = await _with_retry(self.client.alist_mental_models, self.bank, detail="content")
        return resp.items

    async def get_mental_model(self, model_id: str):
        return await _with_retry(self.client.aget_mental_model, self.bank, model_id, detail="content")

    async def mental_model_history(self, model_id: str):
        return await _with_retry(self.client.aget_mental_model_history, self.bank, model_id)

    async def refresh_mental_model(self, model_id: str):
        return await _with_retry(self.client.arefresh_mental_model, self.bank, model_id)
