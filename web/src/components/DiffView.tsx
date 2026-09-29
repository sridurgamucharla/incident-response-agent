import { Minus, Plus } from "lucide-react"
import type { ReactNode } from "react"

import type { RunbookDiff } from "@/lib/types"

const CONTEXT = 2

/** Line diff: green "+" lines, red "-" lines. "changes" mode folds unchanged runs, keeping 2 lines of context. */
export function DiffView({ diff, mode = "changes" }: { diff: RunbookDiff; mode?: "changes" | "full" }) {
  const lines = diff.lines
  const keep = new Set<number>()
  lines.forEach((l, i) => {
    if (mode === "full" || l.op !== " ") {
      for (let j = Math.max(0, i - CONTEXT); j <= Math.min(lines.length - 1, i + CONTEXT); j++) keep.add(j)
    }
  })

  const rows: ReactNode[] = []
  let skipped = 0
  lines.forEach((l, i) => {
    if (!keep.has(i)) {
      skipped++
      return
    }
    if (skipped) {
      rows.push(<Fold key={`fold-${i}`} count={skipped} />)
      skipped = 0
    }
    rows.push(<Line key={i} op={l.op} text={l.text} />)
  })
  if (skipped) rows.push(<Fold key="fold-end" count={skipped} />)

  if (!diff.added && !diff.removed) return <p className="text-base text-muted-foreground">No changes.</p>
  return <div className="overflow-x-auto rounded-lg border bg-background font-mono text-[0.92rem] leading-relaxed">{rows}</div>
}

function Line({ op, text }: { op: " " | "+" | "-"; text: string }) {
  const style =
    op === "+" ? { background: "var(--diff-add-bg)", color: "var(--diff-add-fg)" }
      : op === "-" ? { background: "var(--diff-del-bg)", color: "var(--diff-del-fg)" } : undefined
  return (
    <div className="flex gap-3 px-3 py-0.5" style={style}>
      <span className="w-4 shrink-0 select-none" aria-label={op === "+" ? "added" : op === "-" ? "removed" : "unchanged"}>
        {op === "+" ? <Plus className="mt-1 size-3.5" /> : op === "-" ? <Minus className="mt-1 size-3.5" /> : null}
      </span>
      <span className={op === " " ? "whitespace-pre-wrap text-muted-foreground" : "whitespace-pre-wrap"}>{text || " "}</span>
    </div>
  )
}

function Fold({ count }: { count: number }) {
  return (
    <div className="border-y border-dashed px-3 py-1 text-center text-sm text-muted-foreground">
      ⋯ {count} unchanged line{count === 1 ? "" : "s"}
    </div>
  )
}

export function DiffStats({ added, removed }: { added: number; removed: number }) {
  return (
    <span className="font-mono text-base">
      <span style={{ color: "var(--diff-add-fg)" }}>+{added}</span>{" "}
      <span style={{ color: "var(--diff-del-fg)" }}>−{removed}</span>
    </span>
  )
}
