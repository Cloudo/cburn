import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Пути относительные: страницу отдаёт FastAPI, а на M5 её же откроет webview
// Tauri — абсолютный /assets там не сработает.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8799",
      "/ws": { target: "ws://127.0.0.1:8799", ws: true },
    },
  },
});
