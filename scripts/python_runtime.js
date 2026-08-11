const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
const PROBE_CODE = [
  "import fastapi, json, platform, sys, uvicorn",
  "print(json.dumps({'executable': sys.executable, "
    + "'prefix': sys.prefix, 'base_prefix': sys.base_prefix, "
    + "'version': platform.python_version(), "
    + "'version_info': list(sys.version_info[:3])}, sort_keys=True))",
].join("; ");

class PythonRuntimeError extends Error {
  constructor(code, detail = "") {
    super(detail ? `${code}: ${detail}` : code);
    this.name = "PythonRuntimeError";
    this.code = code;
  }
}

function _candidate(command, prefixArgs, source, explicit = false) {
  return { command, prefixArgs, source, explicit };
}

function pythonCandidates({ env = process.env, platform = process.platform } = {}) {
  const candidates = [];
  const canonical = (env.INTERVIEW_RUNTIME_PYTHON || "").trim();
  const legacy = (env.STAGE41_PYTHON || "").trim();
  if (canonical) {
    candidates.push(_candidate(canonical, [], "INTERVIEW_RUNTIME_PYTHON", true));
  } else if (legacy) {
    candidates.push(_candidate(legacy, [], "STAGE41_PYTHON", true));
  }

  const virtualEnv = (env.VIRTUAL_ENV || "").trim();
  if (virtualEnv) {
    candidates.push(
      _candidate(
        path.join(
          virtualEnv,
          platform === "win32" ? "Scripts" : "bin",
          platform === "win32" ? "python.exe" : "python",
        ),
        [],
        "VIRTUAL_ENV",
      ),
    );
  }

  candidates.push(
    _candidate(
      path.join(
        ROOT,
        ".venv",
        platform === "win32" ? "Scripts" : "bin",
        platform === "win32" ? "python.exe" : "python",
      ),
      [],
      "workspace_venv",
    ),
  );
  if (platform === "win32") {
    candidates.push(_candidate("py", ["-3.11"], "windows_launcher"));
  }
  candidates.push(_candidate("python3.11", [], "PATH_python3.11"));
  candidates.push(_candidate("python", [], "PATH_python"));

  const seen = new Set();
  return candidates.filter((item) => {
    const key = JSON.stringify([item.command, item.prefixArgs]);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function _runJson(candidate, args, {
  cwd,
  env,
  spawnSyncImpl,
}) {
  const result = spawnSyncImpl(
    candidate.command,
    [...candidate.prefixArgs, ...args],
    {
      cwd,
      env,
      encoding: "utf8",
      windowsHide: true,
      shell: false,
    },
  );
  if (result.error || result.status !== 0) {
    const detail = result.error?.message || String(result.stderr || "").trim();
    throw new PythonRuntimeError("PYTHON_RUNTIME_UNAVAILABLE", detail);
  }
  try {
    return JSON.parse(String(result.stdout || "").trim());
  } catch (_error) {
    throw new PythonRuntimeError(
      "PYTHON_RUNTIME_IDENTITY_INVALID",
      "interpreter returned invalid identity JSON",
    );
  }
}

function _probeCandidate(candidate, options) {
  const identity = _runJson(candidate, ["-c", PROBE_CODE], options);
  if (
    !Array.isArray(identity.version_info)
    || identity.version_info.length < 2
    || identity.version_info[0] !== 3
    || identity.version_info[1] !== 11
  ) {
    throw new PythonRuntimeError(
      "PYTHON_VERSION_UNSUPPORTED",
      `Python 3.11 is required; found ${identity.version || "unknown"}`,
    );
  }
  if (!identity.executable || typeof identity.executable !== "string") {
    throw new PythonRuntimeError(
      "PYTHON_RUNTIME_IDENTITY_INVALID",
      "sys.executable is missing",
    );
  }
  let realpath;
  try {
    realpath = options.realpathSyncImpl(identity.executable);
  } catch (error) {
    throw new PythonRuntimeError(
      "PYTHON_RUNTIME_IDENTITY_INVALID",
      error.message,
    );
  }
  return {
    ...candidate,
    executable: identity.executable,
    realpath,
    version: identity.version,
    prefix: identity.prefix,
    basePrefix: identity.base_prefix,
  };
}

function _normalizedIdentity(value, platform) {
  const normalized = path.normalize(value);
  return platform === "win32" ? normalized.toLowerCase() : normalized;
}

function resolvePythonRuntime({
  env = process.env,
  platform = process.platform,
  cwd = ROOT,
  spawnSyncImpl = spawnSync,
  realpathSyncImpl = fs.realpathSync,
} = {}) {
  const options = { cwd, env, spawnSyncImpl, realpathSyncImpl };
  const candidates = pythonCandidates({ env, platform });
  let runtime = null;
  let lastError = null;
  for (const candidate of candidates) {
    try {
      runtime = _probeCandidate(candidate, options);
      break;
    } catch (error) {
      lastError = error;
      if (candidate.explicit) throw error;
    }
  }
  if (!runtime) {
    throw lastError || new PythonRuntimeError("PYTHON_RUNTIME_UNAVAILABLE");
  }

  const canonical = (env.INTERVIEW_RUNTIME_PYTHON || "").trim();
  const legacy = (env.STAGE41_PYTHON || "").trim();
  if (canonical && legacy) {
    const legacyRuntime = _probeCandidate(
      _candidate(legacy, [], "STAGE41_PYTHON", true),
      options,
    );
    if (
      _normalizedIdentity(runtime.realpath, platform)
      !== _normalizedIdentity(legacyRuntime.realpath, platform)
    ) {
      throw new PythonRuntimeError(
        "PYTHON_RUNTIME_IDENTITY_MISMATCH",
        "INTERVIEW_RUNTIME_PYTHON and STAGE41_PYTHON resolve differently",
      );
    }
  }

  const preflight = _runJson(
    runtime,
    ["-m", "scripts.reproducibility_preflight", "--python-only"],
    options,
  );
  if (preflight.python_major_minor !== "3.11") {
    throw new PythonRuntimeError(
      "PYTHON_RUNTIME_PREFLIGHT_MISMATCH",
      "reproducibility preflight did not confirm Python 3.11",
    );
  }

  return {
    command: runtime.command,
    prefixArgs: runtime.prefixArgs,
    source: runtime.source,
    executable: runtime.executable,
    realpath: runtime.realpath,
    version: runtime.version,
    preflight,
  };
}

function pythonArgs(runtime, args) {
  return [...runtime.prefixArgs, ...args];
}

module.exports = {
  PythonRuntimeError,
  pythonArgs,
  pythonCandidates,
  resolvePythonRuntime,
};
