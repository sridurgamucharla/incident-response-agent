import { useEffect, useRef, useState } from "react"

export type LiveStatus = "connecting" | "live" | "reconnecting"
export interface LiveMessage {
  type: string
  data: any // eslint-disable-line @typescript-eslint/no-explicit-any
}

/** WebSocket to the agent with automatic reconnect (1s, 2s, 4s ... capped at 10s).
 *  `onReconnect` fires after a reconnect so the app can re-fetch anything it missed. */
export function useLive(onMessage: (m: LiveMessage) => void, onReconnect: () => void): LiveStatus {
  const [status, setStatus] = useState<LiveStatus>("connecting")
  const handlers = useRef({ onMessage, onReconnect })
  handlers.current = { onMessage, onReconnect }

  useEffect(() => {
    let socket: WebSocket | null = null
    let retry = 0
    let timer: number | undefined
    let stopped = false
    let everConnected = false

    const connect = () => {
      const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`
      socket = new WebSocket(url)
      socket.onopen = () => {
        retry = 0
        setStatus("live")
        if (everConnected) handlers.current.onReconnect()
        everConnected = true
      }
      socket.onmessage = (e) => {
        try {
          handlers.current.onMessage(JSON.parse(e.data))
        } catch {
          /* ignore malformed frames */
        }
      }
      socket.onclose = () => {
        if (stopped) return
        setStatus("reconnecting")
        timer = window.setTimeout(connect, Math.min(10_000, 1000 * 2 ** retry++))
      }
      socket.onerror = () => socket?.close()
    }
    connect()
    const ping = window.setInterval(() => socket?.readyState === WebSocket.OPEN && socket.send("ping"), 20_000)
    return () => {
      stopped = true
      window.clearTimeout(timer)
      window.clearInterval(ping)
      socket?.close()
    }
  }, [])

  return status
}
