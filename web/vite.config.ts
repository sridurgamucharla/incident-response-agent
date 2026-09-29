import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig, loadEnv } from "vite"

// Backend URLs come from the project-root .env (AGENT_URL, SHOPLITE_URL); the browser only talks to Vite.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, path.resolve(__dirname, ".."), "")
  const agent = env.AGENT_URL || "http://localhost:8000"
  const shop = env.SHOPLITE_URL || "http://localhost:8001"
  return {
    plugins: [react(), tailwindcss()],
    resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
    server: {
      port: 5173,
      proxy: {
        "/api": { target: agent, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
        "/ws": { target: agent.replace(/^http/, "ws"), ws: true },
        "/shop": { target: shop, changeOrigin: true, rewrite: (p) => p.replace(/^\/shop/, "") },
      },
    },
  }
})
