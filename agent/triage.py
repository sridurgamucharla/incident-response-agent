"""Triage: retain -> recall -> reflect -> Gemini writes the card (with a fallback at every step).

Gemini budget per incident (free tier): the evidence is gathered from Hindsight first (no Gemini
quota), so a normal triage is ONE Gemini call. LLM_MAX_TRIAGE_CALLS (default 3) caps tool rounds and
repairs of malformed tool calls; after 2 failed repairs, or if Gemini is unavailable, we fall back to
Hindsight reflect with a JSON schema, and finally to a card built straight from the evidence.
"""
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable

from pydantic import BaseModel
from sqlmodel import Session, select

from agent.config import get_settings
from agent.llm import BACKGROUND_PRIORITY, SEVERITY_PRIORITY, GeminiClient, LLMUnavailable
from agent.memory import IncidentMemory, SimilarIncident
from agent.models import Incident, IncidentEvent, engine
from agent.tools import (AskMemory, DontTry, Owner, PastMatch, SearchPastIncidents, ToolCallError,
                         TriageCard, json_schema, openai_tools, parse_tool_call)

log = logging.getLogger("dejavu.triage")

MAX_REPAIRS = 2
# a remediation that failed ("attempted X but it failed", "ineffective", "did not help") - not just any
# sentence containing "failed" (e.g. "every failed report leaked a connection")
FAILED_WORDING = re.compile(
    r"\(FAILED\)|\b(?:attempt\w*|tried|tries)\b[^.]*\b(?:fail\w*|ineffective|did not|didn't)"
    r"|\bineffective\b|\bdid not (?:help|work|resolve|fix)|\bfailed to (?:resolve|fix|address|help)"
    r"|\bonly bought\b|\bmade (?:it|things) worse\b", re.I)

SYSTEM_WITH_MEMORY = """You are DéjàVu, an incident-response agent for the ShopLite store.
You are given a live alert plus EVIDENCE from the team's incident memory: similar past incidents,
what fixed them, what was tried and did NOT work, and who fixed them.

Write a triage card for the on-call engineer:
- Ground every claim in the evidence. Cite past incidents by their exact IDs (e.g. INC-2034).
- Put anything that failed before into do_not_try, with the incident where it failed.
- suggested_fix: concrete ordered steps that worked before, adapted to this alert.
- owner: the engineer who fixed the matching incidents.
- confidence: high (>0.8) only if a past incident clearly matches the signature; low if nothing matches.
You may call search_past_incidents or ask_memory if the evidence is not enough, then call
submit_triage exactly once. Always answer by calling a tool."""

SYSTEM_NO_MEMORY = """You are an incident-response assistant for the ShopLite store. You have NO access
to past incidents or team history - only the live alert. Give your best generic triage and call
submit_triage exactly once. Leave matched_incidents empty and owner null (you know no engineers)."""


class TriageResult(BaseModel):
    card: TriageCard
    path: str  # gemini | hindsight-reflect | evidence-only | no-memory
    model: str | None  # which model produced the card
    llm_calls: int = 0
    repairs: int = 0
    memory_used: bool
    memories_used: int = 0
    evidence_incidents: list[str] = []
    latency_ms: int = 0
    notes: list[str] = []


# ---------------- evidence ----------------

def alert_query(alert: dict) -> str:
    return f"{alert.get('signature')}: {alert.get('error_sample') or ''} on {', '.join(alert.get('affected_services') or [])}"


def describe_alert(alert: dict) -> str:
    return (f"LIVE ALERT {alert.get('severity')} on {', '.join(alert.get('affected_services') or [])} "
            f"(primary {alert.get('service')}), version {alert.get('version')}\n"
            f"Error signature: {alert.get('signature')}\n"
            f"Sample error: {alert.get('error_sample')}\n"
            f"Metrics: {alert.get('summary')}\n"
            f"DB pool connections in use (max): {alert.get('db_pool_in_use_max')}\n"
            f"Stack:\n{(alert.get('stack_sample') or '')[-800:]}")


def past_records(ids: list[str]) -> dict[str, Incident]:
    if not ids:
        return {}
    with Session(engine) as session:
        return {i.id: i for i in session.exec(select(Incident).where(Incident.id.in_(ids)))}


def render_evidence(similar: list[SimilarIncident], records: dict[str, Incident], reflection: str | None) -> str:
    parts = []
    for s in similar:
        r = records.get(s.incident_id)
        head = f"### {s.incident_id} (similarity {s.similarity})"
        if r:
            head += (f" - {r.title} - {r.opened_at:%Y-%m-%d} - {r.severity} - owner {r.owner}\n"
                     f"Root cause: {r.root_cause}\nFix that worked: {r.fix}")
        facts = "\n".join(f"- {h.text}" for h in s.evidence[:8])
        parts.append(f"{head}\nMemories:\n{facts}")
    if reflection:
        parts.append(f"### Incident memory's own analysis (Hindsight reflect)\n{reflection}")
    return "\n\n".join(parts) or "No similar past incidents were found."


def finish_card(card: TriageCard, similar: list[SimilarIncident], records: dict[str, Incident]) -> TriageCard:
    """Fill system-owned fields (dates, similarity) from our data, never from the model."""
    sim = {s.incident_id: s.similarity for s in similar}
    for m in card.matched_incidents:
        r = records.get(m.incident_id)
        m.date = r.opened_at.date().isoformat() if r else None
        m.similarity = sim.get(m.incident_id)
    return card


def evidence_only_card(similar: list[SimilarIncident], records: dict[str, Incident]) -> TriageCard:
    """Last resort: no LLM at all, straight from recall + the incident records."""
    if not similar:
        return TriageCard(likely_cause="No similar past incident found in memory.",
                          suggested_fix=["Investigate from the error sample and stack trace."],
                          confidence=0.1, reasoning="Incident memory had no match for this signature.")
    top = similar[0]
    rec = records.get(top.incident_id)
    failed = []
    for s in similar:
        for h in s.evidence:
            # recall returns Hindsight's extracted facts ("... re-ran the export job, but it failed"),
            # so match the wording, not only our own "(FAILED)" label
            if FAILED_WORDING.search(h.text):
                failed.append(DontTry(action=h.text.split(" | When:")[0][:200],
                                      why="Tried before and it did not resolve the incident", incident_id=s.incident_id))
    return TriageCard(
        likely_cause=rec.root_cause if rec and rec.root_cause else top.evidence[0].text,
        suggested_fix=[rec.fix] if rec and rec.fix else [h.text for h in top.evidence[:2]],
        do_not_try=failed[:4],
        matched_incidents=[PastMatch(incident_id=s.incident_id, what_happened=(records[s.incident_id].title
                           if s.incident_id in records else s.evidence[0].text[:120]),
                           fix=(records[s.incident_id].fix or "") if s.incident_id in records else "")
                           for s in similar],
        owner=Owner(name=rec.owner, reason=f"Resolved {top.incident_id}") if rec and rec.owner else None,
        confidence=round(min(top.similarity, 0.9), 2),
        reasoning=f"Built without an LLM from the closest past incident {top.incident_id} "
                  f"(similarity {top.similarity}).",
    )


# ---------------- the agent loop ----------------

async def run_tool_loop(llm: GeminiClient, memory: IncidentMemory | None, messages: list, *, tool_names: list[str],
                        known: set[str], priority: int, label: str, result: TriageResult,
                        similar: list[SimilarIncident]) -> TriageCard | None:
    """Gemini tool-calling loop with Pydantic validation. Returns the card or None (give up)."""
    tools = openai_tools(tool_names)
    max_calls = get_settings().llm_max_triage_calls
    while result.llm_calls < max_calls:
        reply = await llm.chat(messages, priority=priority, tools=tools, tool_choice="required", label=label)
        result.llm_calls += 1
        result.model = reply.model
        msg = reply.message
        messages.append(msg.model_dump(exclude_none=True))  # keeps Gemini thought signatures intact

        if not msg.tool_calls:
            result.repairs += 1
            if result.repairs > MAX_REPAIRS:
                return None
            messages.append({"role": "user", "content": "You must answer by calling a tool (submit_triage when done)."})
            continue

        card, malformed = None, False
        for call in msg.tool_calls:
            try:
                name, args = parse_tool_call(call, tool_names, known)
            except ToolCallError as e:
                malformed = True
                result.notes.append(f"repair: {e}")
                messages.append({"role": "tool", "tool_call_id": call.id, "content": f"ERROR: {e}"})
                continue
            if isinstance(args, TriageCard):
                card = args
                content = "Triage card accepted."
            elif isinstance(args, SearchPastIncidents) and memory:
                found = await memory.similar_incidents(args.query, [args.service] if args.service else None)
                known.update(s.incident_id for s in found)
                similar.extend(s for s in found if s.incident_id not in {x.incident_id for x in similar})
                content = render_evidence(found, past_records([s.incident_id for s in found]), None)
            elif isinstance(args, AskMemory) and memory:
                answer = await memory.reflect(args.question, services=[args.service] if args.service else None)
                known.update(answer.incident_ids)
                content = answer.text
            else:
                content = "This tool is not available."
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content})

        if card:
            return card
        if malformed:
            result.repairs += 1
            if result.repairs > MAX_REPAIRS:
                return None
    return None


async def _no_progress(_: str) -> None:
    return None


async def triage_with_memory(alert: dict, incident_id: str, memory: IncidentMemory, llm: GeminiClient,
                             progress: Callable[[str], Awaitable[None]] = _no_progress) -> TriageResult:
    """progress(stage) is awaited at each step: recall -> reflect -> llm [-> fallback] (for the UI)."""
    start = time.monotonic()
    services = alert.get("affected_services") or [alert.get("service")]
    await progress("recall")
    similar = await memory.similar_incidents(alert_query(alert), services, limit=3, exclude={incident_id})
    await progress("reflect")
    records = past_records([s.incident_id for s in similar])
    reflection = None
    try:
        r = await memory.reflect(
            f"{describe_alert(alert)}\n\nBased on past incidents: what is the likely root cause, what fixed it, "
            f"what was tried and did NOT work, and who should be paged?", services=services)
        reflection, memories_used = r.text, r.memories_used
    except Exception as e:  # noqa: BLE001 - reflect is evidence, not a hard dependency
        log.warning("reflect failed: %s", e)
        memories_used = 0

    known = {s.incident_id for s in similar}
    result = TriageResult(card=TriageCard(likely_cause="", suggested_fix=["-"], confidence=0, reasoning=""),
                          path="gemini", model=None, memory_used=bool(similar), memories_used=memories_used,
                          evidence_incidents=sorted(known))
    messages = [
        {"role": "system", "content": SYSTEM_WITH_MEMORY},
        {"role": "user", "content": f"{describe_alert(alert)}\n\nEVIDENCE FROM INCIDENT MEMORY:\n"
                                    f"{render_evidence(similar, records, reflection)}"},
    ]
    card = None
    await progress("llm")
    try:
        card = await run_tool_loop(llm, memory, messages, tool_names=list(("search_past_incidents", "ask_memory",
                                   "submit_triage")), known=known, priority=SEVERITY_PRIORITY.get(alert.get("severity"), 3),
                                   label=f"triage {incident_id}", result=result, similar=similar)
        if card is None:
            result.notes.append("Gemini did not produce a valid card; falling back to Hindsight reflect")
    except LLMUnavailable as e:
        result.notes.append(f"Gemini unavailable ({e}); falling back to Hindsight reflect")

    records = past_records([s.incident_id for s in similar])
    if card is None:
        await progress("fallback")
        card = await reflect_card(alert, services, memory, result)
    if card is None:
        result.path, result.model = "evidence-only", None
        card = evidence_only_card(similar, records)

    result.card = finish_card(card, similar, records)
    result.latency_ms = int((time.monotonic() - start) * 1000)
    return result


async def reflect_card(alert: dict, services: list[str], memory: IncidentMemory, result: TriageResult) -> TriageCard | None:
    """No-Gemini path: Hindsight reflect (its own LLM) fills the triage card JSON schema."""
    try:
        r = await memory.reflect(
            f"{describe_alert(alert)}\n\nWrite a triage card for this alert using past incidents: likely cause, "
            f"fix steps that worked before, things that failed before, similar incidents, who to page, confidence.",
            services=services, response_schema=json_schema(TriageCard))
        if not r.structured:
            return None
        result.path, result.model = "hindsight-reflect", "hindsight"
        card = TriageCard.model_validate(r.structured)
        known = set(r.incident_ids) | set(result.evidence_incidents)
        card.matched_incidents = [m for m in card.matched_incidents if m.incident_id in known]
        return card
    except Exception as e:  # noqa: BLE001
        result.notes.append(f"Hindsight reflect fallback failed: {e}")
        return None


async def triage_without_memory(alert: dict, incident_id: str, llm: GeminiClient) -> TriageResult:
    """The 'Memory OFF' side of the comparison: same alert, same model, no incident history."""
    start = time.monotonic()
    result = TriageResult(card=TriageCard(likely_cause="", suggested_fix=["-"], confidence=0, reasoning=""),
                          path="no-memory", model=None, memory_used=False)
    messages = [{"role": "system", "content": SYSTEM_NO_MEMORY}, {"role": "user", "content": describe_alert(alert)}]
    card = None
    try:
        card = await run_tool_loop(llm, None, messages, tool_names=["submit_triage"], known=set(),
                                   priority=BACKGROUND_PRIORITY, label=f"memory-off {incident_id}",
                                   result=result, similar=[])
    except LLMUnavailable as e:
        result.notes.append(f"Gemini unavailable: {e}")
    result.card = card or TriageCard(
        likely_cause="Unknown - no incident history available.",
        suggested_fix=["Check recent deploys", "Restart the affected service", "Scale up and watch error rates"],
        confidence=0.2, reasoning="Generic runbook steps; without memory there is nothing specific to go on.")
    result.latency_ms = int((time.monotonic() - start) * 1000)
    return result


# ---------------- memory ON vs OFF ----------------

STOPWORDS = set("the a an and or to of in on for with by from it its is be as at this that then if any all "
                "check verify ensure use using service services".split())


# ops verbs that mean the same remediation ("raise the pool" == "increase the pool")
SYNONYMS = {"raise": "increase", "bump": "increase", "enlarge": "increase", "grow": "increase", "bigger": "increase",
            "larger": "increase", "rais": "increase", "more": "increase", "reboot": "restart", "bounce": "restart", "recycle": "restart",
            "redeploy": "restart", "retry": "retry", "rerun": "retry", "re": "retry"}


def _stem(word: str) -> str:
    for suffix, keep in (("ing", 5), ("ed", 4), ("es", 4), ("s", 3)):
        if word.endswith(suffix) and len(word) > keep:
            word = word[: -len(suffix)]
            break
    return SYNONYMS.get(word, word)


def _words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOPWORDS}


def _overlap(a: str, b: str) -> float:
    """Share of the shorter text's meaningful words found in the other; 0 unless >= 2 words are shared."""
    wa, wb = _words(a), _words(b)
    shared = len(wa & wb)
    return shared / max(1, min(len(wa), len(wb))) if shared >= 2 else 0.0


async def flag_failed_suggestions(off_card: dict, on_card: dict, services: list[str],
                                  memory: IncidentMemory) -> list[dict]:
    """For each step the Memory-OFF card suggests, find evidence that it already FAILED in a past incident.

    1) compare with the Memory-ON card's do_not_try list (word overlap);
    2) otherwise ask recall for failed attempts like it (Hindsight only, no Gemini call).
    """
    flags = []
    for index, suggestion in enumerate(off_card.get("suggested_fix") or []):
        match = max(((d, _overlap(suggestion, d["action"])) for d in on_card.get("do_not_try") or []),
                    key=lambda x: x[1], default=(None, 0.0))
        if match[0] and match[1] >= 0.4:
            flags.append({"index": index, "suggestion": suggestion, "failed_in": match[0].get("incident_id"),
                          "evidence": f"{match[0]['action']} - {match[0]['why']}"})
            continue
        hits = await memory.recall_similar(f"tried to {suggestion} but it failed", services, max_tokens=1024)
        failed = [h for h in hits if FAILED_WORDING.search(h.text) and h.semantic >= 0.72
                  and _overlap(suggestion, h.text) >= 0.3]
        if failed:
            best = max(failed, key=lambda h: h.semantic)
            flags.append({"index": index, "suggestion": suggestion, "failed_in": best.incident_id,
                          "evidence": best.text.split(" | When:")[0]})
    return flags


def memory_adds(on_card: dict, off_card: dict, flags: list[dict]) -> dict:
    """What memory contributed that the no-memory card could not know - for the dashboard to highlight."""
    return {
        "do_not_try": len(on_card.get("do_not_try") or []),
        "matched_incidents": [m["incident_id"] for m in on_card.get("matched_incidents") or []],
        "owner": (on_card.get("owner") or {}).get("name"),
        "off_suggestions_that_failed_before": len(flags),
        "confidence_on": on_card.get("confidence"),
        "confidence_off": off_card.get("confidence"),
    }


# ---------------- postmortem ----------------

def human_duration(seconds: int | None) -> str:
    seconds = seconds or 0
    return f"{seconds} s" if seconds < 90 else f"{round(seconds / 60)} min"


def timeline_text(events: list[IncidentEvent]) -> str:
    return "\n".join(f"- {e.at:%H:%M:%S} [{e.kind}{'/' + e.outcome if e.outcome else ''}]"
                     f"{' ' + e.actor if e.actor else ''}: {e.text}" for e in events)


async def draft_postmortem(incident: Incident, events: list[IncidentEvent], llm: GeminiClient) -> tuple[str, str | None]:
    """Returns (markdown draft, model used or None for the template fallback)."""
    facts = (f"Incident {incident.id}: {incident.title}\nSeverity {incident.severity}; services "
             f"{', '.join(incident.affected_services)}\nOpened {incident.opened_at:%Y-%m-%d %H:%M} UTC; resolved "
             f"{incident.resolved_at:%H:%M} UTC; time to resolve {human_duration(incident.time_to_resolve_s)}\n"
             f"Root cause: {incident.root_cause}\nFix: {incident.fix}\nOwner: {incident.owner}\n"
             f"Triage card: {json.dumps((incident.triage or {}).get('card', {}))[:1500]}\n\nTimeline:\n{timeline_text(events)}")
    try:
        reply = await llm.chat([
            {"role": "system", "content": "Write a concise blameless postmortem in Markdown with sections: Summary, "
                                          "Impact, Timeline, Root cause, What worked, What did NOT work, Action items. "
                                          "Use only the facts given. Under 350 words."},
            {"role": "user", "content": facts},
        ], priority=BACKGROUND_PRIORITY, label=f"postmortem {incident.id}")
        if reply.message.content:
            return reply.message.content.strip(), reply.model
    except LLMUnavailable as e:
        log.warning("postmortem draft falling back to template: %s", e)
    worked = [e.text for e in events if e.kind == "action" and e.outcome == "worked"]
    failed = [e.text for e in events if e.kind == "action" and e.outcome == "failed"]
    return (f"# Postmortem: {incident.title} ({incident.id})\n\n## Summary\n{incident.severity} on "
            f"{', '.join(incident.affected_services)}, resolved in {human_duration(incident.time_to_resolve_s)}.\n\n"
            f"## Root cause\n{incident.root_cause}\n\n## What worked\n" + "\n".join(f"- {w}" for w in worked or [incident.fix or "-"]) +
            "\n\n## What did NOT work\n" + "\n".join(f"- {f}" for f in failed or ["-"]) +
            f"\n\n## Timeline\n{timeline_text(events)}\n\n## Action items\n- \n"), None
