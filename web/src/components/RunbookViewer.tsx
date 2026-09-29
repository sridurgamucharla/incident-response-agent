import { BookOpen, FileDiff, FileText } from "lucide-react"
import { useEffect, useState } from "react"

import { Panel, Spinner } from "@/components/common"
import { DiffStats, DiffView } from "@/components/DiffView"
import { Markdown } from "@/components/Markdown"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { ago, runbookName } from "@/lib/format"
import type { RunbookUpdate } from "@/lib/store"
import type { RunbookDiff, RunbookSummary, RunbookVersion } from "@/lib/types"
import { cn } from "@/lib/utils"

export function RunbookViewer({ refreshing, updates }: { refreshing: Record<string, string>; updates: RunbookUpdate[] }) {
  const [summaries, setSummaries] = useState<RunbookSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [history, setHistory] = useState<RunbookVersion[]>([])
  const [version, setVersion] = useState<number | null>(null)
  const [mode, setMode] = useState<"document" | "changes">("changes")
  const [diff, setDiff] = useState<RunbookDiff | null>(null)

  // reload whenever a runbook changes anywhere
  useEffect(() => {
    api.runbooks().then((list) => {
      setSummaries(list)
      // open on the most recently updated runbook, so its diff is the first thing on screen
      const newest = [...list].sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""))
        .find((s) => s.id !== "team-expertise-map") ?? list[0]
      setSelected((current) => (updates.length || !current ? newest?.id ?? null : current))
    }).catch(() => {})
  }, [updates.length])
  useEffect(() => {
    if (!selected) return
    api.runbookHistory(selected).then((h) => {
      setHistory(h)
      const latest = h[h.length - 1]?.version ?? null
      setVersion(latest)
      setMode(latest && latest > 1 ? "changes" : "document")
    }).catch(() => {})
  }, [selected, updates.length])
  useEffect(() => {
    setDiff(null)
    if (selected && version && version > 1) api.runbookDiff(selected, version - 1, version).then(setDiff).catch(() => {})
  }, [selected, version])

  const current = history.find((h) => h.version === version)

  return (
    <div className="grid gap-4 pb-8 xl:grid-cols-[20rem_1fr]">
      <Panel title="Runbooks" icon={<BookOpen className="size-4" />} bodyClassName="flex flex-col gap-2 p-3">
        {summaries.map((s) => (
          <button key={s.id} onClick={() => setSelected(s.id)}
                  className={cn("flex flex-col gap-1 rounded-lg border px-3 py-2.5 text-left hover:bg-accent",
                    selected === s.id && "border-[var(--series-memory)] bg-accent")}>
            <span className="flex items-center gap-2 text-base font-semibold">
              {s.name}
              {refreshing[s.id] && <Spinner className="ml-auto size-4" />}
            </span>
            <span className="text-sm text-muted-foreground">
              {s.version ? `v${s.version} · ${s.versions} version${s.versions === 1 ? "" : "s"} · ${ago(s.updated_at)}` : "not built yet"}
            </span>
            {s.reason && <span className="truncate text-sm text-muted-foreground">{s.reason}</span>}
          </button>
        ))}
        <p className="px-1 pt-2 text-sm text-muted-foreground">
          Rebuilt from Hindsight memory after every approved postmortem. Unchanged sections are kept word-for-word.
        </p>
      </Panel>

      <Panel title={selected ? runbookName(selected) : "Runbook"} icon={<FileText className="size-4" />}
             right={
               <div className="flex items-center gap-2">
                 {history.map((h) => (
                   <button key={h.version} onClick={() => setVersion(h.version)}
                           className={cn("rounded-md border px-2.5 py-1 font-mono text-sm",
                             h.version === version ? "border-[var(--series-memory)] text-foreground" : "text-muted-foreground")}>
                     v{h.version}
                   </button>
                 ))}
                 {version && version > 1 && (
                   <>
                     <Button variant={mode === "changes" ? "default" : "outline"} className="h-9 gap-2 text-base" onClick={() => setMode("changes")}>
                       <FileDiff className="size-4" /> Changes
                     </Button>
                     <Button variant={mode === "document" ? "default" : "outline"} className="h-9 gap-2 text-base" onClick={() => setMode("document")}>
                       <FileText className="size-4" /> Document
                     </Button>
                   </>
                 )}
               </div>
             }>
        {selected && refreshing[selected] && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-dashed p-3 text-base">
            <Spinner /> Updating from memory: {selected && refreshing[selected]}
          </div>
        )}
        {current && (
          <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
            <span>v{current.version}: {current.reason}</span>
            <span>{new Date(current.created_at).toLocaleString()}</span>
            <span>source: {current.source}</span>
            {mode === "changes" && diff && <DiffStats added={diff.added} removed={diff.removed} />}
          </div>
        )}
        {!current ? <p className="text-base text-muted-foreground">Loading…</p>
          : mode === "changes" && version && version > 1
            ? (diff ? <DiffView diff={diff} /> : <Spinner />)
            : <Markdown text={current.content} />}
      </Panel>
    </div>
  )
}
