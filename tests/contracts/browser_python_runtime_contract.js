const assert = require("assert");

const {
  PythonRuntimeError,
  pythonArgs,
  resolvePythonRuntime,
} = require("../../scripts/python_runtime");


function fakeSpawn(identities, { preflight = "3.11" } = {}) {
  return (command, args) => {
    if (args.includes("-c")) {
      const identity = identities[command];
      if (!identity) {
        return { status: 1, stdout: "", stderr: "not found" };
      }
      return {
        status: 0,
        stdout: `${JSON.stringify(identity)}\n`,
        stderr: "",
      };
    }
    if (args.includes("scripts.reproducibility_preflight")) {
      return {
        status: 0,
        stdout: `${JSON.stringify({ python_major_minor: preflight })}\n`,
        stderr: "",
      };
    }
    throw new Error(`unexpected invocation: ${command} ${args.join(" ")}`);
  };
}


function identity(executable, version = "3.11.9") {
  return {
    executable,
    prefix: "/runtime",
    base_prefix: "/base",
    version,
    version_info: version.split(".").map(Number),
  };
}


{
  const runtime = resolvePythonRuntime({
    env: {
      INTERVIEW_RUNTIME_PYTHON: "project-python",
      STAGE41_PYTHON: "legacy-python",
    },
    platform: "linux",
    spawnSyncImpl: fakeSpawn({
      "project-python": identity("/runtime/python"),
      "legacy-python": identity("/runtime/python"),
    }),
    realpathSyncImpl: (value) => value,
  });
  assert.strictEqual(runtime.command, "project-python");
  assert.strictEqual(runtime.source, "INTERVIEW_RUNTIME_PYTHON");
  assert.strictEqual(runtime.realpath, "/runtime/python");
  assert.strictEqual(runtime.preflight.python_major_minor, "3.11");
  assert.deepStrictEqual(
    pythonArgs(runtime, ["-m", "uvicorn"]),
    ["-m", "uvicorn"],
  );
}


{
  const runtime = resolvePythonRuntime({
    env: {},
    platform: "linux",
    spawnSyncImpl: fakeSpawn({
      "python3.11": identity("/usr/bin/python3.11", "3.11.12"),
      python: identity("/usr/bin/python", "3.8.20"),
    }),
    realpathSyncImpl: (value) => value,
  });
  assert.strictEqual(runtime.command, "python3.11");
  assert.strictEqual(runtime.source, "PATH_python3.11");
}


assert.throws(
  () => resolvePythonRuntime({
    env: { INTERVIEW_RUNTIME_PYTHON: "python38" },
    platform: "linux",
    spawnSyncImpl: fakeSpawn({
      python38: identity("/runtime/python38", "3.8.20"),
    }),
    realpathSyncImpl: (value) => value,
  }),
  (error) => (
    error instanceof PythonRuntimeError
    && error.code === "PYTHON_VERSION_UNSUPPORTED"
  ),
);


assert.throws(
  () => resolvePythonRuntime({
    env: {
      INTERVIEW_RUNTIME_PYTHON: "project-python",
      STAGE41_PYTHON: "legacy-python",
    },
    platform: "win32",
    spawnSyncImpl: fakeSpawn({
      "project-python": identity("C:\\Python311\\python.exe"),
      "legacy-python": identity("D:\\Python311\\python.exe"),
    }),
    realpathSyncImpl: (value) => value,
  }),
  (error) => (
    error instanceof PythonRuntimeError
    && error.code === "PYTHON_RUNTIME_IDENTITY_MISMATCH"
  ),
);


assert.throws(
  () => resolvePythonRuntime({
    env: { INTERVIEW_RUNTIME_PYTHON: "project-python" },
    platform: "linux",
    spawnSyncImpl: fakeSpawn(
      { "project-python": identity("/runtime/python") },
      { preflight: "3.8" },
    ),
    realpathSyncImpl: (value) => value,
  }),
  (error) => (
    error instanceof PythonRuntimeError
    && error.code === "PYTHON_RUNTIME_PREFLIGHT_MISMATCH"
  ),
);


process.stdout.write("browser python runtime contract: PASS\n");
