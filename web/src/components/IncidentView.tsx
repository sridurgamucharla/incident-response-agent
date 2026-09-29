import { ArrowRight, BookOpen, Check, CheckCircle2, ClipboardCheck, FileText, GitCompare, Pencil, ScrollText, Stethoscope, ThumbsDown, ThumbsUp } from "lucide-react"
import { type Dispatch, useEffect, useState } from "react"

import { Panel, SeverityBadge, StatusBadge, Working } from "@/components/common"
import { DiffStats, DiffView } from "@/components/DiffView"
import { Markdown } from "@/components/Markdown"
import { TriageCardView } from "@/components/TriageCard"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { clock, duration, runbookName } from "@/lib/format"
import type { Action, State } from "@/lib/store"
import type { Incident, IncidentEvent, TriageStage } from "@/lib/types"
import { cn } from "@/lib/utils"

const TRIAGE_STEPS = [
  { key: "recall", label: "Recalling similar past incidents (Hindsight recall)" },
  { key: "reflect", label: "Asking the team's memory what worked and what failed (Hindsight reflect)" },
  { key: "llm", label: "Gemini is writing the triage card" },
  { key: "fallback", label: "Gemini unavailable — building the card from memory" },
]

function useActor(): [string, (v: string) => void] {
  const [actor, setActor] = useState(() => {
    try { return localStorage.getItem("dejavu.actor") || "On-call engineer" } catch { return "On-call engineer" }
  })
  return [actor, (v: string) => { setActor(v); try { localStorage.setItem("dejavu.actor", v) } catch { /* ignore */ } }]
}

export function IncidentView({ incident, events, stage, state, dispatch, onOpenRunbooks, onCompare }: {
  incident: Incident
  events: IncidentEvent[]
  stage?: TriageStage
  state: State
  dispatch: Dispatch<Action>
  onOpenRunbooks: () => void
  onCompare: () => void
}) {
  const [actor, setActor] = useActor()
  const [busy, setBusy] = useState<string | null>(null)
  const alert = (incident.alert ?? {}) as Record<string, string>

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key)
    try {
      const out = await fn()
      if (out && typeof out === "object" && "id" in out && "status" in out) dispatch({ type: "upsert", incident: out as Incident })
    } finally { setBusy(null) }
  }

  if (incident.source === "seed") return <SeedIncident incident={incident} />

  return (
    <div className="flex flex-col gap-4 pb-8">
      {/* header */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-2xl font-bold">{incident.id}</span>
        <SeverityBadge severity={incident.severity} />
        <StatusBadge status={incident.status} />
        <span className="text-2xl font-semibold">{incident.title}</span>
        <span className="ml-auto text-base text-muted-foreground">
          opened {clock(incident.opened_at)} · {incident.affected_services.join(", ")} · {String(alert.version ?? "")}
        </span>
      </div>
      {alert.error_sample && (
        <div className="rounded-lg border bg-card px-4 py-2.5 font-mono text-base">
          <span className="text-muted-foreground">{alert.summary}</span>
          <div style={{ color: "var(--status-serious)" }}>{alert.error_sample}</div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Lifecycle incident={incident} state={state} />
        <NextStep incident={incident} actor={actor} busy={busy} act={act} />
      </div>

      {/* triage */}
      <Panel title="Triage card" icon={<Stethoscope className="size-4" />}
             right={incident.triage && (
               <Button variant="outline" onClick={onCompare} className="h-9 gap-2 text-base">
                 <GitCompare className="size-4" /> Compare with memory OFF
               </Button>
             )}>
        {incident.triage ? <TriageCardView triage={incident.triage} />
          : <Working title="Triage in progress" steps={TRIAGE_STEPS.filter((s) => s.key !== "fallback" || stage === "fallback")}
                     active={stage ?? "recall"} since={incident.opened_at} />}
      </Panel>

      {/* actions */}
      {incident.triage && incident.status === "open" && (
        <Panel title="Respond" icon={<ClipboardCheck className="size-4" />}
               right={<label className="flex items-center gap-2 text-sm text-muted-foreground">
                 You are
                 <input value={actor} onChange={(e) => setActor(e.target.value)}
                        className="h-8 w-44 rounded-md border bg-background px-2 text-base text-foreground" />
               </label>}>
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap gap-3">
              <Button size="lg" disabled={!!incident.diagnosed_at || busy !== null} className="h-11 gap-2 px-5 text-base"
                      onClick={() => act("diagnose", () => api.diagnose(incident.id, actor))}>
                <Check className="size-5" /> {incident.diagnosed_at ? `Diagnosis confirmed in ${duration(incident.time_to_diagnose_s)}` : "Confirm diagnosis"}
              </Button>
              <Button size="lg" variant="secondary" disabled={busy !== null} className="h-11 gap-2 px-5 text-base"
                      onClick={() => act("resolve", () => api.resolve(incident.id, actor))}>
                <CheckCircle2 className="size-5" /> Resolve incident
              </Button>
            </div>
            <ActionLogger incident={incident} actor={actor} busy={busy} act={act} />
          </div>
        </Panel>
      )}

      {/* postmortem */}
      {incident.status === "resolved" && (
        <Postmortem incident={incident} actor={actor} busy={busy} act={act} />
      )}

      {/* runbook updates caused by this incident */}
      {incident.postmortem && <RunbookUpdates incident={incident} state={state} dispatch={dispatch} onOpenRunbooks={onOpenRunbooks} />}

      <Timeline events={events} />
    </div>
  )
}

function Lifecycle({ incident, state }: { incident: Incident; state: State }) {
  const updated = state.runbookUpdates.some((u) => u.reason.includes(incident.id))
  const steps = [
    { label: "Triage", done: !!incident.triage },
    { label: `Diagnosed${incident.time_to_diagnose_s != null ? ` · ${duration(incident.time_to_diagnose_s)}` : ""}`, done: !!incident.diagnosed_at },
    { label: `Resolved${incident.time_to_resolve_s != null ? ` · ${duration(incident.time_to_resolve_s)}` : ""}`, done: incident.status === "resolved" },
    { label: "Postmortem approved", done: !!incident.postmortem },
    { label: "Runbook updated", done: updated },
  ]
  return (
    <ol className="flex flex-wrap items-center gap-2 text-base">
      {steps.map((s, i) => (
        <li key={s.label} className="flex items-center gap-2">
          <span className={cn("flex items-center gap-1.5 rounded-full border px-3 py-1",
            s.done ? "border-[var(--status-good)] text-foreground" : "text-muted-foreground")}>
            {s.done ? <CheckCircle2 className="size-4" style={{ color: "var(--status-good)" }} /> : <span className="size-4 rounded-full border" />}
            {s.label}
          </span>
          {i < steps.length - 1 && <span className="text-muted-foreground">→</span>}
        </li>
      ))}
    </ol>
  )
}

/** One button that always does the next thing in the demo flow, so nothing needs scrolling on stage. */
function NextStep({ incident, actor, busy, act }: {
  incident: Incident
  actor: string
  busy: string | null
  act: (key: string, fn: () => Promise<unknown>) => Promise<void>
}) {
  let step: { key: string; label: string; run: () => Promise<unknown> } | null = null
  if (incident.triage && incident.status === "open" && !incident.diagnosed_at) {
    step = { key: "diagnose", label: "Confirm diagnosis", run: () => api.diagnose(incident.id, actor) }
  } else if (incident.triage && incident.status === "open") {
    step = { key: "resolve", label: "Resolve incident", run: () => api.resolve(incident.id, actor) }
  } else if (incident.postmortem_draft && !incident.postmortem) {
    step = { key: "approve", label: "Approve postmortem", run: () => api.approve(incident.id, actor) }
  }
  if (!step) return null
  return (
    <Button size="lg" disabled={busy !== null} onClick={() => act(step.key, step.run)}
            className="ml-auto h-11 gap-2 px-5 text-base font-semibold">
      <ArrowRight className="size-5" /> {step.label}
    </Button>
  )
}

function ActionLogger({ incident, actor, busy, act }: {
  incident: Incident
  actor: string
  busy: string | null
  act: (key: string, fn: () => Promise<unknown>) => Promise<void>
}) {
  const [text, setText] = useState("")
  const suggestions = incident.triage?.card.suggested_fix ?? []
  const log = (outcome: "worked" | "failed") => {
    if (!text.trim()) return
    act(`action-${outcome}`, () => api.action(incident.id, text.trim(), actor, outcome)).then(() => setText(""))
  }
  return (
    <div className="flex flex-col gap-2">
      <span className="text-sm text-muted-foreground">Log what you tried — every outcome is retained to memory</span>
      <div className="flex gap-2">
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Rolled back to v2.4.0"
               list="fix-suggestions" className="h-11 min-w-0 flex-1 rounded-md border bg-background px-3 text-base" />
        <datalist id="fix-suggestions">{suggestions.map((s) => <option key={s} value={s} />)}</datalist>
        <Button variant="outline" disabled={!text.trim() || busy !== null} onClick={() => log("worked")}
                className="h-11 gap-2 text-base" style={{ color: "var(--status-good)" }}>
          <ThumbsUp className="size-4" /> Worked
        </Button>
        <Button variant="outline" disabled={!text.trim() || busy !== null} onClick={() => log("failed")}
                className="h-11 gap-2 text-base" style={{ color: "var(--status-critical)" }}>
          <ThumbsDown className="size-4" /> Didn't work
        </Button>
      </div>
      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {suggestions.slice(0, 3).map((s) => (
            <button key={s} onClick={() => setText(s)}
                    className="max-w-[28rem] truncate rounded-full border px-3 py-1 text-sm text-muted-foreground hover:text-foreground">
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function Postmortem({ incident, actor, busy, act }: {
  incident: Incident
  actor: string
  busy: string | null
  act: (key: string, fn: () => Promise<unknown>) => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(incident.postmortem_draft ?? "")
  useEffect(() => { if (!editing) setText(incident.postmortem_draft ?? "") }, [incident.postmortem_draft, editing])

  if (!incident.postmortem_draft) {
    return <Working title="Writing the postmortem draft" since={incident.resolved_at} />
  }
  const approved = !!incident.postmortem
  return (
    <Panel title={approved ? "Postmortem — approved and retained to memory" : "Postmortem draft — review before it becomes memory"}
           icon={<FileText className="size-4" />}
           right={!approved && (
             <div className="flex gap-2">
               <Button variant="outline" className="h-9 gap-2 text-base" onClick={() => setEditing((v) => !v)}>
                 <Pencil className="size-4" /> {editing ? "Preview" : "Edit"}
               </Button>
               <Button className="h-9 gap-2 text-base" disabled={busy !== null}
                       onClick={() => act("approve", () => api.approve(incident.id, actor, text))}>
                 <Check className="size-4" /> Approve &amp; save to memory
               </Button>
             </div>
           )}>
      {editing && !approved
        ? <textarea value={text} onChange={(e) => setText(e.target.value)}
                    className="h-96 w-full rounded-md border bg-background p-3 font-mono text-base" />
        : <div className="max-h-[28rem] overflow-y-auto"><Markdown text={incident.postmortem ?? text} /></div>}
    </Panel>
  )
}

function RunbookUpdates({ incident, state, dispatch, onOpenRunbooks }: {
  incident: Incident
  state: State
  dispatch: Dispatch<Action>
  onOpenRunbooks: () => void
}) {
  const updates = state.runbookUpdates.filter((u) => u.reason.includes(incident.id))
  const refreshing = Object.entries(state.refreshing).filter(([, reason]) => reason.includes(incident.id))
  const [checkedServer, setCheckedServer] = useState(false)

  // After a page reload the live events are gone: recover this incident's runbook versions from the API.
  useEffect(() => {
    if (updates.length || refreshing.length || checkedServer) return
    const t = window.setTimeout(async () => {
      const summaries = await api.runbooks()
      for (const s of summaries.filter((s) => s.reason?.includes(incident.id) && s.version)) {
        const diff = await api.runbookDiff(s.id)
        dispatch({ type: "diff", key: `${s.id}@${s.version}`, diff })
        dispatch({ type: "runbookUpdated", update: { id: s.id, version: s.version!, added: diff.added, removed: diff.removed, reason: s.reason! } })
      }
      setCheckedServer(true)
    }, 8000)
    return () => window.clearTimeout(t)
  }, [updates.length, refreshing.length, checkedServer, incident.id, dispatch])

  useEffect(() => {
    for (const u of updates) {
      const key = `${u.id}@${u.version}`
      if (!state.diffs[key]) api.runbookDiff(u.id, undefined, u.version).then((diff) => dispatch({ type: "diff", key, diff }))
    }
  }, [updates, state.diffs, dispatch])

  if (!updates.length && !refreshing.length) {
    return checkedServer
      ? <Panel title="Runbooks"><p className="text-base text-muted-foreground">No runbook changes from this incident.</p></Panel>
      : <Working title="Updating runbooks with what this incident taught us" since={incident.postmortem ? null : null} />
  }
  return (
    <Panel title="Runbooks updated by this incident" icon={<ScrollText className="size-4" />}
           right={<Button variant="outline" className="h-9 gap-2 text-base" onClick={onOpenRunbooks}><BookOpen className="size-4" /> All runbooks</Button>}>
      <div className="flex flex-col gap-5">
        {refreshing.map(([id]) => (
          <Working key={id} title={`Updating ${runbookName(id)}…`} since={null} />
        ))}
        {updates.map((u) => {
          const diff = state.diffs[`${u.id}@${u.version}`]
          return (
            <div key={`${u.id}@${u.version}`} className="flex flex-col gap-2">
              <div className="flex items-center gap-3 text-lg font-semibold">
                {runbookName(u.id)}
                <span className="text-base font-normal text-muted-foreground">v{u.version - 1} → v{u.version}</span>
                <DiffStats added={u.added} removed={u.removed} />
              </div>
              {diff ? <DiffView diff={diff} /> : <Working title="Loading diff" />}
            </div>
          )
        })}
      </div>
    </Panel>
  )
}

function Timeline({ events }: { events: IncidentEvent[] }) {
  if (!events.length) return null
  return (
    <Panel title="Timeline" icon={<ScrollText className="size-4" />} bodyClassName="p-4">
      <ol className="flex flex-col gap-1.5">
        {events.map((e) => (
          <li key={e.id} className="flex gap-3 text-base">
            <span className="w-24 shrink-0 font-mono text-muted-foreground">{clock(e.at)}</span>
            <span className="w-28 shrink-0 text-muted-foreground">{e.kind.replace("_", " ")}</span>
            <span className="min-w-0">
              {e.outcome && (
                <span className="mr-2 font-semibold" style={{ color: e.outcome === "worked" ? "var(--status-good)" : "var(--status-critical)" }}>
                  {e.outcome === "worked" ? "✓ worked" : "✗ failed"}
                </span>
              )}
              {e.text}{e.actor && <span className="text-muted-foreground"> — {e.actor}</span>}
            </span>
          </li>
        ))}
      </ol>
    </Panel>
  )
}

function SeedIncident({ incident }: { incident: Incident }) {
  return (
    <Panel title={`${incident.id} — history (before DéjàVu)`}>
      <div className="flex flex-col gap-2 text-base">
        <span className="text-xl font-semibold">{incident.title}</span>
        <span>Root cause: {incident.root_cause}</span>
        <span>Fix: {incident.fix}</span>
        <span className="text-muted-foreground">Owner {incident.owner} · diagnosed in {duration(incident.time_to_diagnose_s)} · resolved in {duration(incident.time_to_resolve_s)}</span>
      </div>
    </Panel>
  )
}
