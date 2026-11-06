import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API runs separately (uvicorn on port 8001); Vite forwards /api to it.
// The production build is served by FastAPI at /ui, so assets use that base path.
export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === "build" ? "/ui/" : "/",
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8001", "/health": "http://127.0.0.1:8001" },
  },
}));
