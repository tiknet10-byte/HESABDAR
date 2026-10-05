import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { readFileSync } from "node:fs";

// The UI is built for one backend version; a mismatch means an old server process is still running.
const version = /__version__\s*=\s*"([^"]+)"/.exec(readFileSync(new URL("../backend/app/__init__.py", import.meta.url), "utf8"))?.[1] ?? "dev";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: { __APP_VERSION__: JSON.stringify(version) },
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { chunkSizeWarningLimit: 1200 },
});
