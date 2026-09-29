export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "—"
  if (seconds < 90) return `${Math.round(seconds)} s`
  if (seconds < 90 * 60) return `${Math.round(seconds / 60)} min`
  return `${(seconds / 3600).toFixed(1)} h`
}

export function clock(iso: string | null | undefined): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
}

export function day(iso: string | null | undefined): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" })
}

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return ""
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

export function pct(x: number | null | undefined): string {
  return x == null ? "—" : `${Math.round(x * 100)}%`
}

export function runbookName(id: string): string {
  return id === "team-expertise-map" ? "Team expertise map" : id.replace(/^runbook-/, "Runbook: ")
}
