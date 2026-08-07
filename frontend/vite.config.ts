/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// API base: dev points at the FastAPI backend (CORS-allowed); the container
// build sets it to /apps/cadless/api so calls route back through the edge.
const API_BASE = process.env.VITE_API_BASE ?? "http://localhost:8000";
// Public base path: "/" for local dev, "/apps/cadless/" for the deploy
// (so built asset URLs are /apps/cadless/assets/...). Set via BASE_PATH at build.
const BASE = process.env.BASE_PATH ?? "/";

export default defineConfig({
  base: BASE,
  plugins: [react()],
  define: {
    "import.meta.env.VITE_API_BASE": JSON.stringify(API_BASE),
  },
  server: {
    port: 5173,
    proxy: {
      // optional: proxy API paths in dev so the app can also use same-origin URLs
      "/projects": "http://localhost:8000",
      "/versions": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
