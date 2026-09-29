import { Ban, Brain, Cpu, History, ListChecks, PhoneCall, Sparkles } from "lucide-react"

import { day, pct } from "@/lib/format"
import type { TriageResult } from "@/lib/types"

const PATH_LABEL: Record<TriageResult["path"], string> = {
  gemini: "Gemini + Hindsight memory",
  "hindsight-reflect": "Hindsight reflect (Gemini unavailable)",
  "evidence-only": "Evidence only (no LLM available)",
  "no-memory": "Gemini, no memory",
}

export function Provenance({ triage }: { triage: TriageResult }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
      <span className="flex items-center gap-1.5"><Cpu className="size-4" />{PATH_LABEL[triage.path]}</span>
      {triage.model && <span className="font-mono text-foreground">{triage.model}</span>}
      {triage.path !== "no-memory" && <span>{triage.llm_calls} LLM call{triage.llm_calls === 1 ? "" : "s"}</span>}
      {triage.repairs > 0 && <span>{triage.repairs} repaired tool call{triage.repairs === 1 ? "" : "s"}</span>}
      {triage.memories_used > 0 && <span>{triage.memories_used} memories</span>}
      <span>{(triage.latency_ms / 1000).toFixed(1)} s</span>
    </div>
  )
}

/** Confidence as a stat tile: the number is the headline, the bar is secondary. */
export function Confidence({ value, color = "var(--series-memory)" }: { value: number; color?: string }) {
  return (
    <div className="flex w-44 shrink-0 flex-col gap-1.5 rounded-lg border p-3">
      <span className="text-sm text-muted-foreground">Confidence</span>
      <span className="text-4xl font-bold tabular-nums">{pct(value)}</span>
      <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full" style={{ width: `${Math.round(value * 100)}%`, background: color }} />
      </div>
    </div>
  )
}

export function TriageCardView({ triage }: { triage: TriageResult }) {
  const card = triage.card
  return (
    <div className="flex flex-col gap-5">
      <div className="flex gap-5">
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <span className="flex items-center gap-2 text-sm font-semibold tracking-wide text-muted-foreground uppercase">
            <Sparkles className="size-4" /> Likely root cause
          </span>
          <p className="text-xl leading-snug font-semibold">{card.likely_cause}</p>
          <Provenance triage={triage} />
        </div>
        <Confidence value={card.confidence} />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <section className="flex flex-col gap-3 rounded-lg border p-4">
          <h3 className="flex items-center gap-2 text-base font-semibold"><ListChecks className="size-5" /> Suggested fix</h3>
          <ol className="flex flex-col gap-2.5">
            {card.suggested_fix.map((step, i) => (
              <li key={i} className="flex gap-3 text-base leading-snug">
                <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-semibold">{i + 1}</span>
                {step}
              </li>
            ))}
          </ol>
        </section>

        <section className="flex flex-col gap-3 rounded-lg border p-4"
                 style={{ borderColor: card.do_not_try.length ? "var(--status-critical)" : undefined }}>
          <h3 className="flex items-center gap-2 text-base font-semibold" style={{ color: "var(--status-critical)" }}>
            <Ban className="size-5" /> Don't try — failed before
          </h3>
          {card.do_not_try.length === 0 && <p className="text-base text-muted-foreground">Nothing known to have failed.</p>}
          <ul className="flex flex-col gap-3">
            {card.do_not_try.map((d, i) => (
              <li key={i} className="flex flex-col gap-0.5">
                <span className="text-base font-semibold">{d.action}</span>
                <span className="text-base text-muted-foreground">
                  {d.why}{d.incident_id && <> · <span className="font-mono text-foreground">{d.incident_id}</span></>}
                </span>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <div className="grid gap-5 xl:grid-cols-[1fr_20rem]">
        <section className="flex flex-col gap-3 rounded-lg border p-4">
          <h3 className="flex items-center gap-2 text-base font-semibold" style={{ color: "var(--series-memory)" }}>
            <History className="size-5" /> Seen before
          </h3>
          {card.matched_incidents.length === 0 && <p className="text-base text-muted-foreground">No similar past incident.</p>}
          <div className="grid gap-3 lg:grid-cols-2">
            {card.matched_incidents.map((m) => (
              <div key={m.incident_id} className="flex flex-col gap-1 rounded-lg bg-muted/60 p-3">
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-base font-semibold">{m.incident_id}</span>
                  <span className="text-sm text-muted-foreground">{day(m.date)}</span>
                  {m.similarity != null && <span className="ml-auto text-sm text-muted-foreground">{pct(m.similarity)} match</span>}
                </div>
                <span className="text-base">{m.what_happened}</span>
                <span className="text-sm text-muted-foreground"><span className="font-semibold text-foreground">Fixed by:</span> {m.fix}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="flex flex-col gap-2 rounded-lg border p-4">
          <h3 className="flex items-center gap-2 text-base font-semibold"><PhoneCall className="size-5" /> Who to page</h3>
          {card.owner ? (
            <>
              <span className="text-2xl font-bold">{card.owner.name}</span>
              <span className="text-base text-muted-foreground">{card.owner.reason}</span>
            </>
          ) : <span className="text-base text-muted-foreground">No known expert.</span>}
        </section>
      </div>

      <p className="flex gap-2 text-base text-muted-foreground">
        <Brain className="mt-0.5 size-4 shrink-0" /> {card.reasoning}
      </p>
    </div>
  )
}
