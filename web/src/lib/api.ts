// All requests go through the Vite proxy: /api -> agent, /shop -> ShopLite (see vite.config.ts).
import type {
  ChaosStatus, CompareResponse, Incident, IncidentEvent, IqPoint, RunbookDiff, RunbookSummary, RunbookVersion,
} from "./types"

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  })
  if (!res.ok) throw new Error(`${init?.method ?? "GET"} ${url} -> ${res.status} ${await res.text()}`)
  return res.json() as Promise<T>
}

const post = <T>(url: string, body?: unknown) =>
  request<T>(url, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  incidents: () => request<Incident[]>("/api/incidents?limit=60"),
  incident: (id: string) => request<Incident & { events: IncidentEvent[] }>(`/api/incidents/${id}`),
  action: (id: string, action: string, actor: string, outcome: "worked" | "failed" | null) =>
    post(`/api/incidents/${id}/actions`, { action, actor, outcome }),
  diagnose: (id: string, actor: string) => post<Incident>(`/api/incidents/${id}/diagnosis`, { actor }),
  resolve: (id: string, actor: string) => post<Incident>(`/api/incidents/${id}/resolve`, { actor }),
  approve: (id: string, approver: string, text?: string) =>
    post<Incident>(`/api/incidents/${id}/postmortem/approve`, { approver, text }),
  compare: (id: string, refresh = false) =>
    post<CompareResponse>(`/api/incidents/${id}/compare${refresh ? "?refresh=true" : ""}`),

  runbooks: () => request<RunbookSummary[]>("/api/runbooks"),
  runbookHistory: (id: string) => request<RunbookVersion[]>(`/api/runbooks/${id}/history`),
  runbookDiff: (id: string, from?: number, to?: number) => {
    const q = new URLSearchParams()
    if (from) q.set("from_version", String(from))
    if (to) q.set("to_version", String(to))
    return request<RunbookDiff>(`/api/runbooks/${id}/diff?${q}`)
  },

  iq: () => request<IqPoint[]>("/api/stats/iq"),

  chaos: () => request<ChaosStatus>("/shop/chaos"),
  chaosOn: (fault: string) => post<ChaosStatus>(`/shop/chaos/${fault}`),
  chaosOff: (fault: string) => request<ChaosStatus>(`/shop/chaos/${fault}`, { method: "DELETE" }),
  chaosReset: () => post<ChaosStatus>("/shop/chaos/reset"),
}
