const fs = require("fs");
const { resolvePythonRuntime } = require("./python_runtime");

async function run() {
  const major = Number(process.versions.node.split(".")[0]);
  if (major !== 22) {
    throw new Error("NODE_VERSION_UNSUPPORTED");
  }
  const pythonRuntime = resolvePythonRuntime();

  let playwrightPackage;
  let chromium;
  try {
    playwrightPackage = require("@playwright/test/package.json");
    ({ chromium } = require("@playwright/test"));
  } catch (_error) {
    throw new Error("BROWSER_DEPENDENCY_UNAVAILABLE");
  }
  const executable = chromium.executablePath();
  if (!executable || !fs.existsSync(executable)) {
    throw new Error("BROWSER_DEPENDENCY_UNAVAILABLE");
  }

  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const browserVersion = browser.version();
    process.stdout.write(`${JSON.stringify({
      browser_preflight: "PASS",
      node_version: process.versions.node,
      python_version: pythonRuntime.version,
      python_executable_realpath: pythonRuntime.realpath,
      python_runtime_source: pythonRuntime.source,
      python_runtime_preflight: pythonRuntime.preflight.python_major_minor,
      playwright_version: playwrightPackage.version,
      chromium_version: browserVersion,
    })}\n`);
  } catch (_error) {
    throw new Error("BROWSER_DEPENDENCY_UNAVAILABLE");
  } finally {
    await browser?.close();
  }
}

run().catch((error) => {
  process.stderr.write(`${error.message || "BROWSER_DEPENDENCY_UNAVAILABLE"}\n`);
  process.exitCode = 1;
});
