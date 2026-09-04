import { defineConfig } from "vite";

export default defineConfig({
  server: {
    port: 5173,
    // Every backend prefix has to be listed here. An unlisted one does not
    // fail loudly: the dev server falls through to index.html, so `fetch`
    // gets HTML and the caller reports `Unexpected token '<'`. If a new page
    // cannot load its data, check this list first.
    proxy: {
      "/ws":        { target: "ws://127.0.0.1:8765", ws: true },
      "/rooms":     "http://127.0.0.1:8765",
      "/approvals": "http://127.0.0.1:8765",
      "/health":    "http://127.0.0.1:8765",
      "/leads":     "http://127.0.0.1:8765",
      "/invoices":  "http://127.0.0.1:8765",
      "/pipeline":  "http://127.0.0.1:8765",
      // Published preview sites are served by the backend off disk.
      "/preview":   "http://127.0.0.1:8765",
      "/staging":   "http://127.0.0.1:8765",
      "/agents":    "http://127.0.0.1:8765",
    },
  },
});
