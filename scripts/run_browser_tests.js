const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
const BACKEND_HEALTH_URL = "http://127.0.0.1:8011/api/health";
const FRONTEND_URL = "http://127.0.0.1:4173/prep";
const python = process.env.STAGE41_PYTHON || "python";
if (!process.env.AGENT_TRACE_DIR) {
  process.env.AGENT_TRACE_DIR = fs.mkdtempSync(
    path.join(os.tmpdir(), "stage43-agent-traces-"),
  );
}

function urlAvailable(url) {
  return new Promise((resolve) => {
    const request = http.get(url, (response) => {
      response.resume();
      resolve(Boolean(response.statusCode && response.statusCode >= 200 && response.statusCode < 400));
    });
    request.setTimeout(1_000, () => request.destroy());
    request.on("error", () => resolve(false));
  });
}

async function waitForService(server, url, label, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) {
      throw new Error(`Browser support server exited with code ${server.exitCode}`);
    }
    if (await urlAvailable(url)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${label}: ${url}`);
}

function waitForExit(child, timeoutMs) {
  if (child.exitCode !== null) return Promise.resolve(true);
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve(true);
    });
  });
}

async function stopServer(server) {
  if (!server || server.exitCode !== null) return;
  server.kill();
  if (await waitForExit(server, 5_000)) return;
  if (process.platform === "win32") {
    server.kill("SIGKILL");
  } else {
    server.kill("SIGKILL");
  }
  await waitForExit(server, 5_000);
}

async function run() {
  const reuseExisting = process.env.REUSE_EXISTING_SERVER === "true";
  const backendAvailable = await urlAvailable(BACKEND_HEALTH_URL);
  const frontendAvailable = await urlAvailable(FRONTEND_URL);
  let backend = null;
  let frontend = null;

  if ((backendAvailable || frontendAvailable) && !reuseExisting) {
    throw new Error(
      "Browser test ports 8011 or 4173 are already in use; stop existing services or set REUSE_EXISTING_SERVER=true",
    );
  }

  try {
    if (!backendAvailable) {
      backend = spawn(
        python,
        [
          "-m",
          "uvicorn",
          "tests.browser_support_app:app",
          "--host",
          "127.0.0.1",
          "--port",
          "8011",
        ],
        {
          cwd: ROOT,
          env: process.env,
          shell: false,
          stdio: ["ignore", "inherit", "inherit"],
          windowsHide: true,
        },
      );
      await waitForService(backend, BACKEND_HEALTH_URL, "browser support backend");
    }

    if (!frontendAvailable) {
      const viteCli = path.join(
        ROOT,
        "frontend",
        "node_modules",
        "vite",
        "bin",
        "vite.js",
      );
      frontend = spawn(
        process.execPath,
        [viteCli, "--host", "127.0.0.1", "--port", "4173"],
        {
          cwd: path.join(ROOT, "frontend"),
          env: {
            ...process.env,
            VITE_API_TARGET: "http://127.0.0.1:8011",
          },
          shell: false,
          stdio: ["ignore", "inherit", "inherit"],
          windowsHide: true,
        },
      );
      await waitForService(frontend, FRONTEND_URL, "Vite frontend");
    }

    const cli = require.resolve("@playwright/test/cli");
    const testProcess = spawn(
      process.execPath,
      [cli, "test", ...process.argv.slice(2)],
      {
        cwd: ROOT,
        env: {
          ...process.env,
          PLAYWRIGHT_EXTERNAL_WEB_SERVER: "true",
          REUSE_EXISTING_SERVER: "true",
        },
        shell: false,
        stdio: "inherit",
        windowsHide: true,
      },
    );
    const exitCode = await new Promise((resolve, reject) => {
      testProcess.once("error", reject);
      testProcess.once("exit", (code) => resolve(code ?? 1));
    });
    process.exitCode = exitCode;
  } finally {
    await stopServer(frontend);
    await stopServer(backend);
  }
}

run().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exitCode = 1;
});
