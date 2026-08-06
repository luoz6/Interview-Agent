const { test, expect } = require("@playwright/test");
const { desktopOnly } = require("./reference-ui-geometry");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});

const viewports = [
  { width: 320, height: 900 },
  { width: 375, height: 900 },
  { width: 414, height: 900 },
  { width: 768, height: 900 },
  { width: 844, height: 900 },
  { width: 900, height: 900 },
  { width: 901, height: 900 },
  { width: 1024, height: 900 },
  { width: 1280, height: 900 },
  { width: 1440, height: 900 },
  { width: 2048, height: 1152 },
];

test("preparation workbench follows the locked navigation-aware viewport matrix", async ({ page }) => {
  test.setTimeout(90_000);

  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    await page.goto("/prep");

    await expect(page.locator(".start-prep-app-shell")).toBeVisible();
    await expect(page.locator(".start-activity-rail")).toBeVisible();
    await expect(page.locator(".start-editor-workspace")).toBeVisible();
    await expect(page.locator(".start-inspector")).toBeVisible();
    await expect(page.locator(".start-prep-status-bar")).toBeVisible();

    const prep = await page.evaluate(() => {
      const topbar = document.querySelector(".start-app-topbar").getBoundingClientRect();
      const shell = document.querySelector(".start-prep-app-shell");
      const rail = document.querySelector(".start-activity-rail");
      const workspace = document.querySelector(".start-editor-workspace");
      const inspector = document.querySelector(".start-inspector");
      const statusBar = document.querySelector(".start-prep-status-bar");
      const primary = document.querySelector(".start-prep-primary-action");
      const mobileNav = document.querySelector(".mobile-nav");
      const visibleDocuments = [...document.querySelectorAll(".start-document-canvas [data-document]")]
        .filter((element) => {
          const style = getComputedStyle(element);
          const rect = element.getBoundingClientRect();
          return style.display !== "none" && rect.width > 0 && rect.height > 0;
        }).length;
      const controls = [...document.querySelectorAll(".start-prep-app-shell button, .start-prep-app-shell .start-file-button")]
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        });

      return {
        topbarHeight: Math.round(topbar.height),
        viewportWidth: document.documentElement.clientWidth,
        documentWidth: document.documentElement.scrollWidth,
        visibleDocuments,
        smallControls: controls.filter((element) => element.getBoundingClientRect().height < 43.5).length,
        activityRailCount: document.querySelectorAll(".start-activity-rail").length,
        inspectorCount: document.querySelectorAll(".start-inspector").length,
        statusBarCount: document.querySelectorAll(".start-prep-status-bar").length,
        splitEntryCount: document.querySelectorAll(".start-split-tab").length,
        primaryCount: document.querySelectorAll(".start-prep-primary-action").length,
        primaryRect: primary?.getBoundingClientRect().toJSON(),
        railRect: rail.getBoundingClientRect().toJSON(),
        workspaceRect: workspace.getBoundingClientRect().toJSON(),
        inspectorRect: inspector.getBoundingClientRect().toJSON(),
        statusRect: statusBar.getBoundingClientRect().toJSON(),
        mobileNavRect: mobileNav.getBoundingClientRect().toJSON(),
        mobileNavVisible: getComputedStyle(mobileNav).display !== "none",
        shellOverflow: getComputedStyle(shell).overflow,
      };
    });

    expect(prep.topbarHeight).toBe(64);
    expect(prep.documentWidth).toBeLessThanOrEqual(prep.viewportWidth);
    expect(prep.visibleDocuments).toBe(1);
    expect(prep.activityRailCount).toBe(1);
    expect(prep.inspectorCount).toBe(1);
    expect(prep.statusBarCount).toBe(1);
    expect(prep.primaryCount).toBe(1);
    expect(prep.splitEntryCount).toBe(viewport.width >= 1180 ? 1 : 0);
    expect(prep.primaryRect.top).toBeGreaterThanOrEqual(prep.topbarHeight);
    expect(prep.primaryRect.bottom).toBeLessThanOrEqual(viewport.height + 1);

    if (viewport.width <= 900) {
      expect(prep.mobileNavVisible).toBe(true);
      expect(prep.statusRect.bottom).toBeLessThanOrEqual(prep.mobileNavRect.top + 1);
      expect(prep.primaryRect.bottom).toBeLessThanOrEqual(prep.mobileNavRect.top + 1);
    } else {
      expect(prep.mobileNavVisible).toBe(false);
    }

    if (viewport.width >= 1180) {
      expect(prep.railRect.right).toBeLessThanOrEqual(prep.workspaceRect.left + 1);
      expect(prep.workspaceRect.right).toBeLessThanOrEqual(prep.inspectorRect.left + 1);
    } else {
      expect(prep.inspectorRect.top).toBeGreaterThanOrEqual(prep.workspaceRect.bottom - 1);
    }

    if (viewport.width <= 414) expect(prep.smallControls).toBe(0);
  }
});

test("activity rail, inspector tabs and the fixed status bar expose one shared state model", async ({ page }) => {
  await page.goto("/prep");

  const sources = page.getByRole("button", { name: "资料" });
  const plan = page.getByRole("button", { name: "蓝图" });
  const evidence = page.getByRole("button", { name: "证据" });
  await expect(sources).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("tab", { name: "准备状态" })).toHaveAttribute("aria-selected", "true");

  await plan.click();
  await expect(plan).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("tab", { name: "计划" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "尚未生成面试计划" })).toBeVisible();

  await page.getByRole("tab", { name: "准备状态" }).click();
  await expect(plan).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("tab", { name: "准备状态" })).toHaveAttribute("aria-selected", "true");

  await page.getByRole("tab", { name: "准备状态" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("tab", { name: "计划" })).toBeFocused();
  await expect(page.getByRole("tab", { name: "计划" })).toHaveAttribute("aria-selected", "true");

  await evidence.click();
  await expect(evidence).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("tab", { name: "证据" })).toHaveAttribute("aria-selected", "true");

  const fields = await page.locator(".start-prep-status-bar > span").allTextContents();
  expect(fields).toHaveLength(5);
  expect(fields[0]).toContain("当前请求");
  expect(fields[1]).toContain("岗位 JD");
  expect(fields[2]).toContain("候选人经历");
  expect(fields[3]).toContain("草稿");
  expect(fields[4]).toContain("Knowledge");
});
