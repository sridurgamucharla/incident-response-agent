"""Self-updating runbooks with version history and diffs.

Source of each version, in order of preference:
1. The Hindsight mental model for that runbook (delta mode, auto-refresh) - used whenever it has content.
2. Hindsight reflect with the same source query, scoped to the service, given the current version and told
   to edit it in place (keep unchanged sections verbatim). This mimics delta mode, so diffs stay small.
   (Hindsight Cloud mental models return "nothing readable in scope" on our account; see README.)
Versions live in dejavu_db (runbook_versions); a new version is stored only when the content changed.
"""
import difflib
import logging

from sqlmodel import Session, col, func, select

from agent.memory import RUNBOOKS, IncidentMemory, RunbookSpec
from agent.models import RunbookVersion, engine

log = logging.getLogger("dejavu.runbooks")

SPECS = {spec.id: spec for spec in RUNBOOKS}
PLACEHOLDER = "Generating content"

UPDATE_INSTRUCTIONS = (
    "Below is the CURRENT version of this document. Make the smallest possible edit, like a careful reviewer "
    "editing a wiki page:\n"
    "- Keep every section in its current ORDER. Append sections for brand-new failure modes at the end.\n"
    "- Copy every line that is still correct EXACTLY, character for character. Do not rephrase, reformat "
    "or re-round numbers.\n"
    "- Only change lines that newer incidents change: add new incident IDs to 'Seen in', new failed attempts "
    "to 'Did NOT work', faster fixes, new experts, updated time to resolve.\n"
    "- If nothing new is relevant, return the document unchanged.\n"
    "Return the complete document in the same Markdown format, with no preamble.\n\n"
    "CURRENT VERSION:\n"
)


def latest(runbook_id: str) -> RunbookVersion | None:
    with Session(engine) as session:
        return session.exec(select(RunbookVersion).where(RunbookVersion.runbook_id == runbook_id)
                            .order_by(col(RunbookVersion.version).desc())).first()


def history(runbook_id: str) -> list[RunbookVersion]:
    with Session(engine) as session:
        return list(session.exec(select(RunbookVersion).where(RunbookVersion.runbook_id == runbook_id)
                                 .order_by(RunbookVersion.version)))


def summaries() -> list[dict]:
    with Session(engine) as session:
        counts = dict(session.exec(select(RunbookVersion.runbook_id, func.count())
                                   .group_by(RunbookVersion.runbook_id)).all())
    out = []
    for spec in RUNBOOKS:
        head = latest(spec.id)
        out.append({"id": spec.id, "name": spec.name, "service": spec.service, "versions": counts.get(spec.id, 0),
                    "version": head.version if head else None,
                    "updated_at": head.created_at.isoformat() if head else None,
                    "reason": head.reason if head else None, "source": head.source if head else None})
    return out


def _clean(markdown: str) -> str:
    text = markdown.strip()
    if text.startswith("```"):  # some answers wrap the whole document in a code fence
        text = text.strip("`").removeprefix("markdown").strip()
    return text


async def _generate(spec: RunbookSpec, current: RunbookVersion | None, memory: IncidentMemory) -> tuple[str, str, list[str], int]:
    """Returns (content, source, incident_ids, memories_used)."""
    try:
        model = await memory.get_mental_model(spec.id)
        content = (model.content or "").strip()
        if len(content) > 200 and PLACEHOLDER not in content:
            return content, "hindsight-mental-model", [], 0
    except Exception as e:  # noqa: BLE001 - fall through to reflect
        log.info("mental model %s unavailable: %s", spec.id, e)

    context = f"{UPDATE_INSTRUCTIONS}{current.content}" if current else None
    answer = await memory.reflect(spec.query, services=[spec.service] if spec.service else None,
                                  context=context, budget="mid")
    return _clean(answer.text), "hindsight-reflect", answer.incident_ids, answer.memories_used


async def refresh(runbook_id: str, reason: str, memory: IncidentMemory) -> RunbookVersion | None:
    """Build the next version. Returns it, or None when nothing changed."""
    spec = SPECS[runbook_id]
    current = latest(runbook_id)
    content, source, incident_ids, memories_used = await _generate(spec, current, memory)
    if not content or (current and content.strip() == current.content.strip()):
        return None
    version = RunbookVersion(runbook_id=runbook_id, version=(current.version + 1) if current else 1,
                             content=content, reason=reason, source=source, incident_ids=incident_ids,
                             memories_used=memories_used)
    with Session(engine) as session:
        session.add(version)
        session.commit()
        session.refresh(version)
    if spec.service:
        # keep the real mental model in sync too, so it self-heals if Hindsight starts serving it
        try:
            await memory.refresh_mental_model(spec.id)
        except Exception:  # noqa: BLE001
            pass
    return version


def for_services(services: list[str]) -> list[str]:
    """Runbooks to refresh after an incident on these services (plus the expertise map)."""
    return [s.id for s in RUNBOOKS if s.service in services or s.service is None]


def diff(runbook_id: str, from_version: int | None = None, to_version: int | None = None) -> dict:
    versions = {v.version: v for v in history(runbook_id)}
    if not versions:
        return {"runbook_id": runbook_id, "from": None, "to": None, "lines": [], "added": 0, "removed": 0}
    to_v = to_version or max(versions)
    from_v = from_version or max([v for v in versions if v < to_v], default=to_v)
    a, b = versions[from_v].content.splitlines(), versions[to_v].content.splitlines()
    lines = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            lines += [{"op": " ", "text": t} for t in a[i1:i2]]
        if op in ("delete", "replace"):
            lines += [{"op": "-", "text": t} for t in a[i1:i2]]
        if op in ("insert", "replace"):
            lines += [{"op": "+", "text": t} for t in b[j1:j2]]
    return {"runbook_id": runbook_id, "from": from_v, "to": to_v, "lines": lines,
            "added": sum(1 for x in lines if x["op"] == "+"), "removed": sum(1 for x in lines if x["op"] == "-"),
            "to_reason": versions[to_v].reason}
