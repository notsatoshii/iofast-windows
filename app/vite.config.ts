import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Fixed port: tauri.conf.json's devUrl must match, and a silent port bump
// would leave `tauri dev` pointed at nothing.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: { port: 1420, strictPort: true },
  build: { target: "es2021" },
});
