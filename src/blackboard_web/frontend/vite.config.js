import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // `npm run dev` serves the UI while FastAPI keeps serving the data.
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
