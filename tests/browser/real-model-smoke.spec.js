const { test, expect } = require("@playwright/test");
const { spawn, spawnSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs/promises");

let reportWorker;
let webServer;

function terminateProcessTree(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], {
      windowsHide: true,
    });
    return;
  }
  child.kill();
}

async function waitForServer(url, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      // The server is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`web server did not become ready: ${url}`);
}

test.beforeAll(async () => {
  if (process.env.RUN_REAL_BROWSER_SMOKE !== "1") return;
  const python = process.env.STAGE41_PYTHON || "python";
  const runtimePrefix = process.env.STAGE42_REAL_BROWSER_PREFIX || "stage42_real_browser";
  const runtimeEnv = {
    ...process.env,
    INTERVIEW_RUNTIME_STORE: "postgres",
    INTERVIEW_RUNTIME_TABLE_PREFIX: runtimePrefix,
    INTERVIEW_EVENT_BACKEND: "local",
    REPORT_WORKER_ID: `${runtimePrefix}_worker`,
    OPENAI_REQUEST_TIMEOUT_SECONDS:
      process.env.OPENAI_REQUEST_TIMEOUT_SECONDS || "75",
    OPENAI_MAX_RETRIES: process.env.OPENAI_MAX_RETRIES || "0",
    OPENAI_REPORT_OUTPUT_MODE: process.env.OPENAI_REPORT_OUTPUT_MODE || "raw_only",
  };
  const workerCode = [
    "from app.services.report_worker import run_forever",
    "from app.services.runtime import get_report_executor, get_report_job_store",
    "executor = get_report_executor()",
    "job_store = get_report_job_store()",
    "print('stage42-report-worker-ready', flush=True)",
    "run_forever(executor=executor, job_store=job_store)",
  ].join("; ");
  reportWorker = spawn(python, ["-u", "-c", workerCode], {
    cwd: process.cwd(),
    env: runtimeEnv,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  webServer = spawn(
    python,
    ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8012"],
    {
      cwd: process.cwd(),
      env: runtimeEnv,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  reportWorker.stderr.on("data", (chunk) => process.stderr.write(`[ReportWorker] ${chunk}`));
  webServer.stderr.on("data", (chunk) => process.stderr.write(`[WebServer] ${chunk}`));
  const workerReady = new Promise((resolve, reject) => {
    const timeout = setTimeout(
      () => reject(new Error("report worker did not become ready")),
      120_000,
    );
    reportWorker.once("error", reject);
    reportWorker.once("exit", (code) => {
      if (code !== null) reject(new Error(`report worker exited with code ${code}`));
    });
    reportWorker.stdout.on("data", (chunk) => {
      if (!chunk.toString().includes("stage42-report-worker-ready")) return;
      clearTimeout(timeout);
      resolve();
    });
  });
  await Promise.all([
    workerReady,
    waitForServer("http://127.0.0.1:8012/api/health"),
  ]);
});

test.afterAll(() => {
  terminateProcessTree(reportWorker);
  terminateProcessTree(webServer);
});

test("fresh provider preserves Stage 42 evidence through report and PDF", async ({ page, request }, testInfo) => {
  test.skip(process.env.RUN_REAL_BROWSER_SMOKE !== "1", "explicit provider opt-in required");

  await page.goto("/prep");
  await page.locator("#jobDescription").fill(
    "Backend engineer using Python, FastAPI, Redis, PostgreSQL, queues, and observability.",
  );
  await page.locator("#resumeText").fill(
    "Built FastAPI services with Redis cache-aside, PostgreSQL indexes, async jobs, and incident reviews.",
  );
  await page.locator("#prepButton").click();
  await expect(page.locator("#startButton")).toBeEnabled({ timeout: 90_000 });
  await expect.poll(() => page.locator("#planQuestions li").count()).toBeGreaterThanOrEqual(3);
  await page.screenshot({ path: testInfo.outputPath("stage42-real-prep.png"), fullPage: true });
  await page.locator("#startButton").click();
  await expect(page).toHaveURL(/\/interview\?session_id=/, { timeout: 30_000 });
  const sessionId = new URL(page.url()).searchParams.get("session_id");
  const initialSnapshot = await (await request.get(`/api/interviews/${sessionId}`)).json();
  const initialBindings = Object.fromEntries(
    (initialSnapshot.prep_context?.question_hints || []).map((hint) => [
      hint.question_id,
      hint.evidence_ids,
    ]),
  );

  for (const answer of [
    "I used cache-aside with explicit TTLs, request coalescing, and database fallback.",
    "The trade-off was stale reads, so writes invalidated keys and metrics tracked hit rate and fallback latency.",
    "For PostgreSQL I used composite indexes based on query predicates and verified plans with EXPLAIN ANALYZE.",
    "I monitored lock waits and slow queries, then used bounded retries only for transient serialization failures.",
  ]) {
    await page.locator("#answerInput").fill(answer);
    await page.locator("#sendAnswerButton").click();
    await expect(page.locator("#sendAnswerButton")).toBeEnabled({ timeout: 90_000 });
  }

  const answeredSnapshot = await (await request.get(`/api/interviews/${sessionId}`)).json();
  const candidateMessages = answeredSnapshot.messages.filter((item) => item.role === "candidate");
  const interviewerCounts = answeredSnapshot.messages
    .filter((item) => item.role === "interviewer")
    .reduce((counts, item) => {
      counts[item.question_id] = (counts[item.question_id] || 0) + 1;
      return counts;
    }, {});
  expect(new Set(candidateMessages.map((item) => item.question_id)).size).toBeGreaterThanOrEqual(2);
  expect(Object.values(interviewerCounts).some((count) => count > 1)).toBe(true);

  await page.locator("#finishInterviewButton").click();
  await expect(page).toHaveURL(/\/report-detail\?session_id=/, { timeout: 600_000 });
  await expect(page.locator("body")).toContainText(/\b[0-9]{1,3}\b/);
  await page.screenshot({ path: testInfo.outputPath("stage42-real-report.png"), fullPage: true });

  const finalSnapshot = await (await request.get(`/api/interviews/${sessionId}`)).json();
  const finalBindings = Object.fromEntries(
    (finalSnapshot.prep_context?.question_hints || []).map((hint) => [
      hint.question_id,
      hint.evidence_ids,
    ]),
  );
  expect(finalBindings).toEqual(initialBindings);

  const evaluationsResponse = await request.get(
    `/api/interviews/${sessionId}/question-evaluations`,
  );
  expect(evaluationsResponse.status()).toBe(200);
  const evaluations = (await evaluationsResponse.json()).items;
  expect(evaluations.length).toBeGreaterThanOrEqual(2);
  expect(evaluations.every((item) => item.retrieval_path === "bound_evidence_ids")).toBe(true);
  expect(JSON.stringify(evaluations)).not.toContain("evidence_content_sha256");

  const reportResponse = await request.get(`/api/interviews/${sessionId}/report`);
  expect(reportResponse.status()).toBe(200);
  const report = await reportResponse.json();
  for (const feedback of report.feedbacks) {
    const expectedIds = initialBindings[feedback.question_id] || [];
    const referenceIds = feedback.references.map((reference) => reference.chunk_id);
    expect(referenceIds.length).toBeGreaterThan(0);
    expect(referenceIds.every((evidenceId) => expectedIds.includes(evidenceId))).toBe(true);
  }

  const pdf = await request.get(`/api/interviews/${sessionId}/report.pdf`);
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toContain("application/pdf");
  expect((await pdf.body()).length).toBeGreaterThan(1000);

  const runSummary = {
    run_at: new Date().toISOString(),
    session_ref: crypto.createHash("sha256").update(sessionId).digest("hex").slice(0, 16),
    model: process.env.OPENAI_MODEL || "provider-default",
    distinct_questions_answered: new Set(candidateMessages.map((item) => item.question_id)).size,
    follow_up_observed: Object.values(interviewerCounts).some((count) => count > 1),
    evidence_ids: [...new Set(Object.values(initialBindings).flat())].sort(),
    evidence_continuity: true,
  };
  await fs.writeFile(
    testInfo.outputPath("stage42-real-run.json"),
    `${JSON.stringify(runSummary, null, 2)}\n`,
    "utf8",
  );
});
