const { defineConfig, devices } = require("@playwright/test");

const python = process.env.STAGE41_PYTHON || "python";

module.exports = defineConfig({
  testDir: "./tests/browser",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:8011",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: `"${python}" -m uvicorn tests.browser_support_app:app --host 127.0.0.1 --port 8011`,
    url: "http://127.0.0.1:8011/api/health",
    timeout: 30_000,
    reuseExistingServer: false,
    stdout: "pipe",
    stderr: "pipe",
  },
});
