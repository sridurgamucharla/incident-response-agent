import { Activity, BookOpen, Brain, Cpu, GitCompare, LineChart, Siren, Wifi, WifiOff } from "lucide-react"
import { useCallback, useEffect, useMemo, useReducer, useState } from "react"

import { AgentIQ } from "@/components/AgentIQ"
import { ChaosPanel } from "@/components/ChaosPanel"
import { CompareView } from "@/components/CompareView"
import { IncidentFeed } from "@/components/IncidentFeed"
import { IncidentView } from "@/components/IncidentView"
import { RunbookViewer } from "@/components/RunbookViewer"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { api } from "@/lib/api"
import { initialState, reducer } from "@/lib/store"
import { useLive, type LiveMessage } from "@/lib/useLive"

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState)
  const [tab, setTab] = useState("incident")

  const loadIncidents = useCallback(() => {
    api.incidents().then((incidents) => dispatch({ type: "incidents", incidents })).catch(() => {})
  }, [])
  useEffect(loadIncidents, [loadIncidents])

  const onMessage = useCallback((m: LiveMessage) => {
    switch (m.type) {
      case "incident.opened":
        dispatch({ type: "upsert", incident: m.data, select: true })
        setTab("incident")
        break
      case "incident.updated":
      case "triage.ready":
      case "postmortem.draft":
        dispatch({ type: "upsert", incident: m.data })
        break
      case "incident.event":
        dispatch({ type: "event", event: m.data })
        break
      case "triage.progress":
        dispatch({ type: "stage", id: m.data.incident_id, stage: m.data.stage })
        break
      case "llm.call":
        dispatch({ type: "llm", call: m.data })
        break
      case "runbook.refreshing":
        dispatch({ type: "refreshing", id: m.data.id, reason: m.data.reason })
        break
      case "runbook.updated":
        dispatch({ type: "runbookUpdated", update: m.data })
        break
      case "runbook.unchanged":
        dispatch({ type: "runbookUnchanged", id: m.data.id })
        break
    }
  }, [])
  const live = useLive(onMessage, loadIncidents)

  const incidents = useMemo(
    () => Object.values(state.incidents).sort((a, b) => b.opened_at.localeCompare(a.opened_at)),
    [state.incidents],
  )
  const selected = state.selectedId ? state.incidents[state.selectedId] : null

  // timeline of the selected incident
  useEffect(() => {
    if (!state.selectedId || state.incidents[state.selectedId]?.source !== "live") return
    const id = state.selectedId
    api.incident(id).then((d) => dispatch({ type: "events", id, events: d.events })).catch(() => {})
  }, [state.selectedId])

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <header className="flex items-center gap-4 border-b bg-card px-6 py-3">
        <div className="flex items-center gap-2.5">
          <Brain className="size-7" style={{ color: "var(--series-memory)" }} />
          <span className="text-2xl font-bold tracking-tight">DéjàVu</span>
          <span className="hidden text-base text-muted-foreground lg:inline">— the on-call engineer that never forgets an outage</span>
        </div>
        <div className="ml-auto flex items-center gap-5 text-sm">
          {state.lastLlm && (
            <span className="flex items-center gap-1.5 text-muted-foreground" title={state.lastLlm.label}>
              <Cpu className="size-4" /> last LLM: <span className="font-mono text-foreground">{state.lastLlm.model}</span>
              <span className="font-mono">({state.lastLlm.status === "ok" ? `${(state.lastLlm.latency_ms / 1000).toFixed(1)}s` : state.lastLlm.status})</span>
            </span>
          )}
          <span className="flex items-center gap-1.5 font-medium"
                style={{ color: live === "live" ? "var(--status-good)" : "var(--status-warning)" }}>
            {live === "live" ? <Wifi className="size-4" /> : <WifiOff className="size-4" />}
            {live === "live" ? "Live" : live === "connecting" ? "Connecting…" : "Reconnecting…"}
          </span>
        </div>
      </header>

      <div className="flex min-h-0 flex-1 gap-4 p-4">
        <aside className="flex w-[22rem] shrink-0 flex-col gap-4">
          <ChaosPanel />
          <IncidentFeed incidents={incidents} selectedId={state.selectedId}
                        onSelect={(id) => { dispatch({ type: "select", id }); setTab("incident") }} />
        </aside>

        <main className="flex min-w-0 flex-1 flex-col">
          <Tabs value={tab} onValueChange={(v) => setTab(String(v))} className="flex min-h-0 flex-1 flex-col">
            <TabsList className="h-11 gap-1 self-start">
              <TabsTrigger value="incident" className="px-4 text-base"><Siren className="size-4" />Incident</TabsTrigger>
              <TabsTrigger value="compare" className="px-4 text-base"><GitCompare className="size-4" />Memory ON vs OFF</TabsTrigger>
              <TabsTrigger value="runbooks" className="px-4 text-base"><BookOpen className="size-4" />Runbooks</TabsTrigger>
              <TabsTrigger value="iq" className="px-4 text-base"><LineChart className="size-4" />Agent IQ</TabsTrigger>
            </TabsList>
            <div className="min-h-0 flex-1 overflow-y-auto pr-1">
              <TabsContent value="incident">
                {selected ? (
                  <IncidentView incident={selected} events={state.events[selected.id] ?? []}
                                stage={state.stages[selected.id]} state={state} dispatch={dispatch}
                                onOpenRunbooks={() => setTab("runbooks")} onCompare={() => setTab("compare")} />
                ) : (
                  <div className="flex h-96 flex-col items-center justify-center gap-3 text-muted-foreground">
                    <Activity className="size-10" />
                    <p className="text-xl">No incident selected — break something to start the demo.</p>
                  </div>
                )}
              </TabsContent>
              <TabsContent value="compare">
                <CompareView incident={selected} dispatch={dispatch} />
              </TabsContent>
              <TabsContent value="runbooks">
                <RunbookViewer refreshing={state.refreshing} updates={state.runbookUpdates} />
              </TabsContent>
              <TabsContent value="iq">
                <AgentIQ refreshKey={incidents.filter((i) => i.time_to_diagnose_s != null).length} />
              </TabsContent>
            </div>
          </Tabs>
        </main>
      </div>
    </div>
  )
}
