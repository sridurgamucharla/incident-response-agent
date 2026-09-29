import type { Incident, IncidentEvent, LlmCall, RunbookDiff, TriageStage } from "./types"

export interface RunbookUpdate {
  id: string
  version: number
  added: number
  removed: number
  reason: string
}

export interface State {
  incidents: Record<string, Incident>
  selectedId: string | null
  events: Record<string, IncidentEvent[]>
  stages: Record<string, TriageStage>
  lastLlm: LlmCall | null
  refreshing: Record<string, string> // runbook id -> reason
  runbookUpdates: RunbookUpdate[] // newest first
  diffs: Record<string, RunbookDiff> // "runbookId@version" -> diff
}

export const initialState: State = {
  incidents: {}, selectedId: null, events: {}, stages: {}, lastLlm: null, refreshing: {}, runbookUpdates: [], diffs: {},
}

export type Action =
  | { type: "incidents"; incidents: Incident[] }
  | { type: "upsert"; incident: Incident; select?: boolean }
  | { type: "select"; id: string }
  | { type: "events"; id: string; events: IncidentEvent[] }
  | { type: "event"; event: IncidentEvent }
  | { type: "stage"; id: string; stage: TriageStage }
  | { type: "llm"; call: LlmCall }
  | { type: "refreshing"; id: string; reason: string }
  | { type: "runbookUpdated"; update: RunbookUpdate }
  | { type: "runbookUnchanged"; id: string }
  | { type: "diff"; key: string; diff: RunbookDiff }

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "incidents": {
      const incidents = Object.fromEntries(action.incidents.map((i) => [i.id, i]))
      const openLive = action.incidents.find((i) => i.source === "live")
      return { ...state, incidents, selectedId: state.selectedId ?? openLive?.id ?? null }
    }
    case "upsert":
      return {
        ...state,
        incidents: { ...state.incidents, [action.incident.id]: action.incident },
        selectedId: action.select ? action.incident.id : state.selectedId ?? action.incident.id,
      }
    case "select":
      return { ...state, selectedId: action.id }
    case "events":
      return { ...state, events: { ...state.events, [action.id]: action.events } }
    case "event": {
      const list = state.events[action.event.incident_id] ?? []
      if (list.some((e) => e.id === action.event.id)) return state
      return { ...state, events: { ...state.events, [action.event.incident_id]: [...list, action.event] } }
    }
    case "stage":
      return { ...state, stages: { ...state.stages, [action.id]: action.stage } }
    case "llm":
      return { ...state, lastLlm: action.call }
    case "refreshing":
      return { ...state, refreshing: { ...state.refreshing, [action.id]: action.reason } }
    case "runbookUpdated": {
      const { [action.update.id]: _, ...refreshing } = state.refreshing
      return { ...state, refreshing, runbookUpdates: [action.update, ...state.runbookUpdates] }
    }
    case "runbookUnchanged": {
      const { [action.id]: _, ...refreshing } = state.refreshing
      return { ...state, refreshing }
    }
    case "diff":
      return { ...state, diffs: { ...state.diffs, [action.key]: action.diff } }
  }
}
