import { AlertOctagon, AlertTriangle, CheckCircle2, Info, Loader2 } from "lucide-react"
import { type ReactNode, useEffect, useState } from "react"

import { cn } from "@/lib/utils"
import type { Severity } from "@/lib/types"

const SEVERITY: Record<Severity, { color: string; Icon: typeof AlertOctagon }> = {
  SEV1: { color: "var(--status-critical)", Icon: AlertOctagon },
  SEV2: { color: "var(--status-serious)", Icon: AlertTriangle },
  SEV3: { color: "var(--status-warning)", Icon: Info },
}

/** Status color never carries meaning alone: icon + label + color. */
export function SeverityBadge({ severity, className }: { severity: Severity; className?: string }) {
  const { color, Icon } = SEVERITY[severity] ?? SEVERITY.SEV3
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-sm font-semibold", className)}
          style={{ color, borderColor: color }}>
      <Icon className="size-4" aria-hidden /> {severity}
    </span>
  )
}

export function StatusBadge({ status }: { status: "open" | "resolved" }) {
  return status === "resolved" ? (
    <span className="inline-flex items-center gap-1 text-sm font-medium" style={{ color: "var(--status-good)" }}>
      <CheckCircle2 className="size-4" aria-hidden /> Resolved
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 text-sm font-medium" style={{ color: "var(--status-critical)" }}>
      <span className="relative flex size-2.5">
        <span className="absolute inline-flex size-full animate-ping rounded-full opacity-75" style={{ background: "var(--status-critical)" }} />
        <span className="relative inline-flex size-2.5 rounded-full" style={{ background: "var(--status-critical)" }} />
      </span>
      Open
    </span>
  )
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-5 animate-spin", className)} aria-hidden />
}

export function Panel({ title, icon, right, children, className, bodyClassName }: {
  title?: ReactNode
  icon?: ReactNode
  right?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section className={cn("rounded-xl border bg-card", className)}>
      {title && (
        <header className="flex items-center justify-between gap-3 border-b px-5 py-3">
          <h2 className="flex items-center gap-2 text-base font-semibold tracking-wide text-muted-foreground uppercase">
            {icon}
            {title}
          </h2>
          {right}
        </header>
      )}
      <div className={cn("p-5", bodyClassName)}>{children}</div>
    </section>
  )
}

/** A long-running step (20–60 s): spinner, what is happening now, and elapsed time. */
export function Working({ title, steps, active, since }: {
  title: string
  steps?: { key: string; label: string }[]
  active?: string
  since?: string | null
}) {
  const activeIndex = steps?.findIndex((s) => s.key === active) ?? -1
  return (
    <div className="flex flex-col gap-4 rounded-xl border border-dashed p-6">
      <div className="flex items-center gap-3 text-lg font-semibold">
        <Spinner className="size-6 text-[var(--series-memory)]" />
        {title}
        {since && <Elapsed since={since} />}
      </div>
      {steps && (
        <ol className="flex flex-col gap-2 text-base">
          {steps.map((s, i) => (
            <li key={s.key} className={cn("flex items-center gap-2",
              i < activeIndex ? "text-muted-foreground" : i === activeIndex ? "font-semibold" : "text-muted-foreground/50")}>
              {i < activeIndex ? <CheckCircle2 className="size-4" style={{ color: "var(--status-good)" }} />
                : i === activeIndex ? <Spinner className="size-4" /> : <span className="size-4 rounded-full border" />}
              {s.label}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}


export function Elapsed({ since }: { since: string }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [])
  const s = Math.max(0, Math.round((now - new Date(since).getTime()) / 1000))
  return <span className="ml-auto font-mono text-base font-normal text-muted-foreground">{s}s</span>
}
