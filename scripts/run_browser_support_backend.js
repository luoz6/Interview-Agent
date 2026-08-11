const { spawn } = require("child_process");

const { pythonArgs, resolvePythonRuntime } = require("./python_runtime");


const runtime = resolvePythonRuntime();
const child = spawn(
  runtime.command,
  pythonArgs(runtime, [
    "-m",
    "uvicorn",
    "tests.browser_support_app:app",
    "--host",
    "127.0.0.1",
    "--port",
    "8011",
  ]),
  {
    cwd: process.cwd(),
    env: {
      ...process.env,
      INTERVIEW_RUNTIME_PYTHON: runtime.executable,
    },
    shell: false,
    stdio: "inherit",
    windowsHide: true,
  },
);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    if (child.exitCode === null) child.kill(signal);
  });
}

child.once("error", (error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});

child.once("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exitCode = code ?? 1;
});
