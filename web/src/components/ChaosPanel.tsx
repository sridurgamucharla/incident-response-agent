import { Bomb, Clock, Database, KeyRound, MemoryStick, RotateCcw, Rocket } from "lucide-react"
import { useCallback, useEffect, useState } from "react"

import { Panel } from "@/components/common"
import { Button } from "@/components/ui/button"
import { api } from "@/lib/api"
import type { ChaosStatus } from "@/lib/types"
import { cn } from "@/lib/utils"

const FAULTS: Record<string, { label: string; Icon: typeof Database }> = {
  db_leak: { label: "DB connection leak", Icon: Database },
  memory_leak: { label: "Memory leak", Icon: MemoryStick },
  slow_payment: { label: "Slow payment API", Icon: Clock },
  bad_deploy: { label: "Bad deploy", Icon: Rocket },
  expired_api_key: { label: "Expired API key", Icon: KeyRound },
}

/** Buttons that break ShopLite for real. ShopLite has no WebSocket, so its state is polled. */
export function ChaosPanel() {
  const [status, setStatus] = useState<ChaosStatus | null>(null)
  const [error, setError] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)

  const refresh = useCallback(() => {
    api.chaos().then((s) => { setStatus(s); setError(false) }).catch(() => setError(true))
  }, [])
  useEffect(() => {
    refresh()
    const t = window.setInterval(refresh, 3000)
    return () => window.clearInterval(t)
  }, [refresh])

  const run = async (key: string, fn: () => Promise<ChaosStatus>) => {
    setBusy(key)
    try { setStatus(await fn()) } finally { setBusy(null) }
  }

  return (
    <Panel title="Chaos control" icon={<Bomb className="size-4" />}
           right={<span className="font-mono text-sm text-muted-foreground">ShopLite {status?.version ?? "…"}</span>}
           bodyClassName="flex flex-col gap-2 p-4">
      {error && <p className="text-sm" style={{ color: "var(--status-serious)" }}>ShopLite is not reachable on :8001</p>}
      {Object.entries(FAULTS).map(([name, { label, Icon }]) => {
        const fault = status?.faults.find((f) => f.name === name)
        const active = !!fault?.active
        return (
          <Button key={name} variant="outline" disabled={!status || busy !== null}
                  title={fault?.description}
                  onClick={() => run(name, () => (active ? api.chaosOff(name) : api.chaosOn(name)))}
                  style={active ? { borderColor: "var(--status-critical)", background: "rgba(208, 59, 59, 0.18)" } : undefined}
                  className={cn("h-11 justify-start gap-3 text-base", active && "text-foreground")}>
            <Icon className="size-5" />
            <span className="flex-1 text-left">{label}</span>
            {active && <span className="text-sm font-semibold" style={{ color: "var(--status-critical)" }}>BREAKING</span>}
          </Button>
        )
      })}
      <Button variant="secondary" disabled={!status || busy !== null} onClick={() => run("reset", api.chaosReset)}
              className="mt-1 h-11 gap-2 text-base">
        <RotateCcw className="size-5" /> Fix everything (reset)
      </Button>
      {status && (status.leaked_db_connections > 0 || status.leaked_memory_mb > 0) && (
        <p className="font-mono text-sm text-muted-foreground">
          leaked: {status.leaked_db_connections} DB conns · {status.leaked_memory_mb} MB
        </p>
      )}
    </Panel>
  )
}
