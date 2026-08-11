const { test, expect } = require("@playwright/test");
const {
  createCompletedReport,
  createSession,
  desktopOnly,
  expectGeometry,
  seedReport,
  viewports,
} = require("./browser-suite-support");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});

test("help route remains stable across viewports", async ({ page }) => {
  test.setTimeout(60_000);
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: 900 });
    await page.goto("/help");
    await expectGeometry(page);
    await expect(page.locator(".help-manual-section")).toHaveCount(5);
    await expect(page.locator(".help-inspector, .help-status-bar")).toHaveCount(0);
  }
});

test("help route is a truthful single-column recovery manual", async ({ page }) => {
  await page.goto("/help");

  await expect(page.locator(".help-app.start-app-root")).toBeVisible();
  await expect(page.locator(".help-app-topbar.start-app-topbar")).toBeVisible();
  await expect(page.locator(".help-workspace.start-editor-workspace")).toBeVisible();
  await expect(page.locator(".help-inspector.start-inspector")).toHaveCount(0);
  await expect(page.locator(".help-status-bar.start-status-bar")).toHaveCount(0);
  await expect(page.locator(".start-runtime")).toHaveCount(0);
  await expect(page.locator(".help-manual-toc a")).toHaveCount(5);
  await expect(page.getByRole("heading", { name: "准备资料" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "进行面试" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "恢复会话" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "报告失败" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "草稿与数据" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("帮助可用");
  await expect(page.locator("body")).not.toContainText("手册就绪");
  await expect(page.locator("#drafts-data")).toContainText("进程内临时保存");
  await expect(page.locator("#drafts-data")).toContainText("持久保存");

  const manual = await page.evaluate(() => {
    const shell = document.querySelector(".help-app-shell");
    const section = document.querySelector(".help-manual-section");
    const title = section.querySelector("h2");
    const copy = section.querySelector("p");
    const tocLinks = [...document.querySelectorAll(".help-manual-toc a")];
    return {
      shellColumns: getComputedStyle(shell).gridTemplateColumns.split(" ").filter(Boolean).length,
      sectionWidth: section.getBoundingClientRect().width,
      titleSize: Number.parseFloat(getComputedStyle(title).fontSize),
      copySize: Number.parseFloat(getComputedStyle(copy).fontSize),
      touchTargets: tocLinks.every((link) => link.getBoundingClientRect().height >= 40),
      horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    };
  });
  expect(manual.shellColumns).toBe(1);
  expect(manual.sectionWidth).toBeGreaterThan(0);
  expect(manual.titleSize).toBeGreaterThanOrEqual(18);
  expect(manual.copySize).toBeGreaterThanOrEqual(13);
  expect(manual.touchTargets).toBe(true);
  expect(manual.horizontalOverflow).toBe(false);

  const reportFailureLink = page.locator('.help-manual-toc a[href="#report-failure"]');
  await reportFailureLink.click();
  await expect(page.locator("#report-failure")).toBeInViewport();

  const reportCenter = page.getByRole("link", { name: "打开报告中心" });
  await reportCenter.focus();
  await expect(reportCenter).toBeFocused();
  const focusWidth = await reportCenter.evaluate((element) => Number.parseFloat(getComputedStyle(element).outlineWidth));
  expect(focusWidth).toBeGreaterThanOrEqual(2);
});

test("help manual honors reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/help");
  await expect(page.locator(".help-manual-toc a").first()).toBeVisible();
  const motion = await page.evaluate(() => {
    const root = document.querySelector(".help-app");
    const sample = document.querySelector(".help-manual-toc a");
    return {
      scrollBehavior: getComputedStyle(document.querySelector(".help-manual")).scrollBehavior,
      animationDuration: getComputedStyle(sample).animationDuration,
      transitionDuration: getComputedStyle(sample).transitionDuration,
      rootVisible: root.getBoundingClientRect().height > 0,
    };
  });
  expect(motion.scrollBehavior).toBe("auto");
  expect(Number.parseFloat(motion.animationDuration)).toBeLessThanOrEqual(0.001);
  expect(Number.parseFloat(motion.transitionDuration)).toBeLessThanOrEqual(0.001);
  expect(motion.rootVisible).toBe(true);
});

test("mobile navigation remains reachable from 360 through 900 pixels", async ({ page }) => {
  for (const width of [360, 390, 768, 900]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/help");
    await expect(page.locator(".help-workspace")).toBeVisible();
    const mobileNav = page.locator(".mobile-nav");
    await expect(mobileNav).toBeVisible();
    await expect(mobileNav.locator('a[href="/help"]')).toHaveAttribute("aria-current", "page");
    const geometry = await page.evaluate(() => {
      const nav = document.querySelector(".mobile-nav").getBoundingClientRect();
      return {
        navBottom: nav.bottom,
        viewportHeight: window.innerHeight,
        documentWidth: document.documentElement.scrollWidth,
        viewportWidth: document.documentElement.clientWidth,
      };
    });
    expect(geometry.navBottom).toBeLessThanOrEqual(geometry.viewportHeight + 1);
    expect(geometry.documentWidth).toBeLessThanOrEqual(geometry.viewportWidth);
  }
});

test("streaming conversation keeps token output outside live regions", async ({ page, request }) => {
  const sessionId = await createSession(request);
  await page.goto(`/interview?session_id=${sessionId}`);
  await expect(page.locator(".interview-workspace")).toBeVisible();
  await expect(page.locator(".agent-console")).not.toHaveAttribute("aria-live");
  await expect(page.locator(".current-question")).not.toHaveAttribute("aria-live");
  await expect(page.locator(".composer-draft-state")).not.toHaveAttribute("aria-live");
  await expect(page.locator('.visually-hidden[role="status"][aria-live="polite"]')).toHaveCount(1);
});

test("report routes reflow at portrait, landscape and 200 percent equivalent widths", async ({ page, request }) => {
  test.setTimeout(90_000);
  const processing = await seedReport(request, "processing");
  const completed = await createCompletedReport(request);
  const routes = [
    `/report-processing?session_id=${processing.session_id}`,
    `/report-detail?session_id=${completed}`,
    "/reports",
    "/help",
  ];
  const accessibilityViewports = [
    { width: 390, height: 844 },
    { width: 844, height: 390 },
    { width: 640, height: 900 },
  ];

  for (const viewport of accessibilityViewports) {
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
