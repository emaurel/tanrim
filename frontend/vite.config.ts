import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/ws":        { target: "ws://127.0.0.1:8765", ws: true },
      "/rooms":     "http://127.0.0.1:8765",
      "/approvals": "http://127.0.0.1:8765",
      "/health":    "http://127.0.0.1:8765",
    },
  },
});
