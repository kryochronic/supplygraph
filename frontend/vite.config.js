import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// `npm run dev`          → talks to the API on :58100 via proxy (VITE_API_PROXY overrides, e.g. in compose)
// `npm run build`        → frontend/dist, served by `sg serve`
// `npm run build:static` → one self-contained HTML with the graph embedded
export default defineConfig(({ mode }) => ({
  plugins: [react(), ...(mode === "static" ? [viteSingleFile()] : [])],
  build: { outDir: mode === "static" ? "dist-static" : "dist" },
  server: { proxy: { "/api": process.env.VITE_API_PROXY || "http://127.0.0.1:58100" } },
}));
