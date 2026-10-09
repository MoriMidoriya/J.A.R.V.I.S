import { defineConfig } from "vite";

// Dev server proxies the WebSocket + REST calls to the Python backend so `npm run dev` works with hot reload.
export default defineConfig({
  base: "./",
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/ws": { target: "ws://127.0.0.1:8765", ws: true },
      "/chat": "http://127.0.0.1:8765",
      "/health": "http://127.0.0.1:8765",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    target: "es2022",
    sourcemap: false,
  },
});
