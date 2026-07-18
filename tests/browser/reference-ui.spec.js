const { test, expect } = require("@playwright/test");

const jobDescription = "Backend engineer with Redis and MySQL";
const resumeText = "Built cache-aside recovery workflows";

test.beforeEach(async ({}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium", "desktop-only UI refactor");
});

async function createSession(request) {
  const response = await request.post("/api/interviews", {
    data: {
      job_description: jobDescription,
      resume_text: resumeText,
    },
  });
  expect(response.status()).toBe(200);
  return (await response.json()).session_id;
}

async function createCompletedReport(request) {
  const sessionId = await createSession(request);
  const snapshot = await request.get(`/api/interviews/${sessionId}`);
  const { state_version: stateVersion } = await snapshot.json();
  const finish = await request.post(`/api/interviews/${sessionId}/finish`, {
    data: {
      expected_version: stateVersion,
      command_id: `reference-finish-${sessionId}`,
    },
  });
  expect(finish.status()).toBe(200);
  await expect.poll(async () => {
    const report = await request.get(`/api/interviews/${sessionId}/report`);
    return report.status();
  }).toBe(200);
  return sessionId;
}

async function startInterviewThroughPrep(page) {
  await page.goto("/prep");
  await page.locator("#jobDescription").fill(jobDescription);
  await page.locator("#resumeText").fill(resumeText);
  await page.locator("#prepButton").click();
  await expect(page.locator("#planQuestions li")).toHaveCount(3);
  await page.locator("#startButton").click();
  await expect(page).toHaveURL(/\/interview\?session_id=/);
  await expect(page.locator("#sessionStatus")).toHaveText("active");
  return new URL(page.url()).searchParams.get("session_id");
}

async function seedReport(request, status, ageDays = 0) {
  const response = await request.post(
    `/test-support/reports/${status}?age_days=${ageDays}`,
  );
  expect(response.status()).toBe(200);
  return response.json();
}

async function expectDesktopGeometry(page) {
  await page.evaluate(() => window.scrollTo(0, 0));
  const metrics = await page.evaluate(() => {
    const isVisible = (element) => {
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none"
        && style.visibility !== "hidden"
        && rect.width > 0
        && rect.height > 0;
    };
    const rectOf = (element) => {
      if (!element) return null;
      const rect = element.getBoundingClientRect();
      return {
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        left: rect.left,
        width: rect.width,
        height: rect.height,
      };
    };
    const buttons = [...document.querySelectorAll("button")]
      .filter(isVisible)
      .map((button) => ({
        ...rectOf(button),
        text: button.textContent.trim(),
      }));
    const buttonOverlaps = [];
    for (let left = 0; left < buttons.length; left += 1) {
      for (let right = left + 1; right < buttons.length; right += 1) {
        const first = buttons[left];
        const second = buttons[right];
        const overlapWidth = Math.min(first.right, second.right) - Math.max(first.left, second.left);
        const overlapHeight = Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top);
        if (overlapWidth > 1 && overlapHeight > 1) {
          buttonOverlaps.push([first.text, second.text]);
        }
      }
    }
    const tableContainer = document.querySelector(".report-table-scroll, .feedback-table-wrap");
    return {
      viewport: document.documentElement.clientWidth,
      document: document.documentElement.scrollWidth,
      bodyTextLength: document.body.innerText.trim().length,
      topbar: rectOf(document.querySelector(".app-topbar")),
      main: rectOf(document.querySelector("main")),
      buttons,
      buttonOverlaps,
      table: tableContainer ? {
        ...rectOf(tableContainer),
        clientWidth: tableContainer.clientWidth,
        scrollWidth: tableContainer.scrollWidth,
      } : null,
    };
  });

  expect(metrics.bodyTextLength).toBeGreaterThan(0);
  expect(metrics.document).toBeLessThanOrEqual(metrics.viewport);
  expect(metrics.buttons.length).toBeGreaterThan(0);
  expect(metrics.buttons.every((button) => button.width > 0 && button.height > 0)).toBe(true);
  expect(metrics.buttonOverlaps).toEqual([]);
  if (metrics.topbar && metrics.main) {
    expect(metrics.topbar.bottom).toBeLessThanOrEqual(metrics.main.top + 1);
    expect(metrics.main.left).toBeGreaterThanOrEqual(0);
    expect(metrics.main.right).toBeLessThanOrEqual(metrics.viewport + 1);
  }
  if (metrics.table) {
    expect(metrics.table.width).toBeGreaterThan(0);
    expect(metrics.table.height).toBeGreaterThan(0);
    expect(metrics.table.clientWidth).toBeGreaterThan(0);
    expect(metrics.table.scrollWidth).toBeGreaterThanOrEqual(metrics.table.clientWidth);
    expect(metrics.table.right).toBeLessThanOrEqual(metrics.viewport + 1);
  }
}

test("reference preparation validates imports and renders real plan metrics", async ({ page }) => {
  await page.goto("/prep");

  await page.locator("#jobDescriptionFileInput").setInputFiles({
    name: "role.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("not a supported text file"),
  });
  await expect(page.locator("#prepStatus")).toContainText(".txt");

  await page.locator("#jobDescriptionFileInput").setInputFiles({
    name: "oversized.md",
    mimeType: "text/markdown",
    buffer: Buffer.alloc((1024 * 1024) + 1, "a"),
  });
  await expect(page.locator("#prepStatus")).toContainText("1 MiB");

  await page.locator("#jobDescriptionFileInput").setInputFiles({
    name: "role.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(jobDescription),
  });
  await page.locator("#resumeFileInput").setInputFiles({
    name: "resume.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(resumeText),
  });
  await expect(page.locator("#jobDescription")).toHaveValue(jobDescription);
  await expect(page.locator("#resumeText")).toHaveValue(resumeText);

  await page.locator("#prepButton").click();
  await expect(page.locator("#planQuestions li")).toHaveCount(3);
  await expect(page.locator("#planQuestionCount")).toContainText("3");
  await expect(page.locator("#planDuration")).toContainText("12-18");
  await expect(page.locator("#prepKnowledgeStatus")).toBeVisible();
});

test("interview focus mode and question draft survive refresh and submission failure", async ({ page }) => {
  const sessionId = await startInterviewThroughPrep(page);
  const snapshot = await page.request.get(`/api/interviews/${sessionId}`);
  const questionId = (await snapshot.json()).current_question.id;
  const draftKey = `interviewAnswerDraft:${sessionId}:${questionId}`;
  const draft = "Cache-aside with database fallback and timeout recovery.";

  await page.locator("#answerInput").fill(draft);
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), draftKey)).toBe(draft);
  await page.reload();
  await expect(page.locator("#answerInput")).toHaveValue(draft);

  await page.locator("#focusModeButton").click();
  await expect(page.locator("#focusModeButton")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".question-nav")).toBeHidden();
  await expect(page.locator(".interview-side")).toBeHidden();
  await page.keyboard.press("Escape");
  await expect(page.locator("#focusModeButton")).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator(".question-nav")).toBeVisible();
  await expect(page.locator(".interview-side")).toBeVisible();

  const streamPattern = `**/api/interviews/${sessionId}/answer/stream`;
  const failAnswer = (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ detail: "simulated provider outage" }),
  });
  await page.route(streamPattern, failAnswer);
  await page.locator("#sendAnswerButton").click();
  await expect(page.locator("#interviewNotice")).toContainText("simulated provider outage");
  await expect(page.locator("#answerInput")).toHaveValue(draft);
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), draftKey)).toBe(draft);

  await page.unroute(streamPattern, failAnswer);
  await page.locator("#sendAnswerButton").click();
  await expect(page.locator("#conversation")).toContainText("trade-off");
  await expect.poll(() => page.evaluate((key) => localStorage.getItem(key), draftKey)).toBeNull();
});

test("report center filters, paginates, requeues, downloads and opens progress", async ({ page, request }) => {
  const processingReports = [];
  for (let index = 0; index < 7; index += 1) {
    processingReports.push(await seedReport(request, "processing", index === 6 ? 45 : 0));
  }
  const failedReport = await seedReport(request, "failed");
  const completedSessionId = await createCompletedReport(request);

  await page.goto("/reports");
  await expect(page.locator("#reportsStatus")).toContainText("已加载");

  await page.locator('[data-report-status="processing"]').click();
  await expect(page.locator("#reportsTableBody tr")).toHaveCount(5);
  await page.locator("#paginationNext").click();
  await expect(page.locator("#reportsTableBody tr")).toHaveCount(1);

  await page.locator("#reportDateFilter").selectOption("all");
  await expect(page.locator("#reportsTableBody tr")).toHaveCount(5);
  await page.locator("#paginationNext").click();
  await expect(page.locator("#reportsTableBody tr")).toHaveCount(2);

  await page.locator('[data-report-status="failed"]').click();
  await page.locator("#reportSearch").fill("Backend");
  await expect(page.locator("#reportsTableBody tr")).toHaveCount(1);
  await page.getByRole("button", { name: "重新生成" }).click();
  await expect(page.locator("#reportsStatus")).toContainText("重新进入队列");
  await expect(page.locator("#reportsTableBody tr")).toHaveCount(0);
  const reportsAfterRequeue = await request.get("/api/reports?limit=100");
  const requeued = (await reportsAfterRequeue.json()).items.find(
    (item) => item.session_id === failedReport.session_id,
  );
  expect(requeued.status).toBe("processing");

  await page.locator('[data-report-status="completed"]').click();
  await page.locator("#reportSearch").fill(completedSessionId);
  await expect(page.locator("#reportsTableBody tr")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "下载 PDF" })).toBeVisible();
  await expect(page.getByRole("button", { name: "下载 PDF" })).toBeEnabled();

  await page.locator('[data-report-status="processing"]').click();
  await page.locator("#reportSearch").fill(processingReports[0].session_id);
  await page.getByRole("link", { name: "查看进度" }).click();
  await expect(page).toHaveURL(new RegExp(`/report-processing\\?session_id=${processingReports[0].session_id}`));
  await expect(page.locator("#reportProgressText")).toHaveText("20%");
});

test("report detail renders only safe runtime trace fields and no percentile claim", async ({ page, request }) => {
  const sessionId = await createCompletedReport(request);
  const reportResponse = await request.get(`/api/interviews/${sessionId}/report`);
  const report = await reportResponse.json();
  await page.route(`**/api/interviews/${sessionId}/agent-runs?limit=100`, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      session_id: sessionId,
      items: [{
        run_id: "run-safe-1",
        agent: "reviewer",
        operation: "evaluate",
        status: "completed",
        correlation_id: "correlation-safe-1",
        latency_ms: 14,
        started_at: "2026-07-18T00:00:00Z",
        finished_at: "2026-07-18T00:00:00Z",
        safe_metadata: { prompt: "do-not-render-agent-secret" },
      }],
    }),
  }));
  await page.route(`**/api/interviews/${sessionId}/runtime-events?limit=100`, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      session_id: sessionId,
      items: [{
        event_id: "event-safe-1",
        event_type: "round.closed",
        status: "completed",
        correlation_id: "correlation-safe-1",
        attempt_count: 1,
        max_attempts: 3,
        replay_count: 0,
        created_at: "2026-07-18T00:00:00Z",
        updated_at: "2026-07-18T00:00:00Z",
        payload_json: { answer: "do-not-render-event-secret" },
      }],
    }),
  }));

  await page.goto(`/report-detail?session_id=${sessionId}`);
  await expect(page.locator("#reportScore")).toHaveText(String(report.overall_score));
  await expect(page.locator("#agentRunList")).toContainText("run-safe-1");
  await expect(page.locator("#runtimeEventList")).toContainText("event-safe-1");
  await expect(page.locator("body")).not.toContainText("超过候选人");
  await expect(page.locator("body")).not.toContainText("do-not-render-agent-secret");
  await expect(page.locator("body")).not.toContainText("do-not-render-event-secret");
  await expect(page.locator("body")).not.toContainText("safe_metadata");
  await expect(page.locator("body")).not.toContainText("payload_json");
});

test("five reference pages stay nonempty and bounded at desktop viewports", async ({ page, request }, testInfo) => {
  const activeSessionId = await createSession(request);
  const processing = await seedReport(request, "processing");
  const completedSessionId = await createCompletedReport(request);
  const completedReportResponse = await request.get(
    `/api/interviews/${completedSessionId}/report`,
  );
  const completedReport = await completedReportResponse.json();
  const pages = [
    { name: "prep", url: "/prep", ready: "#prepButton" },
    {
      name: "interview",
      url: `/interview?session_id=${activeSessionId}`,
      ready: "#sessionStatus",
    },
    {
      name: "processing",
      url: `/report-processing?session_id=${processing.session_id}`,
      ready: "#reportProgressText",
    },
    {
      name: "detail",
      url: `/report-detail?session_id=${completedSessionId}`,
      ready: "#reportScore",
    },
    { name: "reports", url: "/reports", ready: "#reportsTableBody" },
  ];

  for (const target of pages) {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(target.url);
    await expect(page.locator(target.ready)).toBeVisible();
    if (target.name === "interview") {
      await expect(page.locator("#sessionStatus")).toHaveText("active");
    } else if (target.name === "processing") {
      await expect(page.locator("#reportProgressText")).toHaveText("20%");
    } else if (target.name === "detail") {
      await expect(page.locator("#reportScore")).toHaveText(
        String(completedReport.overall_score),
      );
    } else if (target.name === "reports") {
      await expect(page.locator("#reportsStatus")).toContainText("已加载");
    }
    await expectDesktopGeometry(page);
    await page.screenshot({
      path: testInfo.outputPath(`${target.name}-1440x1000.png`),
      fullPage: true,
    });

    await page.setViewportSize({ width: 1280, height: 800 });
    await expectDesktopGeometry(page);
  }
});
