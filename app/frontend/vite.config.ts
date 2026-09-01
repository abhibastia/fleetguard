import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxies /api to the FastAPI backend so the browser sees one origin — no CORS in
// development, and none in production either, where FastAPI serves the built assets itself.
// One origin also means the session cookie the U2M flow sets is simply sent, with no
// SameSite gymnastics.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
  build: { outDir: "dist", sourcemap: true },
});
