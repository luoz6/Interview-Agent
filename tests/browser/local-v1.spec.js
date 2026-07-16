const { test, expect } = require("@playwright/test");
const { spawnSync } = require("child_process");
const path = require("path");

const jd = "Backend role using Python, FastAPI, Redis, and PostgreSQL.";
const resume = "Built a FastAPI service with Redis cache-aside and PostgreSQL.";

async function startInterview(page) {
  await page.goto("/prep");
  await page.locator("#jobDescription").fill(jd);
  await page.locator("#resumeText").fill(resume);
  await page.locator("#prepButton").click();
  await expect(page.locator("#planQuestions li")).toHaveCount(3);
  await expect(page.locator("#prepKnowledgeStatus")).toHaveText("知识检索完成");
  await expect(page.locator('[data-evidence-id="redis_consistency"]').first()).toBeVisible();
  const prepEvidenceIds = await page.locator("[data-evidence-id]").evaluateAll((items) => (
    [...new Set(items.map((item) => item.dataset.evidenceId))].sort()
  ));
  await page.locator("#startButton").click();
  await expect(page).toHaveURL(/\/interview\?session_id=/);
  await expect(page.locator("#sessionStatus")).toHaveText("active");
  return {
    sessionId: new URL(page.url()).searchParams.get("session_id"),
    prepEvidenceIds,
  };
}

test("prep, SSE answer, refresh, conflict recovery, report and PDF", async ({ page, request }) => {
  const { sessionId, prepEvidenceIds } = await startInterview(page);

  const persistedPrep = await request.get(`/api/interviews/${sessionId}`);
  const persistedBody = await persistedPrep.json();
  const persistedEvidenceIds = persistedBody.prep_context.evidence_refs
    .map((item) => item.evidence_id)
    .sort();
  expect(persistedEvidenceIds).toEqual(prepEvidenceIds);
  expect(JSON.stringify(persistedBody.prep_context)).not.toContain("content_sha256");

  await page.locator("#answerInput").fill("I used cache-aside and database fallback.");
  await page.locator("#sendAnswerButton").click();
  await expect(page.locator("#conversation")).toContainText("trade-off");

  await page.reload();
  await expect(page.locator("#conversation")).toContainText("cache-aside");

  const snapshot = await request.get(`/api/interviews/${sessionId}`);
  const version = (await snapshot.json()).state_version;
  await request.post(`/api/interviews/${sessionId}/skip`, {
    data: { expected_version: version, command_id: "external-skip" },
  });

  await page.locator("#answerInput").fill("Keep this answer after conflict.");
  await page.locator("#sendAnswerButton").click();
  await expect(page.locator("#interviewNotice")).toContainText("会话状态已刷新");
  await expect(page.locator("#answerInput")).toHaveValue("Keep this answer after conflict.");

  await page.locator("#finishInterviewButton").click();
  await expect(page).toHaveURL(/\/report-detail\?session_id=/, { timeout: 10_000 });
  await expect(page.locator("body")).toContainText("82");
  await expect(page.locator('[data-evidence-id="redis_consistency"]')).toBeVisible();
  await expect(page.locator("body")).toContainText("Knowledge evidence: Prep binding reused");

  const evaluations = await request.get(`/api/interviews/${sessionId}/question-evaluations`);
  const evaluationBody = await evaluations.json();
  expect(evaluationBody.items[0].retrieval_path).toBe("bound_evidence_ids");
  expect(JSON.stringify(evaluationBody)).not.toContain("evidence_content_sha256");

  const reportResponse = await request.get(`/api/interviews/${sessionId}/report`);
  const reportBody = await reportResponse.json();
  expect(reportBody.feedbacks[0].references.map((item) => item.chunk_id)).toEqual([
    "redis_consistency",
  ]);

  const progressResponse = await request.get(`/api/interviews/${sessionId}/report/progress`);
  const progressBody = await progressResponse.json();
  expect(progressBody.metadata.knowledge_path).toBe("bound_evidence_reuse");

  const pdf = await request.get(`/api/interviews/${sessionId}/report.pdf`);
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toContain("application/pdf");
  expect((await pdf.body()).length).toBeGreaterThan(1000);

  const prepRunResponse = await request.get(
    `/test-support/interviews/${sessionId}/prep-run-id`,
  );
  expect(prepRunResponse.status()).toBe(200);
  const { prep_run_id: prepRunId } = await prepRunResponse.json();
  const correlationDir = path.join(process.env.AGENT_TRACE_DIR, prepRunId);
  const audit = spawnSync(
    process.env.STAGE41_PYTHON || "python",
    ["-m", "scripts.audit_agent_runtime", correlationDir],
    {
      cwd: process.cwd(),
      encoding: "utf8",
    },
  );
  expect(audit.status, audit.stdout + audit.stderr).toBe(0);
  const auditResult = JSON.parse(audit.stdout);
  expect(auditResult.status).toBe("PASS");
  expect(auditResult.correlation_continuity_rate).toBe(1);
  expect(auditResult.required_agents_present).toBe(true);
  expect(auditResult.privacy_violations).toEqual([]);
});

test("prep evidence remains visible and bounded on mobile", async ({ page }) => {
  await page.goto("/prep");
  await page.locator("#jobDescription").fill(jd);
  await page.locator("#resumeText").fill(resume);
  await page.locator("#prepButton").click();

  await expect(page.locator("#prepKnowledgeStatus")).toHaveText("知识检索完成");
  await expect(page.locator('[data-evidence-id="redis_consistency"]').first()).toBeVisible();
  const widths = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
  }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
  await expect(page.locator("body")).not.toContainText("Internal benchmark answer");
});

test("degraded knowledge is explicit and still completes without references", async ({ page, request }) => {
  await page.goto("/prep");
  await page.locator("#jobDescription").fill("Backend Redis role simulate degraded");
  await page.locator("#resumeText").fill("Built Redis APIs");
  await page.locator("#prepButton").click();

  await expect(page.locator("#prepKnowledgeStatus")).toHaveText("知识检索降级");
  await expect(page.locator("#planQuestions")).toContainText("本题未附加可信知识依据");
  await expect(page.locator("[data-evidence-id]")).toHaveCount(0);

  await page.locator("#startButton").click();
  await expect(page).toHaveURL(/\/interview\?session_id=/);
  const sessionId = new URL(page.url()).searchParams.get("session_id");
  await page.locator("#finishInterviewButton").click();
  await expect(page).toHaveURL(/\/report-detail\?session_id=/, { timeout: 10_000 });
  await expect(page.locator("body")).toContainText("Knowledge evidence: degraded (missing_evidence_binding)");
  await expect(page.locator("[data-evidence-id]")).toHaveCount(0);

  const reportResponse = await request.get(`/api/interviews/${sessionId}/report`);
  const reportBody = await reportResponse.json();
  expect(reportBody.feedbacks[0].references).toEqual([]);
});

test("missing session pages expose safe errors", async ({ page }) => {
  await page.goto("/interview");
  await expect(page.locator("#interviewNotice")).toContainText("缺少 session_id");
  await expect(page.locator("#sendAnswerButton")).toBeDisabled();

  await page.goto("/report-detail?session_id=missing");
  await expect(page.locator("#reportNotice")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("Traceback");
});
