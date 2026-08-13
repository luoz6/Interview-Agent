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
      const required = (selector) => {
        const element = document.querySelector(selector);
        if (!element) throw new Error(`Missing required report element: ${selector}`);
        return element;
      };
      const shell = required(".report-detail-shell");
      const workspace = required(".report-detail-workspace");
      const inspector = required(".report-detail-inspector");
      const overview = required(".report-detail-overview");
      const score = required(".report-detail-score-mark");
      const title = required("#report-detail-title");
      const coveragePanel = required("#coverage");
      const technicalAppendix = required(".report-detail-technical-appendix");
      const scoreTrack = document.querySelector(".report-detail-score-track > span");
      const dimensionTrack = document.querySelector(".report-detail-dimension-track > span");
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
        scoreLabel: score.getAttribute("aria-label"),
        scoreTrackCount: document.querySelectorAll(".report-detail-score-track > span").length,
        scoreTrackAnimation: scoreTrack ? getComputedStyle(scoreTrack).animationName : "none",
        scoreOrbitCount: document.querySelectorAll(
          ".report-detail-score-orbit, .report-detail-head-score",
        ).length,
        dimensionRows: document.querySelectorAll(
          ".report-detail-dimensions > li",
        ).length,
        primaryRevealCount: document.querySelectorAll(
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
        dimensionTrackCount: document.querySelectorAll(".report-detail-dimension-track > span").length,
        dimensionProgressCount: document.querySelectorAll(
          '.report-detail-dimension-track[role="progressbar"]',
        ).length,
        dimensionAnimation: dimensionTrack ? getComputedStyle(dimensionTrack).animationName : "none",
        coveragePanelBackground: getComputedStyle(coveragePanel).backgroundColor,
        workspaceColor: getComputedStyle(workspace).color,
        candidateSectionCount: document.querySelectorAll(
          "#overview, #coverage, #strengths, #actions, #questions, #limitations",
        ).length,
        technicalAppendixCount: document.querySelectorAll(
          ".report-detail-technical-appendix",
        ).length,
        technicalAppendixOpen: technicalAppendix.open,
        revisionHistoryCount: document.querySelectorAll(
          "#report-revision-history-title",
        ).length,
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
          required(".report-detail-workspace-scroll"),
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
    expect(detail.scoreLabel).toContain("综合评分未发布");
    expect(detail.scoreTrackCount).toBe(0);
    expect(detail.scoreTrackAnimation).toBe("none");
    expect(detail.scoreOrbitCount).toBe(0);
    expect(detail.dimensionRows).toBe(5);
    expect(detail.primaryRevealCount).toBe(6);
    expect(detail.horizontalOverflow).toBe(false);
    expect(detail.wrappedActionLabels).toBe(0);
    expect(detail.railIconCount).toBe(6);
    expect(detail.sectionIconCount).toBe(5);
    expect(detail.scoreAnimations).toContain("report-detail-score-enter");
    expect(detail.dimensionTrackCount).toBe(5);
    expect(detail.dimensionProgressCount).toBe(0);
    expect(detail.dimensionAnimation).toBe("report-detail-dimension-fill");
    expect(detail.coveragePanelBackground).not.toBe("");
    expect(detail.workspaceColor).not.toBe("");
    expect(detail.candidateSectionCount).toBe(6);
    expect(detail.technicalAppendixCount).toBe(1);
    expect(detail.technicalAppendixOpen).toBe(false);
    expect(detail.revisionHistoryCount).toBe(1);
    expect(detail.runtimeTraceCount).toBe(0);
    expect(detail.evaluationLedgerCount).toBe(0);
    expect(detail.inspectorScoreCount).toBe(1);
    expect(detail.statusBarCount).toBe(1);
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
  await expect(
    page.locator(".report-detail-inspector-actions")
      .getByRole("button", { name: "下载已开始" }),
  ).toBeVisible();
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
  const errorState = page.getByRole("alert");
  await expect(errorState.getByRole("heading", { name: "报告暂时无法读取" })).toBeVisible();
  await expect(errorState).toContainText("服务正在恢复中，请稍后重试。");
  await expect(errorState).not.toContainText("报告存储暂时不可用");
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
  await expect(download).toBeEnabled();
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

test("score and coverage states remain explicit without fabricating missing scores", async ({ page, request }) => {
  test.setTimeout(60_000);
  const sessionId = await createCompletedReport(request);
  const response = await request.get(`/api/interviews/${sessionId}/report`);
  expect(response.ok()).toBe(true);
  const baseline = await response.json();
  const activeArtifact = baseline.active_artifact || baseline;
  const wrappedArtifact = Boolean(baseline.active_artifact);

  let payload = baseline;
  await page.route(`**/api/interviews/${sessionId}/report`, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(payload),
  }));

  const cases = [
    {
      score_status: "scored",
      coverage_status: "complete",
      overall_score: 82,
      evaluated_count: 1,
      total_eligible_count: 1,
      scoreLabel: "已评分",
      coverageLabel: "完整覆盖",
      note: "五个维度仍只代表本轮已回答题目的证据",
    },
    {
      score_status: "partial",
      coverage_status: "partial",
      overall_score: 67,
      evaluated_count: 1,
      total_eligible_count: 2,
      scoreLabel: "部分评分",
      coverageLabel: "部分覆盖",
      note: "未评估题目和维度不会按 0 分处理",
    },
    {
      score_status: "unscored",
      coverage_status: "none",
      overall_score: null,
      overall_dimension_scores: {},
      evaluated_count: 0,
      total_eligible_count: 1,
      scoreLabel: "未评分",
      coverageLabel: "无有效覆盖",
      note: "不显示任何假分",
    },
  ];

  for (const state of cases) {
    const overrides = { ...state };
    delete overrides.scoreLabel;
    delete overrides.coverageLabel;
    delete overrides.note;
    payload = wrappedArtifact
      ? {
          ...baseline,
          active_artifact: {
            ...activeArtifact,
            ...overrides,
            payload: {
              ...(activeArtifact.payload || {}),
              ...overrides,
            },
          },
        }
      : { ...baseline, ...overrides };
    await page.goto(`/report-detail?session_id=${sessionId}`);
    const statePair = page.getByLabel("评分和覆盖状态");
    await expect(statePair).toBeVisible({ timeout: 15_000 });
    await expect(statePair).toContainText(state.scoreLabel);
    await expect(statePair).toContainText(state.coverageLabel);
    await expect(page.locator(".report-detail-coverage-note")).toContainText(state.note);
  }

  await expect(page.locator(".report-detail-score-mark")).toContainText("未评分");
  await expect(page.locator(".report-detail-dimension")).toHaveCount(5);
  await expect(page.locator(".report-detail-dimension").first()).toContainText(/未评估|证据不足/);
});

test("report detail excludes the legacy targeted-practice facade", async ({ page, request }) => {
  const sessionId = await createCompletedReport(request);
  let legacyRequests = 0;
  await page.route("**/api/interviews/*/practice-plan", (route) => {
    legacyRequests += 1;
    return route.abort();
  });
  await page.route("**/api/prep-plans/**", (route) => {
    legacyRequests += 1;
    return route.abort();
  });

  await page.goto(`/report-detail?session_id=${sessionId}`);
  await expect(page.locator(".report-detail-score-mark")).toBeVisible();
  await expect(page.getByRole("button", { name: "创建针对性练习" })).toHaveCount(0);
  await expect(page.locator('a[href*="/prep?plan_id="]')).toHaveCount(0);
  expect(legacyRequests).toBe(0);
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
