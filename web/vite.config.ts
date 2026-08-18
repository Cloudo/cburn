import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The paths are relative: the page is served by FastAPI, and in M5 the same page opens
// in the Tauri webview - an absolute /assets would not work there.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    // The port is fixed and never moves: `tauri dev` waits for exactly this address
    // (`devUrl`), and a silent hop to 5174 would leave it waiting forever.
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8799",
      "/ws": { target: "ws://127.0.0.1:8799", ws: true },
    },
  },
});
