import { Brain, History, Radio } from "lucide-react"
import { useEffect, useState } from "react"

import { Panel, SeverityBadge, Spinner, StatusBadge } from "@/components/common"
import { ago, day, duration } from "@/lib/format"
import type { Incident } from "@/lib/types"
import { cn } from "@/lib/utils"

export function IncidentFeed({ incidents, selectedId, onSelect }: {
  incidents: Incident[]
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const [, tick] = useState(0)
  useEffect(() => {
    const t = window.setInterval(() => tick((n) => n + 1), 5000)
    return () => window.clearInterval(t)
  }, [])
  const [showHistory, setShowHistory] = useState(false)
  const live = incidents.filter((i) => i.source === "live")
  const history = incidents.filter((i) => i.source === "seed")

  return (
    <Panel title="Incidents" icon={<Radio className="size-4" />} className="flex min-h-0 flex-1 flex-col"
           bodyClassName="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-3">
      {live.length === 0 && (
        <p className="px-2 py-4 text-base text-muted-foreground">
          All quiet. Break something with the chaos buttons above — Sentinel will open an incident here.
        </p>
      )}
      {live.map((i) => (
        <button key={i.id} onClick={() => onSelect(i.id)}
                className={cn("flex flex-col gap-1.5 rounded-lg border px-3 py-2.5 text-left transition-colors hover:bg-accent",
                  selectedId === i.id && "border-[var(--series-memory)] bg-accent")}>
          <div className="flex items-center gap-2">
            <span className="font-mono text-base font-semibold">{i.id}</span>
            <SeverityBadge severity={i.severity} className="px-1.5 py-0 text-xs" />
            <span className="ml-auto"><StatusBadge status={i.status} /></span>
          </div>
          <div className="truncate text-base">{i.title}</div>
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <span>{i.service}</span>·<span>{ago(i.opened_at)}</span>
            {!i.triage && i.status === "open" && <span className="ml-auto flex items-center gap-1"><Spinner className="size-3.5" />triaging</span>}
            {i.triage?.memory_used && (
              <span className="ml-auto flex items-center gap-1" style={{ color: "var(--series-memory)" }}>
                <Brain className="size-3.5" /> seen before
              </span>
            )}
          </div>
        </button>
      ))}

      <button onClick={() => setShowHistory((v) => !v)}
              className="mt-2 flex items-center gap-2 px-2 py-1 text-sm text-muted-foreground hover:text-foreground">
        <History className="size-4" /> {showHistory ? "Hide" : "Show"} history before DéjàVu ({history.length})
      </button>
      {showHistory && history.map((i) => (
        <div key={i.id} className="flex items-center gap-2 px-2 text-sm text-muted-foreground">
          <span className="font-mono">{i.id}</span>
          <span className="truncate">{i.title}</span>
          <span className="ml-auto shrink-0">{day(i.opened_at)} · {duration(i.time_to_diagnose_s)}</span>
        </div>
      ))}
    </Panel>
  )
}
