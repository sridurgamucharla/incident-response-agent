"""Tools the triage LLM can call, and strict Pydantic validation of every call it makes.

A malformed call (bad JSON, missing fields, unknown tool, an incident ID that isn't in the
evidence) raises ToolCallError; the agent sends that message back to Gemini and lets it retry.
"""
import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


class ToolCallError(Exception):
    """Human-readable reason a tool call was rejected; sent back to the model verbatim."""


# ---------------- the triage card ----------------

class PastMatch(BaseModel):
    incident_id: str = Field(description="Past incident ID exactly as it appears in the evidence, e.g. INC-2034")
    what_happened: str = Field(description="One sentence: what that incident was")
    fix: str = Field(description="What fixed it back then")
    date: str | None = Field(default=None, description="Filled in by the system; leave empty")
    similarity: float | None = Field(default=None, description="Filled in by the system; leave empty")


class DontTry(BaseModel):
    action: str = Field(description="A remediation that was tried before and did NOT work")
    why: str = Field(description="Why it failed")
    incident_id: str | None = Field(default=None, description="Incident where it failed")


class Owner(BaseModel):
    name: str = Field(description="Engineer to page, from the evidence")
    reason: str = Field(description="Why them, e.g. fixed INC-2034 and INC-2044")


class TriageCard(BaseModel):
    likely_cause: str = Field(description="Most likely root cause, grounded in past incidents")
    suggested_fix: list[str] = Field(min_length=1, description="Ordered remediation steps")
    do_not_try: list[DontTry] = Field(default_factory=list, description="Things that failed before")
    matched_incidents: list[PastMatch] = Field(default_factory=list, description="Most similar past incidents, best first")
    owner: Owner | None = Field(default=None, description="Who to page, if the evidence shows an expert")
    confidence: float = Field(ge=0, le=1, description="0-1. High only when a past incident clearly matches")
    reasoning: str = Field(description="Two or three sentences explaining the diagnosis")

    @field_validator("confidence", mode="before")
    @classmethod
    def percent_to_fraction(cls, v: Any) -> Any:
        # models sometimes answer 85 instead of 0.85
        return v / 100 if isinstance(v, (int, float)) and 1 < v <= 100 else v

    @field_validator("owner", mode="before")
    @classmethod
    def owner_from_string(cls, v: Any) -> Any:
        # "Diego Alvarez (handled INC-2040)" -> {"name": "Diego Alvarez", "reason": "handled INC-2040"}
        if isinstance(v, str):
            name, _, reason = v.partition("(")
            return {"name": name.strip(), "reason": reason.rstrip(")").strip() or "Resolved similar incidents"}
        return v

    @field_validator("suggested_fix", mode="before")
    @classmethod
    def fix_from_string(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v


# ---------------- research tools ----------------

class SearchPastIncidents(BaseModel):
    query: str = Field(min_length=3, description="Symptoms or error signature to search for")
    service: str | None = Field(default=None, description="Restrict to one service, e.g. checkout-api")


class AskMemory(BaseModel):
    question: str = Field(min_length=5, description="A question for the team's incident memory")
    service: str | None = Field(default=None, description="Restrict to one service")


TOOLS: dict[str, tuple[type[BaseModel], str]] = {
    "search_past_incidents": (SearchPastIncidents,
                              "Search incident memory for past incidents similar to a symptom or error."),
    "ask_memory": (AskMemory,
                   "Ask the incident memory a question, e.g. 'what did NOT work for pool exhaustion?'."),
    "submit_triage": (TriageCard,
                      "Submit the final triage card. Call this exactly once when you are done."),
}


def _inline_refs(schema: dict) -> dict:
    """Gemini's OpenAI-compatible endpoint does not follow $ref, so inline $defs."""
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def json_schema(model: type[BaseModel]) -> dict:
    return _inline_refs(model.model_json_schema())


def openai_tools(names: list[str]) -> list[dict]:
    return [
        {"type": "function",
         "function": {"name": n, "description": TOOLS[n][1], "parameters": json_schema(TOOLS[n][0])}}
        for n in names
    ]


def parse_tool_call(tool_call: Any, allowed: list[str], known_incidents: set[str]) -> tuple[str, BaseModel]:
    """Validate one tool call from the model. Raises ToolCallError with a fixable message."""
    name = tool_call.function.name
    if name not in allowed:
        raise ToolCallError(f"Unknown tool '{name}'. Available tools: {', '.join(allowed)}.")
    try:
        raw = json.loads(tool_call.function.arguments or "{}")
    except json.JSONDecodeError as e:
        raise ToolCallError(f"Arguments for {name} are not valid JSON ({e.msg}). Send a JSON object.") from None
    try:
        args = TOOLS[name][0].model_validate(raw)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'arguments'}: {err['msg']}" for err in e.errors())
        raise ToolCallError(f"Invalid arguments for {name}: {problems}. Fix them and call {name} again.") from None

    if isinstance(args, TriageCard):
        cited = {m.incident_id for m in args.matched_incidents} | {d.incident_id for d in args.do_not_try if d.incident_id}
        unknown = sorted(cited - known_incidents)
        if unknown:
            raise ToolCallError(
                f"These incident IDs are not in the evidence: {', '.join(unknown)}. Only cite incidents that "
                f"appear in the evidence ({', '.join(sorted(known_incidents)) or 'none'}), or leave them out.")
    return name, args
