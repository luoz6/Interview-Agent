const { test, expect } = require("@playwright/test");
const {
  desktopOnly,
  expectGeometry,
  seedReport,
  viewports,
} = require("./browser-suite-support");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});

async function readProgressBaseline(request, sessionId) {
  const response = await request.get(`/api/interviews/${sessionId}/report/progress`);
  expect(response.ok()).toBe(true);
  return response.json();
}

async function controlProgress(page, sessionId, initialSnapshot) {
  let snapshot = initialSnapshot;
  let requestCount = 0;
  await page.route(`**/api/interviews/${sessionId}/report/progress`, async (route) => {
    requestCount += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(snapshot),
    });
  });
  return {
    requests: () => requestCount,
    update: (patch) => {
      snapshot = { ...snapshot, ...patch };
    },
  };
}

async function refreshControlledProgress(page, controller) {
  const previousRequestCount = controller.requests();
  await page.getByRole("button", { name: "立即刷新报告进度" }).click();
  await expect.poll(controller.requests).toBeGreaterThan(previousRequestCount);
}

test("report processing layout remains stable across viewports", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  const report = await seedReport(request, "processing");
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: 900 });
    await page.goto("/report-processing?session_id=" + report.session_id);
    await expect(page.locator(".processing-app")).toBeVisible();
    await expectGeometry(page);
    await expect(page.locator(".processing-stage-list [data-state=current]")).toHaveCount(1);
    await expect(page.locator(".processing-inspector")).toBeVisible();
    const pipeline = await page.locator(".pipeline-hero").evaluate((element) => {
      const surface = document.querySelector(".start-app-topbar");
      const progressFill = document.querySelector(".processing-progress-track > span");
      const brandMark = document.querySelector(".start-brand-mark");
      return {
        background: getComputedStyle(element).backgroundColor,
        surfaceBackground: getComputedStyle(surface).backgroundColor,
        progressColor: getComputedStyle(progressFill).backgroundColor,
        actionColor: getComputedStyle(brandMark).backgroundColor,
        stageIconCount: document.querySelectorAll(
          ".processing-stage-icon svg",
        ).length,
        spinnerCount: document.querySelectorAll(
          ".processing-app .start-spinner",
        ).length,
        stageCopySize: Number.parseFloat(getComputedStyle(
          document.querySelector(".processing-stage-list li p"),
        ).fontSize),
        currentAnchorWidth: Number.parseFloat(getComputedStyle(
          document.querySelector(".processing-stage-list [data-state=current]"),
          "::before",
        ).width),
        inspectorRedundantLabelCount: document.querySelectorAll(
          ".processing-inspector-section > header > span",
        ).length,
        disabledActionHasLock: Boolean(document.querySelector(
          ".processing-view-disabled .processing-action-lock",
        )),
        actionGuidanceVisible: document.querySelector(
          ".processing-action-guidance",
        ).getBoundingClientRect().height > 0,
        actionButtonsMeetTouchSize: [...document.querySelectorAll(
          ".processing-inspector-actions button",
        )].every((button) => button.getBoundingClientRect().height >= 44),
        primaryCount: document.querySelectorAll(
          ".button-primary:not(:disabled)",
        ).length,
      };
    });
    expect(pipeline.background).toBe(pipeline.surfaceBackground);
    expect(pipeline.progressColor).toBe(pipeline.actionColor);
    expect(pipeline.stageIconCount).toBe(7);
    expect(pipeline.spinnerCount).toBeLessThanOrEqual(1);
    expect(pipeline.stageCopySize).toBeGreaterThanOrEqual(14);
    expect(pipeline.currentAnchorWidth).toBeGreaterThanOrEqual(2);
    expect(pipeline.inspectorRedundantLabelCount).toBe(0);
    expect(pipeline.disabledActionHasLock).toBe(true);
    expect(pipeline.actionGuidanceVisible).toBe(true);
    expect(pipeline.actionButtonsMeetTouchSize).toBe(true);
    expect(pipeline.primaryCount).toBe(0);
  }
  const backButton = page.getByRole("button", { name: "返回报告中心" });
  await backButton.focus();
  const focusRing = await backButton.evaluate((button) => {
    const style = getComputedStyle(button);
    return {
      style: style.outlineStyle,
      width: Number.parseFloat(style.outlineWidth),
      offset: Number.parseFloat(style.outlineOffset),
    };
  });
  expect(focusRing.style).toBe("solid");
  expect(focusRing.width).toBeGreaterThanOrEqual(2);
  expect(focusRing.offset).toBeGreaterThanOrEqual(4);
  await request.delete("/test-support/reports/" + report.session_id);
});

test("failed report exposes a clear recovery path", async ({ page, request }) => {
  const report = await seedReport(request, "failed");
  await page.goto("/report-processing?session_id=" + report.session_id);

  await expect(page.locator(".processing-notice[role=alert]")).toContainText(
    "报告任务已停止",
  );
  await expect(page.locator(".processing-notice-copy > strong")).toHaveText(
    "报告任务已停止",
  );
  await expect(page.locator(".processing-action-guidance")).toContainText(
    "安全地重新入队",
  );
  await expect(page.getByRole("button", { name: "重新尝试" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "返回报告中心" })).toBeEnabled();
  await expect(page.locator(".processing-stage-list [data-state=failed]")).toHaveCount(1);
  await expect(page.locator(".button-primary:not(:disabled)")).toHaveCount(1);
  expect(await page.locator(".processing-notice[role=alert]").evaluate(
    (notice) => getComputedStyle(notice).boxShadow,
  )).toBe("none");

  await request.delete("/test-support/reports/" + report.session_id);
});

test("orphaned report exposes one controlled requeue action", async ({ page, request }) => {
  const report = await seedReport(request, "orphaned");
  await page.goto("/report-processing?session_id=" + report.session_id);

  await expect(page.locator(".processing-runtime")).toContainText("任务已中断");
  await expect(page.locator(".processing-notice")).toContainText("报告任务已中断");
  await expect(page.locator(".processing-action-guidance")).toContainText(
    "安全地重新入队",
  );
  const retry = page.getByRole("button", { name: "重新尝试" });
  await expect(retry).toBeEnabled();
  await retry.click();
  await expect(page.locator(".processing-runtime")).toContainText(/正在重试|报告已完成/);

  await request.delete("/test-support/reports/" + report.session_id);
});

test("unchanged retrieval snapshots keep polling until the completed report opens", async ({
  page,
  request,
}) => {
  test.setTimeout(20_000);
  const report = await seedReport(request, "processing");
  const progressResponse = await request.get(
    `/api/interviews/${report.session_id}/report/progress`,
  );
  expect(progressResponse.ok()).toBe(true);
  const baseline = await progressResponse.json();
  let progressRequests = 0;

  await page.route(
    `**/api/interviews/${report.session_id}/report/progress`,
    async (route) => {
      progressRequests += 1;
      const completed = progressRequests >= 4;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...baseline,
          status: completed ? "completed" : "processing",
          stage: completed ? "completed" : "retrieving",
          percent: completed ? 100 : 20,
          message: completed ? "报告已生成" : "正在检索相关知识",
          last_updated_at: completed ? "2026-08-03T08:52:41Z" : "2026-08-03T08:52:02Z",
        }),
      });
    },
  );

  await page.goto(`/report-processing?session_id=${report.session_id}`);
  await expect(page.locator(".processing-progress-panel").getByRole("heading", { name: "知识检索" })).toBeVisible();
  await expect(page).toHaveURL(
    new RegExp(`/report-detail\\?session_id=${report.session_id}`),
    { timeout: 10_000 },
  );
  expect(progressRequests).toBeGreaterThanOrEqual(4);
  await request.delete("/test-support/reports/" + report.session_id);
});

test("report processing motion respects reduced-motion preferences", async ({
  page,
  request,
}) => {
  const report = await seedReport(request, "processing");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/report-processing?session_id=" + report.session_id);

  const motion = await page.locator(".processing-app").evaluate((root) => {
    const toMilliseconds = (value) => value.split(",").map((part) => {
      const duration = Number.parseFloat(part);
      return part.trim().endsWith("ms") ? duration : duration * 1000;
    });
    const samples = [
      root.querySelector(".start-spinner"),
      root.querySelector(".processing-percent"),
      root.querySelector(".processing-stage-list [data-state=current]"),
      root.querySelector(".processing-progress-track > span"),
    ].filter(Boolean);
    const stageAnchorStyle = getComputedStyle(
      root.querySelector(".processing-stage-list [data-state=current]"),
      "::before",
    );
    return {
      durations: [...samples.flatMap((element) => {
        const style = getComputedStyle(element);
        return [
          ...toMilliseconds(style.animationDuration),
          ...toMilliseconds(style.transitionDuration),
        ];
      }), ...toMilliseconds(stageAnchorStyle.animationDuration)],
      spinnerIterations: getComputedStyle(
        root.querySelector(".start-spinner"),
      ).animationIterationCount,
    };
  });

  expect(Math.max(...motion.durations)).toBeLessThanOrEqual(0.02);
  expect(motion.spinnerIterations).toBe("1");
  await request.delete("/test-support/reports/" + report.session_id);
});

test("progress retargets without remounting its visible value", async ({
  page,
  request,
}) => {
  const report = await seedReport(request, "processing");
  const baseline = await readProgressBaseline(request, report.session_id);
  const controller = await controlProgress(page, report.session_id, {
    ...baseline,
    report_job_id: baseline.report_job_id || "motion-job-1",
    attempt: 1,
    status: "processing",
    stage: "retrieving",
    percent: 20,
    message: "正在检索相关知识",
  });

  await page.goto(`/report-processing?session_id=${report.session_id}`);
  const visiblePercent = page.locator(".processing-percent-value");
  const progressbar = page.getByRole("progressbar", { name: "报告生成进度" });
  await expect(visiblePercent).toHaveText("20");
  await visiblePercent.evaluate((node) => {
    node.dataset.identityProbe = "stable-percent-node";
    window.__observedReportPercents = [Number(node.textContent)];
    const observer = new MutationObserver(() => {
      window.__observedReportPercents.push(Number(node.textContent));
    });
    observer.observe(node, { childList: true, characterData: true, subtree: true });
    window.__reportPercentObserver = observer;
  });

  await refreshControlledProgress(page, controller);
  await expect(visiblePercent).toHaveAttribute("data-identity-probe", "stable-percent-node");

  controller.update({ percent: 35, message: "正在检索岗位知识" });
  await refreshControlledProgress(page, controller);
  await expect(progressbar).toHaveAttribute("aria-valuenow", "35");
  await page.waitForTimeout(40);

  controller.update({ percent: 55, message: "正在整理检索结果" });
  await refreshControlledProgress(page, controller);
  await expect(progressbar).toHaveAttribute("aria-valuenow", "55");
  await expect(visiblePercent).toHaveText("55");
  await expect(visiblePercent).toHaveAttribute("data-identity-probe", "stable-percent-node");

  const result = await page.evaluate(() => {
    window.__reportPercentObserver?.disconnect();
    const fill = document.querySelector(".processing-progress-track > span");
    const scale = new DOMMatrixReadOnly(getComputedStyle(fill).transform).a;
    return {
      observed: window.__observedReportPercents,
      scale,
    };
  });
  expect(result.observed.at(-1)).toBe(55);
  expect(Math.min(...result.observed)).toBeGreaterThanOrEqual(20);
  expect(result.observed.every((value, index, values) => index === 0 || value >= values[index - 1])).toBe(true);
  expect(result.scale).toBeCloseTo(0.55, 2);

  await request.delete("/test-support/reports/" + report.session_id);
});

test("stage display state coalesces rapid updates and critical failures win immediately", async ({
  page,
  request,
}) => {
  const report = await seedReport(request, "processing");
  const baseline = await readProgressBaseline(request, report.session_id);
  const controller = await controlProgress(page, report.session_id, {
    ...baseline,
    report_job_id: baseline.report_job_id || "motion-job-stage",
    attempt: 1,
    status: "processing",
    stage: "retrieving",
    percent: 20,
    message: "正在检索相关知识",
  });

  await page.goto(`/report-processing?session_id=${report.session_id}`);
  const stageCopy = page.locator(".processing-stage-copy");
  await expect(stageCopy.getByRole("heading", { name: "知识检索" })).toBeVisible();
  await stageCopy.evaluate((node) => { node.dataset.identityProbe = "stable-stage-copy"; });

  controller.update({ stage: "analyzing", percent: 35, message: "正在分析回答" });
  await refreshControlledProgress(page, controller);
  await page.waitForTimeout(40);
  controller.update({ stage: "evaluating", percent: 50, message: "正在形成逐题评审" });
  await refreshControlledProgress(page, controller);

  await expect(stageCopy.getByRole("heading", { name: "逐题评审" })).toBeVisible();
  await expect(stageCopy).toContainText("正在形成逐题评审");
  await expect(stageCopy).toHaveAttribute("data-identity-probe", "stable-stage-copy");
  await expect(stageCopy).toHaveAttribute("data-motion-phase", "idle");

  controller.update({
    status: "failed",
    stage: "evaluating",
    percent: 52,
    message: "评审服务暂时不可用",
    retryable: true,
    error: { code: "knowledge_store_unavailable", message: "知识库暂时不可用" },
  });
  await refreshControlledProgress(page, controller);

  await expect(page.locator(".processing-notice[role=alert]")).toBeVisible();
  await expect(page.getByRole("button", { name: "重新尝试" })).toBeEnabled();
  await expect(stageCopy).toContainText("评审服务暂时不可用");
  await expect(stageCopy).toHaveAttribute("data-motion-phase", "idle");
  const criticalVisual = await stageCopy.evaluate((node) => ({
    opacity: getComputedStyle(node).opacity,
    transform: getComputedStyle(node).transform,
  }));
  expect(criticalVisual.opacity).toBe("1");
  expect(criticalVisual.transform).toBe("matrix(1, 0, 0, 1, 0, 0)");

  await request.delete("/test-support/reports/" + report.session_id);
});

test("a new report attempt resets lower progress immediately", async ({
  page,
  request,
}) => {
  const report = await seedReport(request, "processing");
  const baseline = await readProgressBaseline(request, report.session_id);
  const controller = await controlProgress(page, report.session_id, {
    ...baseline,
    report_job_id: "motion-attempt-1",
    attempt: 1,
    status: "processing",
    stage: "evaluating",
    percent: 60,
    message: "正在形成逐题评审",
  });

  await page.goto(`/report-processing?session_id=${report.session_id}`);
  const visiblePercent = page.locator(".processing-percent-value");
  await expect(visiblePercent).toHaveText("60");
  await visiblePercent.evaluate((node) => {
    window.__attemptResetValues = [];
    const observer = new MutationObserver(() => {
      window.__attemptResetValues.push(Number(node.textContent));
    });
    observer.observe(node, { childList: true, characterData: true, subtree: true });
    window.__attemptResetObserver = observer;
  });

  controller.update({
    report_job_id: "motion-attempt-2",
    attempt: 2,
    stage: "queued",
    percent: 10,
    message: "新的报告任务正在排队",
  });
  await refreshControlledProgress(page, controller);
  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "10");
  await expect(visiblePercent).toHaveText("10");
  const resetValues = await page.evaluate(() => {
    window.__attemptResetObserver?.disconnect();
    return window.__attemptResetValues;
  });
  expect(resetValues).toContain(10);
  expect(resetValues.some((value) => value > 10 && value < 60)).toBe(false);

  await request.delete("/test-support/reports/" + report.session_id);
});

test("reduced motion changes commit active progress immediately", async ({
  page,
  request,
}) => {
  const report = await seedReport(request, "processing");
  const baseline = await readProgressBaseline(request, report.session_id);
  const controller = await controlProgress(page, report.session_id, {
    ...baseline,
    report_job_id: baseline.report_job_id || "motion-job-reduced",
    attempt: 1,
    status: "processing",
    stage: "retrieving",
    percent: 20,
    message: "正在检索相关知识",
  });

  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`/report-processing?session_id=${report.session_id}`);
  const visiblePercent = page.locator(".processing-percent-value");
  await expect(visiblePercent).toHaveText("20");

  await page.emulateMedia({ reducedMotion: "no-preference" });
  controller.update({ percent: 80, message: "正在汇总检索证据" });
  await refreshControlledProgress(page, controller);
  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "80");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(visiblePercent).toHaveText("80", { timeout: 100 });
  const scale = await page.locator(".processing-progress-track > span").evaluate(
    (node) => new DOMMatrixReadOnly(getComputedStyle(node).transform).a,
  );
  expect(scale).toBeCloseTo(0.8, 2);

  await request.delete("/test-support/reports/" + report.session_id);
});

test("active report motion is cleaned up after route unmount", async ({
  page,
  request,
}) => {
  const report = await seedReport(request, "processing");
  const baseline = await readProgressBaseline(request, report.session_id);
  const controller = await controlProgress(page, report.session_id, {
    ...baseline,
    report_job_id: baseline.report_job_id || "motion-job-unmount",
    attempt: 1,
    status: "processing",
    stage: "retrieving",
    percent: 20,
    message: "正在检索相关知识",
  });
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.goto(`/report-processing?session_id=${report.session_id}`);
  await expect(page.locator(".processing-app")).toBeVisible();
  controller.update({ stage: "analyzing", percent: 90, message: "正在分析回答" });
  await refreshControlledProgress(page, controller);
  await page.goto("/help");
  await expect(page.locator(".help-app")).toBeVisible();
  await page.waitForTimeout(400);
  await expect(page.locator(".help-app")).toBeVisible();
  expect(pageErrors).toEqual([]);

  await request.delete("/test-support/reports/" + report.session_id);
});

test("non-motion routes do not request GSAP modules", async ({ page }) => {
  for (const route of ["/prep", "/reports", "/help"]) {
    await page.goto(route);
    await expect(page.locator(".start-app-root")).toBeVisible();
    const resources = await page.evaluate(() => performance.getEntriesByType("resource").map((entry) => entry.name));
    expect(resources.filter((resource) => /(?:^|[/@])gsap(?:[/@.]|$)/i.test(resource))).toEqual([]);
  }
});

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
