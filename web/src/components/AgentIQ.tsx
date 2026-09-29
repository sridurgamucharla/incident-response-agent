import { LineChart, Table2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

import { Panel } from "@/components/common"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import { day, duration } from "@/lib/format"
import type { IqPoint } from "@/lib/types"

const MEMORY = "var(--series-memory)"
const NO_MEMORY = "var(--series-nomemory)"

function median(xs: number[]): number | null {
  if (!xs.length) return null
  const s = [...xs].sort((a, b) => a - b)
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2
}

export function AgentIQ({ refreshKey }: { refreshKey: number }) {
  const [points, setPoints] = useState<IqPoint[]>([])
  const [table, setTable] = useState(false)
  useEffect(() => { api.iq().then(setPoints).catch(() => {}) }, [refreshKey])

  const data = useMemo(() => points.map((p) => ({ ...p, minutes: p.time_to_diagnose_s / 60 })), [points])
  const before = median(points.filter((p) => !p.memory_used).map((p) => p.time_to_diagnose_s))
  const after = median(points.filter((p) => p.memory_used).map((p) => p.time_to_diagnose_s))
  const factor = before && after ? Math.round(before / after) : null

  return (
    <div className="flex flex-col gap-4 pb-8">
      <div className="grid gap-3 md:grid-cols-3">
        <Hero label="Before DéjàVu" value={duration(before)} swatch={NO_MEMORY}
              sub={`median time to diagnose · ${points.filter((p) => !p.memory_used).length} incidents, humans only`} />
        <Hero label="With DéjàVu memory" value={after == null ? "—" : duration(after)} swatch={MEMORY}
              sub={after == null ? "confirm a live diagnosis to see it" : `median time to diagnose · ${points.filter((p) => p.memory_used).length} live incident(s)`} />
        <Hero label="Faster diagnosis" value={factor ? `${factor}×` : "—"} sub="median before ÷ median with memory" />
      </div>

      <Panel title="Agent IQ — time to correct diagnosis, per incident" icon={<LineChart className="size-4" />}
             right={<Button variant="outline" className="h-9 gap-2 text-base" onClick={() => setTable((v) => !v)}>
               <Table2 className="size-4" /> {table ? "Chart" : "Table"}
             </Button>}>
        <div className="mb-3 flex items-center gap-6 text-base">
          <LegendItem color={NO_MEMORY} label="Diagnosed without memory (history)" />
          <LegendItem color={MEMORY} label="Diagnosed with DéjàVu memory" />
          <span className="ml-auto text-sm text-muted-foreground">oldest → newest</span>
        </div>
        {table ? <IqTable points={points} /> : (
          <div className="h-[26rem]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} margin={{ top: 28, right: 8, bottom: 8, left: 8 }} barCategoryGap={2}>
                <CartesianGrid vertical={false} stroke="var(--grid)" />
                <XAxis dataKey="id" tickFormatter={(id: string) => id.replace("INC-", "")} interval={0}
                       tick={{ fill: "var(--text-secondary)", fontSize: 13 }} axisLine={{ stroke: "var(--grid)" }} tickLine={false} />
                <YAxis tickFormatter={(m: number) => `${m}`} width={48}
                       tick={{ fill: "var(--text-secondary)", fontSize: 14 }} axisLine={false} tickLine={false}
                       label={{ value: "minutes", angle: -90, position: "insideLeft", fill: "var(--text-secondary)", fontSize: 14 }} />
                <Tooltip cursor={{ fill: "rgba(255,255,255,0.05)" }} content={<IqTooltip />} />
                <Bar dataKey="minutes" maxBarSize={34} radius={[4, 4, 0, 0]} minPointSize={3} isAnimationActive={false}>
                  {data.map((p) => <Cell key={p.id} fill={p.memory_used ? MEMORY : NO_MEMORY} />)}
                  <LabelList dataKey="time_to_diagnose_s" position="top"
                             content={(props) => {
                               const { x, y, width, index } = props as { x: number; y: number; width: number; index: number }
                               const p = data[index]
                               if (!p?.memory_used) return null // selective direct labels: only the memory bars
                               return <text x={x + width / 2} y={y - 8} textAnchor="middle" fill="var(--foreground)" fontSize={15} fontWeight={700}>
                                 {duration(p.time_to_diagnose_s)}
                               </text>
                             }} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Panel>
    </div>
  )
}

function Hero({ label, value, sub, swatch }: { label: string; value: string; sub: string; swatch?: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-xl border bg-card p-5">
      <span className="flex items-center gap-2 text-sm text-muted-foreground">
        {swatch && <span className="size-3 rounded-sm" style={{ background: swatch }} />}{label}
      </span>
      <span className="text-5xl font-bold tabular-nums">{value}</span>
      <span className="text-sm text-muted-foreground">{sub}</span>
    </div>
  )
}

function LegendItem({ color, label }: { color: string; label: string }) {
  return <span className="flex items-center gap-2"><span className="size-3.5 rounded-sm" style={{ background: color }} />{label}</span>
}

function IqTooltip({ active, payload }: { active?: boolean; payload?: { payload: IqPoint }[] }) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  return (
    <div className="rounded-lg border bg-popover px-3 py-2 text-base shadow-lg">
      <div className="font-mono font-semibold">{p.id} <span className="font-sans font-normal text-muted-foreground">· {day(p.opened_at)} · {p.service}</span></div>
      <div>Diagnosed in <span className="font-semibold">{duration(p.time_to_diagnose_s)}</span></div>
      <div className="text-muted-foreground">{p.memory_used ? "with DéjàVu memory" : "without memory"}{p.time_to_resolve_s != null && ` · resolved in ${duration(p.time_to_resolve_s)}`}</div>
    </div>
  )
}

function IqTable({ points }: { points: IqPoint[] }) {
  return (
    <div className="max-h-[26rem] overflow-auto">
      <table className="w-full text-left text-base">
        <thead className="text-sm text-muted-foreground">
          <tr><th className="py-1.5">Incident</th><th>Date</th><th>Service</th><th>Memory</th><th>Time to diagnose</th><th>Time to resolve</th></tr>
        </thead>
        <tbody>
          {points.map((p) => (
            <tr key={p.id} className="border-t">
              <td className="py-1.5 font-mono">{p.id}</td><td>{day(p.opened_at)}</td><td>{p.service}</td>
              <td>{p.memory_used ? "yes" : "no"}</td><td>{duration(p.time_to_diagnose_s)}</td><td>{duration(p.time_to_resolve_s)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
