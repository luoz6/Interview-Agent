const { test, expect } = require("@playwright/test");
const {
  createCompletedReport,
  desktopOnly,
  expectGeometry,
  seedReport,
} = require("./reference-ui-geometry");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});

function reliability(overrides = {}) {
  return {
    planned_question_count: 3,
    answered_question_count: 1,
    skipped_question_count: 2,
    unanswered_question_count: 0,
    reviewed_answer_count: 1,
    review_failed_answer_count: 0,
    evidence_bound_question_count: 1,
    degraded_question_count: 0,
    generation_path: "structured",
    degraded_reasons: [],
    score_applicability: "normal",
    ...overrides,
  };
}

function practiceReadyReport(baseline) {
  const dimensions = {
    breadth: 78,
    depth: 72,
    architecture: 70,
    engineering: 54,
    communication: 76,
  };
  return {
    ...baseline,
    is_fallback: false,
    overall_score: 69,
    overall_dimension_scores: dimensions,
    reliability: reliability(),
    feedbacks: [{
      question_id: "q1",
      question_text: "如何设计一个可恢复的缓存服务？",
      user_answer: "使用 cache-aside，并设置超时和回源保护。",
      answer_state: "answered",
      score: 62,
      dimension_scores: dimensions,
      applicable_dimensions: ["engineering"],
      rationale: "说明了基本路径。",
      critique: "缺少回滚与观测指标。",
      better_answer: "补充失败边界、监控和回滚。",
      references: [],
      dimension_evidence: [],
    }],
  };
}

test("report progress product mode shows only authoritative user-facing state", async ({ page, request }) => {
  const seeded = await seedReport(request, "processing");
  await page.goto(`/report-processing?session_id=${seeded.session_id}`);
  await expect(page.locator(".processing-progress-panel")).toBeVisible();
  await expect(page.locator(".processing-away-card")).toContainText("不必停留在此页");
  await expect(page.locator(".processing-facts")).toContainText("最近更新");
  await expect(page.locator(".processing-facts")).toContainText("已等待");
  await expect(page.locator(".processing-events, .processing-status-bar, .processing-diagnostics")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("持久化事件");
  await expect(page.locator("body")).not.toContainText("任务 ID");
  await expect(page.locator("body")).not.toContainText("执行尝试");
  await expect(page.locator("body")).not.toContainText("最近心跳");
});

test("report reliability states remain explicit and compatibility lowers score authority", async ({ page, request }) => {
  test.setTimeout(60_000);
  const sessionId = await createCompletedReport(request);
  const response = await request.get(`/api/interviews/${sessionId}/report`);
  expect(response.ok()).toBe(true);
  const baseline = practiceReadyReport(await response.json());
  let payload = baseline;
  await page.route(`**/api/interviews/${sessionId}/report`, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(payload),
  }));

  const cases = [
    ["normal", "依据完整", reliability()],
    ["limited", "部分依据受限", reliability({ score_applicability: "limited", generation_path: "mixed", degraded_question_count: 1, degraded_reasons: ["KNOWLEDGE_RETRIEVAL_DEGRADED"] })],
    ["insufficient", "依据不足", reliability({ score_applicability: "insufficient", reviewed_answer_count: 0, evidence_bound_question_count: 0, degraded_question_count: 1, generation_path: "fallback", degraded_reasons: ["REPORT_FALLBACK"] })],
  ];

  for (const [state, label, nextReliability] of cases) {
    payload = { ...baseline, reliability: nextReliability };
    await page.goto(`/report-detail?session_id=${sessionId}`);
    await expect(page.locator(".report-detail-overview")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".report-detail-overview")).toHaveAttribute("data-score-applicability", state);
    await expect(page.locator(".report-detail-reliability")).toContainText(label);
    await expect(page.locator(".report-detail-score-mark")).toContainText("不代表录用概率");
  }

  payload = { ...baseline };
  delete payload.reliability;
  await page.goto(`/report-detail?session_id=${sessionId}`);
  await expect(page.locator(".report-detail-overview")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".report-detail-overview")).toHaveAttribute("data-score-applicability", "compatibility");
  await expect(page.locator(".report-detail-reliability")).toContainText("旧版兼容报告");
  await expect(page.getByRole("button", { name: "创建针对性练习" })).toBeDisabled();
});

test("targeted practice opens the authoritative editable plan", async ({ page, request }) => {
  const sessionId = await createCompletedReport(request);
  const response = await request.get(`/api/interviews/${sessionId}/report`);
  const report = practiceReadyReport(await response.json());
  const planId = "3ad42c35-60d4-47bd-923a-0fd5f87ef802";
  let requestBody;

  await page.route(`**/api/interviews/${sessionId}/report`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(report) }));
  await page.route(`**/api/interviews/${sessionId}/practice-plan`, async (route) => {
    requestBody = route.request().postDataJSON();
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ plan_id: planId }),
    });
  });
  await page.route(`**/api/prep-plans/${planId}`, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      plan_id: planId,
      plan_version: 1,
      state: "editable",
      expires_at: "2026-08-06T12:00:00Z",
      source_sha256: "practice-source",
      title: "工程实践针对性练习",
      questions: [1, 2, 3].map((position) => ({
        question_id: `pq-${position}`,
        position,
        kind: "technical",
        prompt: `针对性练习 ${position}`,
        focus: "工程实践针对性复盘",
        required: false,
        enabled: true,
        source_signals: [],
        topic_labels: [],
        evidence_ids: [],
      })),
      prep_context: {},
      job_tags: ["reliability"],
      durability: "memory",
      practice_provenance: {
        source_session_id: sessionId,
        source_session_question_ids: ["q1"],
        source_plan_question_ids: ["pq-source-1"],
        source_report_id: sessionId,
        focus_dimension: "engineering",
      },
    }),
  }));

  await page.goto(`/report-detail?session_id=${sessionId}`);
  await page.getByRole("button", { name: "创建针对性练习" }).click();
  await expect(page).toHaveURL(new RegExp(`/prep\\?plan_id=${planId}`));
  await expect(page.getByRole("heading", { name: "工程实践针对性练习" })).toBeVisible();
  expect(requestBody).toEqual({
    focus_dimension: "engineering",
    session_question_ids: ["q1"],
    mode: "targeted",
  });
});

test("phase four report routes reflow at portrait, landscape and 200 percent equivalent widths", async ({ page, request }) => {
  test.setTimeout(90_000);
  const processing = await seedReport(request, "processing");
  const completed = await createCompletedReport(request);
  const routes = [
    `/report-processing?session_id=${processing.session_id}`,
    `/report-detail?session_id=${completed}`,
    "/reports",
    "/help",
  ];
  const viewports = [
    { width: 390, height: 844 },
    { width: 844, height: 390 },
    { width: 640, height: 900 },
  ];

  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const route of routes) {
      await page.goto(route);
      await expectGeometry(page);
      const state = await page.evaluate(() => ({
        horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
        visibleMain: document.querySelector("#main-content")?.getBoundingClientRect().height > 0,
        duplicateInspector: document.querySelectorAll(".reports-inspector, .help-inspector").length,
        duplicateStatus: document.querySelectorAll(".reports-status-bar, .help-status-bar").length,
      }));
      expect(state.horizontalOverflow).toBe(false);
      expect(state.visibleMain).toBe(true);
      expect(state.duplicateInspector).toBe(0);
      expect(state.duplicateStatus).toBe(0);
    }
  }
});
