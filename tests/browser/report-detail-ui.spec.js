const { test, expect } = require("@playwright/test");
const {
  createCompletedReport,
  desktopOnly,
  expectGeometry,
  seedReport,
  viewports,
} = require("./browser-suite-support");

const diagnosticsEnabled = process.env.VITE_SHOW_RUNTIME_DIAGNOSTICS === "true";

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});

function reportReliability(overrides = {}) {
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
    reliability: reportReliability(),
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

test("report detail uses the shared Calm Cobalt workbench across viewports", async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const sessionId = await createCompletedReport(request);

  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: 900 });
    await page.goto("/report-detail?session_id=" + sessionId);
    await expect(page.locator(".report-detail-app")).toBeVisible();
    await expect(page.locator(".report-detail-inspector")).toBeVisible();
    await expect(page.locator(".report-detail-score-mark")).toBeVisible();
    await expectGeometry(page);

    const detail = await page.evaluate(() => {
      const shell = document.querySelector(".report-detail-shell");
      const workspace = document.querySelector(".report-detail-workspace");
      const inspector = document.querySelector(".report-detail-inspector");
      const overview = document.querySelector(".report-detail-overview");
      const score = document.querySelector(".report-detail-score-mark");
      const title = document.querySelector("#report-detail-title");
      const referencePanel = document.querySelector(".report-detail-dimension-panel");
      const workspaceRect = workspace.getBoundingClientRect();
      const inspectorRect = inspector.getBoundingClientRect();
      return {
        shellDisplay: getComputedStyle(shell).display,
        shellColumns: getComputedStyle(shell).gridTemplateColumns
          .split(" ")
          .filter(Boolean).length,
        inspectorBelowWorkspace: inspectorRect.top >= workspaceRect.bottom - 1,
        titleSize: Number.parseFloat(getComputedStyle(title).fontSize),
        overviewBackground: getComputedStyle(overview).backgroundColor,
        scoreBackground: getComputedStyle(score).backgroundImage,
        scoreWidth: score.getBoundingClientRect().width,
        scoreTrackAnimation: getComputedStyle(
          document.querySelector(".report-detail-score-track > span"),
        ).animationName,
        scoreOrbitCount: document.querySelectorAll(
          ".report-detail-score-orbit, .report-detail-head-score",
        ).length,
        dimensionRows: document.querySelectorAll(
          ".report-detail-dimensions > li",
        ).length,
        introRevealCount: document.querySelectorAll(
          "[data-report-reveal]",
        ).length,
        horizontalOverflow:
          document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
        wrappedActionLabels: [...document.querySelectorAll(
          ".report-detail-inspector-actions button span, .report-detail-download-tool span",
        )].filter((label) => {
          const style = getComputedStyle(label);
          return label.getBoundingClientRect().height > Number.parseFloat(style.lineHeight) * 1.2;
        }).length,
        railIconCount: document.querySelectorAll(
          ".report-detail-activity-rail svg",
        ).length,
        sectionIconCount: document.querySelectorAll(
          ".report-detail-section-icon svg",
        ).length,
        scoreAnimations: getComputedStyle(score).animationName,
        dimensionAnimation: getComputedStyle(
          document.querySelector(".report-detail-dimension-track > span"),
        ).animationName,
        referencePanelBackground: getComputedStyle(referencePanel).backgroundColor,
        workspaceColor: getComputedStyle(workspace).color,
        reliabilityVisible: Boolean(document.querySelector(".report-detail-reliability")),
        practiceVisible: Boolean(document.querySelector("#practice")),
        runtimeTraceCount: document.querySelectorAll("#runtime-trace").length,
        evaluationLedgerCount: document.querySelectorAll(".report-detail-evaluation-ledger").length,
        inspectorScoreCount: document.querySelectorAll(".report-detail-inspector-score").length,
        statusBarCount: document.querySelectorAll(".report-detail-status-bar").length,
        legacyTraceIdCount: document.querySelectorAll("#trace").length,
        traceEmptyCount: document.querySelectorAll(
          '.report-detail-trace-empty[data-state="empty"]',
        ).length,
        traceGridCount: document.querySelectorAll(
          ".report-detail-trace-grid",
        ).length,
        primaryCount: document.querySelectorAll(
          ".button-primary:not(:disabled)",
        ).length,
        actionHeights: [...document.querySelectorAll(
          ".report-detail-inspector-actions button",
        )].map((button) => button.getBoundingClientRect().height),
        workspaceOverflow: getComputedStyle(
          document.querySelector(".report-detail-workspace-scroll"),
        ).overflowY,
        oldPosterScoreCount: document.querySelectorAll(
          ".overall-score, .score-overview, .highlight-field",
        ).length,
      };
    });

    expect(detail.titleSize).toBeGreaterThanOrEqual(16);
    expect(detail.titleSize).toBeLessThan(32);
    expect(detail.overviewBackground).not.toBe("rgb(255, 113, 89)");
    expect(detail.scoreBackground).toBe("none");
    expect(detail.scoreWidth).toBeGreaterThanOrEqual(180);
    expect(detail.scoreWidth).toBeLessThanOrEqual(320);
    expect(detail.scoreTrackAnimation).toBe("report-detail-score-fill");
    expect(detail.scoreOrbitCount).toBe(0);
    expect(detail.dimensionRows).toBe(5);
    expect(detail.introRevealCount).toBe(4);
    expect(detail.horizontalOverflow).toBe(false);
    expect(detail.wrappedActionLabels).toBe(0);
    expect(detail.railIconCount).toBe(5);
    expect(detail.sectionIconCount).toBe(7);
    expect(detail.scoreAnimations).toContain("report-detail-score-enter");
    expect(detail.dimensionAnimation).toBe("report-detail-dimension-fill");
    expect(detail.referencePanelBackground).not.toBe("");
    expect(detail.workspaceColor).not.toBe("");
    expect(detail.reliabilityVisible).toBe(true);
    expect(detail.practiceVisible).toBe(true);
    expect(detail.runtimeTraceCount).toBe(0);
    expect(detail.evaluationLedgerCount).toBe(0);
    expect(detail.inspectorScoreCount).toBe(0);
    expect(detail.statusBarCount).toBe(0);
    expect(detail.legacyTraceIdCount).toBe(0);
    expect(detail.traceEmptyCount).toBe(0);
    expect(detail.traceGridCount).toBe(0);
    expect(detail.primaryCount).toBeLessThanOrEqual(1);
    expect(detail.actionHeights.every((height) => height >= 44)).toBe(true);
    expect(detail.oldPosterScoreCount).toBe(0);

    if (viewport.width >= 1180) {
      expect(detail.shellDisplay).toBe("grid");
      expect(detail.shellColumns).toBe(3);
      expect(detail.inspectorBelowWorkspace).toBe(false);
      expect(detail.workspaceOverflow).toBe("auto");
    } else if (viewport.width >= 768) {
      expect(detail.shellDisplay).toBe("grid");
      expect(detail.shellColumns).toBe(2);
      expect(detail.inspectorBelowWorkspace).toBe(true);
    } else {
      expect(detail.shellDisplay).toBe("block");
      expect(detail.inspectorBelowWorkspace).toBe(true);
      expect(detail.workspaceOverflow).toBe("visible");
    }
  }

  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/report-detail?session_id=" + sessionId);
  const questionsLink = page.locator('.report-detail-activity-rail a[href="#questions"]');
  await questionsLink.click();
  await expect(page.locator("#questions")).toBeInViewport();
  await expect(questionsLink).toHaveAttribute("aria-current", "location");

  const firstFeedback = page.locator(".report-detail-feedback").first();
  await expect(firstFeedback).toHaveAttribute("open", "");
  await firstFeedback.locator("summary").click();
  await expect(firstFeedback).not.toHaveAttribute("open", "");

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载完整报告" }).click();
  await downloadPromise;
  await expect(page.locator(".report-detail-download-action")).toContainText("下载已开始");
  await expect(firstFeedback).not.toHaveAttribute("open", "");
});

test("report detail error state explains the failure and reloads in place", async ({
  page,
  request,
}) => {
  const sessionId = await createCompletedReport(request);
  let reportRequests = 0;
  await page.route(`**/api/interviews/${sessionId}/report`, async (route) => {
    reportRequests += 1;
    if (reportRequests <= 2) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "报告存储暂时不可用" }),
      });
      return;
    }
    await route.continue();
  });

  await page.goto("/report-detail?session_id=" + sessionId);
  await expect(page.locator(".report-detail-error")).toContainText("报告存储暂时不可用");
  const reload = page.getByRole("button", { name: "重新加载" });
  await expect(reload).toBeEnabled();
  await reload.click();
  await expect(page.locator(".report-detail-score-mark")).toBeVisible();
  expect(reportRequests).toBeGreaterThanOrEqual(3);
});

test("product mode does not request or render runtime diagnostics", async ({
  page,
  request,
}) => {
  const sessionId = await createCompletedReport(request);
  let diagnosticRequests = 0;
  await page.route(`**/api/interviews/${sessionId}/agent-runs?limit=100`, (route) => { diagnosticRequests += 1; return route.abort(); });
  await page.route(`**/api/interviews/${sessionId}/runtime-events?limit=100`, (route) => { diagnosticRequests += 1; return route.abort(); });
  await page.route(`**/api/interviews/${sessionId}/question-evaluations`, (route) => { diagnosticRequests += 1; return route.abort(); });

  await page.goto("/report-detail?session_id=" + sessionId);
  await expect(page.locator(".report-detail-score-mark")).toBeVisible();
  expect(diagnosticRequests).toBe(0);
  await expect(page.locator("#runtime-trace")).toHaveCount(0);
  await expect(page.locator(".report-detail-evaluation-ledger")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("运行轨迹");
});

test("report detail motion and focus states remain accessible", async ({
  page,
  request,
}) => {
  const sessionId = await createCompletedReport(request);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/report-detail?session_id=" + sessionId);

  const download = page.getByRole("button", { name: "下载完整报告" });
  await download.focus();
  const focus = await download.evaluate((button) => {
    const style = getComputedStyle(button);
    return {
      style: style.outlineStyle,
      width: Number.parseFloat(style.outlineWidth),
    };
  });
  expect(focus.style).toBe("solid");
  expect(focus.width).toBeGreaterThanOrEqual(2);

  const durations = await page.locator(".report-detail-app").evaluate((root) => {
    const toMilliseconds = (value) => value.split(",").map((part) => {
      const duration = Number.parseFloat(part);
      return part.trim().endsWith("ms") ? duration : duration * 1000;
    });
    const samples = [
      root.querySelector(".report-detail-score-mark"),
      root.querySelector(".report-detail-dimension"),
      root.querySelector(".report-detail-feedback-caret"),
      root.querySelector(".report-detail-trace-empty-icon"),
    ].filter(Boolean);
    return samples.flatMap((element) => {
      const style = getComputedStyle(element);
      return [
        ...toMilliseconds(style.animationDuration),
        ...toMilliseconds(style.transitionDuration),
      ];
    });
  });
  expect(Math.max(...durations)).toBeLessThanOrEqual(0.02);
});

test("React report detail shows only safe runtime fields and tracks sections", async ({ page, request }) => {
  const sessionId = await createCompletedReport(request);
  await page.route(`**/api/interviews/${sessionId}/agent-runs?limit=100`, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      items: [{
        run_id: "run-safe-1",
        agent: "reviewer",
        operation: "evaluate",
        status: "completed",
        safe_metadata: { prompt: "secret-agent" },
      }],
    }),
  }));
  await page.route(`**/api/interviews/${sessionId}/runtime-events?limit=100`, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      items: [{
        event_id: "event-safe-1",
        event_type: "round.closed",
        status: "completed",
        payload_json: { answer: "secret-event" },
      }],
    }),
  }));
  await page.goto(`/report-detail?session_id=${sessionId}`);
  await expect(page.locator(".report-detail-score-mark")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("secret-agent");
  await expect(page.locator("body")).not.toContainText("secret-event");
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
    ["normal", "依据完整", reportReliability()],
    ["limited", "部分依据受限", reportReliability({
      score_applicability: "limited",
      generation_path: "mixed",
      degraded_question_count: 1,
      degraded_reasons: ["KNOWLEDGE_RETRIEVAL_DEGRADED"],
    })],
    ["insufficient", "依据不足", reportReliability({
      score_applicability: "insufficient",
      reviewed_answer_count: 0,
      evidence_bound_question_count: 0,
      degraded_question_count: 1,
      generation_path: "fallback",
      degraded_reasons: ["REPORT_FALLBACK"],
    })],
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

  await page.route(`**/api/interviews/${sessionId}/report`, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(report),
  }));
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

test("runtime diagnostics follow the explicit build capability", async ({ page, request }) => {
  const completed = await createCompletedReport(request);
  const processing = await seedReport(request, "processing");
  let diagnosticRequests = 0;
  for (const suffix of ["question-evaluations", "agent-runs?limit=100", "runtime-events?limit=100"]) {
    await page.route(`**/api/interviews/${completed}/${suffix}`, async (route) => {
      diagnosticRequests += 1;
      await route.continue();
    });
  }

  await page.goto(`/report-detail?session_id=${completed}`);
  await expect(page.locator(".report-detail-score-mark")).toBeVisible();
  if (diagnosticsEnabled) {
    await expect(page.locator("#runtime-trace")).toBeVisible();
    await expect(page.locator(".report-detail-evaluation-ledger")).toBeVisible();
    expect(diagnosticRequests).toBeGreaterThanOrEqual(3);
  } else {
    await expect(page.locator("#runtime-trace, .report-detail-evaluation-ledger")).toHaveCount(0);
    expect(diagnosticRequests).toBe(0);
  }

  await page.goto(`/report-processing?session_id=${processing.session_id}`);
  await expect(page.locator(".processing-progress-panel")).toBeVisible();
  if (diagnosticsEnabled) {
    await expect(page.locator(".processing-diagnostics")).toBeVisible();
    await expect(page.locator(".processing-diagnostics")).toContainText("任务 ID");
  } else {
    await expect(page.locator(".processing-diagnostics")).toHaveCount(0);
    await expect(page.locator("body")).not.toContainText("任务 ID");
  }
});
