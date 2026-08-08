import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_TARGET || "http://127.0.0.1:8000";

  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": { target: apiTarget, changeOrigin: true },
        "/test-support": { target: apiTarget, changeOrigin: true },
      },
    },
    preview: {
      host: "127.0.0.1",
      port: 4173,
      strictPort: true,
      proxy: {
        "/api": { target: apiTarget, changeOrigin: true },
        "/test-support": { target: apiTarget, changeOrigin: true },
      },
    },
    build: {
      outDir: "dist",
      emptyOutDir: true,
      sourcemap: true,
      manifest: true,
    },
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.js",
      restoreMocks: true,
      clearMocks: true,
    },
  };
});
