export type Severity = "SEV1" | "SEV2" | "SEV3"

export interface PastMatch {
  incident_id: string
  what_happened: string
  fix: string
  date: string | null
  similarity: number | null
}

export interface DontTry {
  action: string
  why: string
  incident_id: string | null
}

export interface TriageCard {
  likely_cause: string
  suggested_fix: string[]
  do_not_try: DontTry[]
  matched_incidents: PastMatch[]
  owner: { name: string; reason: string } | null
  confidence: number
  reasoning: string
}

export interface FailedBefore {
  index: number
  suggestion: string
  failed_in: string | null
  evidence: string
}

export interface TriageResult {
  card: TriageCard
  path: "gemini" | "hindsight-reflect" | "evidence-only" | "no-memory"
  model: string | null
  llm_calls: number
  repairs: number
  memory_used: boolean
  memories_used: number
  evidence_incidents: string[]
  latency_ms: number
  notes: string[]
  failed_before?: FailedBefore[]
}

export interface Incident {
  id: string
  service: string
  affected_services: string[]
  severity: Severity
  title: string
  signature: string | null
  status: "open" | "resolved"
  source: "live" | "seed"
  alert_id: string | null
  owner: string | null
  opened_at: string
  diagnosed_at: string | null
  recovered_at: string | null
  resolved_at: string | null
  time_to_diagnose_s: number | null
  time_to_resolve_s: number | null
  memory_used: boolean
  root_cause: string | null
  fix: string | null
  alert: Record<string, unknown> | null
  triage: TriageResult | null
  triage_no_memory: TriageResult | null
  postmortem_draft: string | null
  postmortem: string | null
}

export interface IncidentEvent {
  id: number
  incident_id: string
  at: string
  kind: string
  actor: string | null
  text: string
  outcome: string | null
}

export interface RunbookSummary {
  id: string
  name: string
  service: string | null
  versions: number
  version: number | null
  updated_at: string | null
  reason: string | null
  source: string | null
}

export interface RunbookVersion {
  id: number
  runbook_id: string
  version: number
  content: string
  created_at: string
  reason: string
  source: string
  incident_ids: string[]
}

export interface RunbookDiff {
  runbook_id: string
  from: number | null
  to: number | null
  lines: { op: " " | "+" | "-"; text: string }[]
  added: number
  removed: number
  to_reason?: string
}

export interface ChaosStatus {
  version: string
  leaked_db_connections: number
  leaked_memory_mb: number
  faults: { name: string; description: string; active: boolean; since: string | null }[]
}

export interface LlmCall {
  at: string
  label: string
  priority: number
  model: string
  status: string | number
  latency_ms: number
}

export interface IqPoint {
  id: string
  opened_at: string
  service: string
  source: "live" | "seed"
  memory_used: boolean
  time_to_diagnose_s: number
  time_to_resolve_s: number | null
}

export interface CompareResponse {
  memory_on: TriageResult
  memory_off: TriageResult
  memory_adds: {
    do_not_try: number
    matched_incidents: string[]
    owner: string | null
    off_suggestions_that_failed_before: number
    confidence_on: number
    confidence_off: number
  }
}

export type TriageStage = "recall" | "reflect" | "llm" | "fallback"
