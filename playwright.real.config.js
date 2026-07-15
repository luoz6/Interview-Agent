const { defineConfig, devices } = require("@playwright/test");

const python = process.env.STAGE41_PYTHON || "python";

module.exports = defineConfig({
  testDir: "./tests/browser",
  testMatch: "real-model-smoke.spec.js",
  timeout: 720_000,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:8012",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "real-model-chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `"${python}" -m uvicorn app.main:app --host 127.0.0.1 --port 8012`,
    url: "http://127.0.0.1:8012/api/health",
    timeout: 30_000,
    reuseExistingServer: false,
    stdout: "pipe",
    stderr: "pipe",
  },
});
