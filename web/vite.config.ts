import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dashboard is a local-only SPA. In production it is served by the
// FastAPI engine itself (loopback) or by the platform's same-origin proxy;
// the dev proxy below mirrors the same-origin contract.
export default defineConfig({
  plugins: [react()],
  // /mp/ base lets the built SPA be served as platform static files at /mp/
  // (Next.js public/) while the same bundle also works at "/" when the
  // FastAPI engine serves web/dist directly on loopback.
  base: "/mp/",
  server: {
    proxy: {
      "/api/v1": "http://127.0.0.1:8899",
    },
  },
});
