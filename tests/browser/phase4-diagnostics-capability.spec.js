const { test, expect } = require("@playwright/test");
const {
  createCompletedReport,
  desktopOnly,
  seedReport,
} = require("./reference-ui-geometry");

const diagnosticsEnabled = process.env.VITE_SHOW_RUNTIME_DIAGNOSTICS === "true";

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
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
    await expect(page.getByRole("region", { name: "运行轨迹" })).toBeVisible();
    await expect(page.getByRole("region", { name: "逐题评审链路" })).toBeVisible();
    expect(diagnosticRequests).toBeGreaterThanOrEqual(3);
  } else {
    await expect(page.getByRole("region", { name: /^(运行轨迹|逐题评审链路)$/ })).toHaveCount(0);
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
    expect(diagnosticRequests).toBe(0);
  }
});
