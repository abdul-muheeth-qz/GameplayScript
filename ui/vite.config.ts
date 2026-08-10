import path from "node:path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

// In development the UI runs here on :5173 and the API on :8000, so /api is proxied
// across. `npm run build` puts the app in ui/dist, which the API then serves itself --
// same origin, no proxy, one process.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "./src") },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        // A spin can run to the 180 s backstop, and OBS takes up to 40 s to start
        // before that. The default proxy timeout would cut the request off first and
        // report a network error for a capture that is still perfectly healthy.
        timeout: 300_000,
        proxyTimeout: 300_000,
      },
    },
  },
})
