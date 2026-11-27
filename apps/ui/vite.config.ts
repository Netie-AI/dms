import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  // Swap: the banner reads the same DMS_RUNTIME_SELFCHECK name as the API
  // setting. The prefix is the full name, so other DMS_* values stay out of
  // the bundle. Unset is off.
  envPrefix: ["VITE_", "DMS_RUNTIME_SELFCHECK"],
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.join(root, "src"),
    },
  },
  server: {
    port: Number(process.env.VITE_UI_PORT ?? 3000),
    proxy: {
      "/api": {
        // Override when running a second stack beside a live one on :8090.
        target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8090",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
