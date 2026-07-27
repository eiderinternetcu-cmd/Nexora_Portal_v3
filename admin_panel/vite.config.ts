import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Puertos 5174/4174 (el web_player usa 5173/4173) para poder levantar
// player y panel a la vez contra la misma API local.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5174,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: "0.0.0.0",
    port: 4174,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
