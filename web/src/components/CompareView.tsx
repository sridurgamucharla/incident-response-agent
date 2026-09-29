import { Ban, Brain, BrainCircuit, History, PhoneCall, XCircle } from "lucide-react"
import { type Dispatch, type ReactNode, useEffect, useState } from "react"

import { Panel, Working } from "@/components/common"
import { Confidence, Provenance } from "@/components/TriageCard"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { day, pct } from "@/lib/format"
import type { Action } from "@/lib/store"
import type { CompareResponse, Incident, TriageResult } from "@/lib/types"

export function CompareView({ incident, dispatch }: { incident: Incident | null; dispatch: Dispatch<Action> }) {
  const [data, setData] = useState<CompareResponse | null>(null)
  const [running, setRunning] = useState(false)
  const [startedAt, setStartedAt] = useState<string | null>(null)

  useEffect(() => {
    setData(null)
    if (incident?.triage_no_memory) api.compare(incident.id).then(setData).catch(() => {})
  }, [incident?.id, incident?.triage_no_memory])

  if (!incident || incident.source !== "live" || !incident.triage) {
    return <p className="p-6 text-lg text-muted-foreground">Select a live incident with a triage card to compare.</p>
  }

  const run = async () => {
    setRunning(true)
    setStartedAt(new Date().toISOString())
    try {
      const res = await api.compare(incident.id)
      setData(res)
      dispatch({ type: "upsert", incident: { ...incident, triage_no_memory: res.memory_off } })
    } finally { setRunning(false) }
  }

  if (!data) {
    return (
      <div className="flex flex-col gap-4 pb-8">
        <Intro incident={incident} />
        {running
          ? <Working title="Asking the same model the same question — with no memory" since={startedAt} />
          : <Button size="lg" onClick={run} className="h-12 self-start gap-2 px-6 text-lg">
              <BrainCircuit className="size-5" /> Run this alert with memory OFF
            </Button>}
      </div>
    )
  }

  const adds = data.memory_adds
  const flags = data.memory_off.failed_before ?? []
  return (
    <div className="flex flex-col gap-4 pb-8">
      <Intro incident={incident} />
      <div className="grid gap-3 md:grid-cols-4">
        <Stat label="Past incidents matched" value={adds.matched_incidents.length} sub={adds.matched_incidents.join(", ") || "—"} />
        <Stat label={`"Don't try" warnings`} value={adds.do_not_try} sub="things that failed before" />
        <Stat label="Expert to page" value={adds.owner ?? "—"} sub="from who fixed it last time" />
        <Stat label="OFF suggestions that already failed" value={adds.off_suggestions_that_failed_before}
              sub="would repeat past mistakes" critical={adds.off_suggestions_that_failed_before > 0} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Side title="Memory OFF — same model, no history" icon={<BrainCircuit className="size-4" />} triage={data.memory_off} off>
          <Section title="Suggested fix">
            <ol className="flex flex-col gap-2.5">
              {data.memory_off.card.suggested_fix.map((step, i) => {
                const flag = flags.find((f) => f.index === i)
                return (
                  <li key={i} className="flex flex-col gap-1 rounded-md p-2"
                      style={flag ? { background: "var(--diff-del-bg)", outline: "1px solid var(--status-critical)" } : undefined}>
                    <span className="flex gap-3 text-base">
                      <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-semibold">{i + 1}</span>
                      {step}
                    </span>
                    {flag && (
                      <span className="ml-9 flex items-start gap-1.5 text-base font-semibold" style={{ color: "var(--diff-del-fg)" }}>
                        <XCircle className="mt-0.5 size-4 shrink-0" />
                        <span>This failed in {flag.failed_in}<span className="block text-sm font-normal text-muted-foreground">{flag.evidence}</span></span>
                      </span>
                    )}
                  </li>
                )
              })}
            </ol>
          </Section>
          <Missing title="Don't try" text="No history — it can't know what failed before." />
          <Missing title="Seen before" text="No memory of past incidents." />
          <Missing title="Who to page" text="Unknown — it doesn't know the team." />
        </Side>

        <Side title="Memory ON — DéjàVu with Hindsight" icon={<Brain className="size-4" />} triage={data.memory_on}>
          <Section title="Suggested fix">
            <ol className="flex flex-col gap-2.5">
              {data.memory_on.card.suggested_fix.map((step, i) => (
                <li key={i} className="flex gap-3 p-2 text-base">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-sm font-semibold">{i + 1}</span>
                  {step}
                </li>
              ))}
            </ol>
          </Section>
          <Section title="Don't try — failed before" icon={<Ban className="size-4" />} memory>
            <ul className="flex flex-col gap-2">
              {data.memory_on.card.do_not_try.map((d, i) => (
                <li key={i} className="text-base"><span className="font-semibold">{d.action}</span>
                  {d.incident_id && <span className="font-mono text-muted-foreground"> · {d.incident_id}</span>}</li>
              ))}
            </ul>
          </Section>
          <Section title="Seen before" icon={<History className="size-4" />} memory>
            <ul className="flex flex-col gap-1.5">
              {data.memory_on.card.matched_incidents.map((m) => (
                <li key={m.incident_id} className="text-base">
                  <span className="font-mono font-semibold">{m.incident_id}</span>
                  <span className="text-muted-foreground"> · {day(m.date)} · {pct(m.similarity)} match — </span>{m.what_happened}
                </li>
              ))}
            </ul>
          </Section>
          <Section title="Who to page" icon={<PhoneCall className="size-4" />} memory>
            <span className="text-xl font-bold">{data.memory_on.card.owner?.name ?? "—"}</span>
          </Section>
        </Side>
      </div>
    </div>
  )
}

function Intro({ incident }: { incident: Incident }) {
  return (
    <p className="text-lg text-muted-foreground">
      Same alert (<span className="font-mono text-foreground">{incident.id}</span> · {incident.signature} on {incident.service}),
      same model — the only difference is Hindsight memory.
    </p>
  )
}

function Stat({ label, value, sub, critical }: { label: string; value: ReactNode; sub: string; critical?: boolean }) {
  return (
    <div className="flex flex-col gap-1 rounded-xl border bg-card p-4"
         style={critical ? { borderColor: "var(--status-critical)" } : undefined}>
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="truncate text-3xl font-bold" style={critical ? { color: "var(--diff-del-fg)" } : undefined}>{value}</span>
      <span className="truncate text-sm text-muted-foreground">{sub}</span>
    </div>
  )
}

function Side({ title, icon, triage, off, children }: {
  title: string
  icon: ReactNode
  triage: TriageResult
  off?: boolean
  children: ReactNode
}) {
  return (
    <Panel title={title} icon={icon} className={off ? "opacity-95" : "border-[var(--series-memory)]"}>
      <div className="flex flex-col gap-4">
        <div className="flex gap-4">
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <span className="text-sm font-semibold tracking-wide text-muted-foreground uppercase">Likely root cause</span>
            <p className="text-lg leading-snug font-semibold">{triage.card.likely_cause}</p>
            <Provenance triage={triage} />
          </div>
          <Confidence value={triage.card.confidence} color={off ? "var(--series-nomemory)" : "var(--series-memory)"} />
        </div>
        {children}
      </div>
    </Panel>
  )
}

function Section({ title, icon, memory, children }: { title: string; icon?: ReactNode; memory?: boolean; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2 rounded-lg border p-3"
             style={memory ? { borderLeft: "4px solid var(--series-memory)" } : undefined}>
      <h3 className="flex items-center gap-2 text-base font-semibold" style={memory ? { color: "var(--series-memory)" } : undefined}>
        {icon}{title}{memory && <span className="ml-auto text-sm font-normal text-muted-foreground">from memory</span>}
      </h3>
      {children}
    </section>
  )
}

function Missing({ title, text }: { title: string; text: string }) {
  return (
    <section className="flex flex-col gap-1 rounded-lg border border-dashed p-3 text-muted-foreground">
      <h3 className="text-base font-semibold">{title}</h3>
      <span className="text-base">{text}</span>
    </section>
  )
}
