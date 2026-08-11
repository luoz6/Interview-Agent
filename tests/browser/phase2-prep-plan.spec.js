const { test, expect } = require("@playwright/test");
const { desktopOnly } = require("./browser-suite-support");

test.beforeEach(async ({}, testInfo) => {
  test.skip(desktopOnly(testInfo), "desktop project owns explicit viewport matrix");
});

const jobDescription = "Backend engineer owning Python, Redis, PostgreSQL and safe releases.";
const resumeText = "Built resilient FastAPI services with cache recovery and production incident ownership.";

async function fillSources(page, jd = jobDescription, resume = resumeText) {
  const jdTab = page.getByRole("tab", { name: /岗位 JD/ });
  if (await jdTab.isVisible()) await jdTab.click();
  await page.getByLabel("岗位 JD").fill(jd);
  const resumeTab = page.getByRole("tab", { name: /候选人经历/ });
  if (await resumeTab.isVisible()) await resumeTab.click();
  await page.getByLabel("简历内容").fill(resume);
}

async function generatePlan(page, jd = jobDescription) {
  await page.goto("/prep");
  await fillSources(page, jd);
  await page.getByRole("button", { name: /生成并检查面试计划/ }).click();
  await expect(page.locator(".start-plan-editor")).toBeVisible();
  await expect(page.locator(".start-plan-question[data-enabled='true']")).toHaveCount(5);
}

async function expectVisuallyHidden(locator) {
  const state = await locator.evaluate((element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return {
      position: style.position,
      overflow: style.overflow,
      whiteSpace: style.whiteSpace,
      width: rect.width,
      height: rect.height,
    };
  });
  expect(state).toMatchObject({
    position: "absolute",
    overflow: "hidden",
    whiteSpace: "nowrap",
    width: 1,
    height: 1,
  });
}

test("preparation uses one pane state model and makes the plan authoritative", async ({ page }) => {
  await page.goto("/prep");
  await expect(page.locator(".start-prep-app-shell")).toBeVisible();
  await expect(page.locator(".start-activity-rail")).toBeVisible();
  await expect(page.locator(".start-inspector-tabs")).toBeVisible();
  await expect(page.locator(".start-prep-status-bar")).toBeVisible();

  await fillSources(page);
  await page.getByRole("button", { name: /生成并检查面试计划/ }).click();
  await expect(page.getByRole("heading", { name: "检查面试蓝图" })).toBeVisible();
  await expect(page.locator(".start-plan-editor")).toBeVisible();
  await expect(page.locator(".start-prep-launch-bar")).toContainText("版本 1");
  await expect(page.getByRole("button", { name: "确认版本并开始面试" })).toBeVisible();
});

test("plan editor presents a compact ledger with integrated metadata and controls", async ({ page }) => {
  await generatePlan(page);

  const editor = page.locator(".start-plan-editor");
  const first = page.locator(".start-plan-question[data-enabled='true']").first();
  const toolbar = first.locator(".start-plan-question-actions");

  await expect(editor.locator(".start-plan-editor-meta")).toContainText("v1 当前版本");
  await expect(editor.locator(".start-plan-editor-tag")).toHaveCount(3);
  await expect(editor.locator(".start-plan-editor-meta")).toContainText("python");
  await expect(page.locator(".start-prep-job-tags")).toHaveCount(0);
  await expect(toolbar.getByRole("button")).toHaveCount(5);
  await expect(first.getByRole("button", { name: /上移第 1 题/ })).toBeVisible();
  await expect(first.getByText(/个来源/)).toBeVisible();

  const geometry = await first.evaluate((question) => {
    const toolbarElement = question.querySelector(".start-plan-question-actions");
    const focusControl = question.querySelector(".start-plan-focus-control");
    const questionRect = question.getBoundingClientRect();
    const toolbarRect = toolbarElement.getBoundingClientRect();
    const focusRect = focusControl.getBoundingClientRect();
    const toolbarButtons = [...toolbarElement.querySelectorAll("button")]
      .map((button) => button.getBoundingClientRect());
    return {
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      questionHeight: questionRect.height,
      questionClientWidth: question.clientWidth,
      questionScrollWidth: question.scrollWidth,
      toolbarHeight: toolbarRect.height,
      toolbarInsideQuestion: toolbarRect.left >= questionRect.left - 1 && toolbarRect.right <= questionRect.right + 1,
      focusInsideQuestion: focusRect.left >= questionRect.left - 1 && focusRect.right <= questionRect.right + 1,
      toolbarButtonsMeetTouchTarget: toolbarButtons.every((button) => button.height >= 44),
    };
  });

  if (geometry.viewportWidth > 767) {
    expect(geometry.questionHeight).toBeLessThan(330);
    expect(geometry.toolbarHeight).toBeLessThanOrEqual(100);
  } else {
    expect(geometry.questionHeight).toBeLessThan(geometry.viewportHeight * 0.65);
    expect(geometry.toolbarHeight).toBeLessThan(geometry.viewportHeight * 0.2);
    expect(geometry.toolbarButtonsMeetTouchTarget).toBe(true);
  }
  expect(geometry.questionScrollWidth).toBeLessThanOrEqual(geometry.questionClientWidth + 1);
  expect(geometry.toolbarInsideQuestion).toBe(true);
  expect(geometry.focusInsideQuestion).toBe(true);
});

test("plan editor patches order, focus, required and enabled state with CAS", async ({ page }) => {
  await generatePlan(page);
  const patches = [];
  await page.route("**/api/prep-plans/*", async (route) => {
    if (route.request().method() === "PATCH") patches.push(route.request().postDataJSON());
    await route.continue();
  });

  const second = page.locator(".start-plan-question[data-enabled='true']").nth(1);
  await second.getByRole("button", { name: /上移第 2 题/ }).click();
  await expect.poll(() => patches.length).toBe(1);
  await expect(page.locator(".start-plan-announcement")).toContainText("第 2 题已上移");
  await expectVisuallyHidden(page.locator(".start-plan-announcement"));
  expect(patches[0]).toMatchObject({
    expected_version: 1,
    operations: [{ type: "move", position: 1 }],
  });

  const first = page.locator(".start-plan-question[data-enabled='true']").first();
  await first.getByLabel("考察重点").fill("新的生产可靠性重点");
  await first.getByRole("button", { name: "保存重点" }).click();
  await expect.poll(() => patches.length).toBe(2);
  expect(patches[1].operations[0]).toMatchObject({
    type: "set_focus",
    focus: "新的生产可靠性重点",
  });

  const requiredCandidate = page.locator(".start-plan-question[data-enabled='true']").last();
  await requiredCandidate.getByRole("button", { name: "设为必考" }).click();
  const requiredHelp = requiredCandidate.locator(".start-plan-action-help");
  await expect(requiredHelp).toBeVisible();
  await expect(requiredHelp).toContainText("请先选择“取消必考”");
  await expect(requiredHelp).toHaveAttribute("role", "note");
  await expect(requiredHelp).toHaveAttribute("tabindex", "0");
  await requiredHelp.focus();
  await expect(requiredHelp).toBeFocused();
  await expect(requiredCandidate.getByRole("button", { name: "排除" })).toBeDisabled();
  await requiredCandidate.getByRole("button", { name: "取消必考" }).click();
  await requiredCandidate.getByRole("button", { name: "排除" }).click();
  await expect(page.locator(".start-plan-question[data-enabled='true']")).toHaveCount(4);
  await expect(page.locator(".start-plan-question[data-enabled='false']")).toHaveCount(1);

  await page.locator(".start-plan-question[data-enabled='true']").last().getByRole("button", { name: "排除" }).click();
  await expect(page.locator(".start-plan-question[data-enabled='true']")).toHaveCount(3);
  const minimumCandidate = page.locator(".start-plan-question[data-enabled='true']").first();
  const minimumHelp = minimumCandidate.locator(".start-plan-action-help");
  await expect(minimumHelp).toBeVisible();
  await expect(minimumHelp).toContainText("当前只剩 3 道启用题");
  await expect(minimumHelp).toHaveAttribute("tabindex", "0");
  await minimumHelp.focus();
  await expect(minimumHelp).toBeFocused();
  await expect(minimumCandidate.getByRole("button", { name: "排除" })).toBeDisabled();
  expect(patches.every((payload) => Number.isInteger(payload.expected_version))).toBe(true);
});

test("maximum plan size exposes visible keyboard-focusable recovery guidance", async ({ page }) => {
  await page.route("**/api/prep", async (route) => {
    const response = await route.fetch();
    const plan = await response.json();
    plan.questions.push({
      ...plan.questions[0],
      question_id: "00000000-0000-4000-8000-000000000006",
      position: null,
      enabled: false,
      required: false,
      prompt: "容量边界候选题",
    });
    await route.fulfill({
      status: response.status(),
      headers: response.headers(),
      body: JSON.stringify(plan),
    });
  });
  await generatePlan(page);

  const excluded = page.locator(".start-plan-question[data-enabled='false']");
  const help = excluded.locator(".start-plan-action-help");
  await expect(excluded.getByRole("button", { name: "重新启用" })).toBeDisabled();
  await expect(help).toBeVisible();
  await expect(help).toContainText("当前已启用 5 道题");
  await expect(help).toContainText("请先排除一道非必考题");
  await expect(help).toHaveAttribute("tabindex", "0");
  await help.focus();
  await expect(help).toBeFocused();
});

test("single-question regeneration replaces only the target and preserves position", async ({ page }) => {
  await generatePlan(page);
  const first = page.locator(".start-plan-question[data-enabled='true']").first();
  const beforeText = await first.getByRole("heading").innerText();
  const beforeLabel = await first.getAttribute("aria-label");

  await first.getByRole("button", { name: "换一道" }).click();
  await expect(first.getByRole("heading")).toContainText("灰度发布演练");
  await expect(first).toHaveAttribute("aria-label", beforeLabel);
  await expect(first.getByRole("heading")).not.toHaveText(beforeText);
  await expect(page.locator(".start-prep-launch-bar")).toContainText("版本 2");
  await first.locator(".start-plan-question-evidence").click();
  await expect(first.locator("code")).toContainText("release_safety");
});

test("regeneration failure retains the original question and gives a safe error", async ({ page }) => {
  await generatePlan(page);
  const first = page.locator(".start-plan-question[data-enabled='true']").first();
  const original = await first.getByRole("heading").innerText();
  await page.route("**/api/prep-plans/*/questions/*/regenerate", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({
      detail: {
        code: "PREP_PLAN_REGENERATION_FAILED",
        message: "替代题暂时无法生成，原题已保留。",
        retryable: true,
      },
    }),
  }));
  await first.getByRole("button", { name: "换一道" }).click();
  await expect(first.getByRole("heading")).toHaveText(original);
  await expect(page.getByRole("alert")).toContainText("原题没有变化");
  await expect(page.getByRole("button", { name: /重试生成/ })).toHaveCount(0);
});

test("retryable plan generation exposes one page-owned retry action", async ({ page }) => {
  let attempts = 0;
  await page.route("**/api/prep", async (route) => {
    attempts += 1;
    if (attempts === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "PREP_TEMPORARILY_UNAVAILABLE",
            message: "准备服务暂时不可用。",
            retryable: true,
          },
        }),
      });
      return;
    }
    await route.continue();
  });

  await page.goto("/prep");
  await fillSources(page);
  await page.getByRole("button", { name: /生成并检查面试计划/ }).click();
  await expect(page.getByRole("button", { name: "重试生成" })).toBeVisible();
  await page.getByRole("button", { name: "重试生成" }).click();
  await expect(page.locator(".start-plan-editor")).toBeVisible();
  expect(attempts).toBe(2);
});

test("pending feedback delays fast spinners and keeps slow spinners stable", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium", "desktop owns the exact pending timing window");
  await page.route("**/api/prep", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 260));
    await route.continue();
  });
  await page.goto("/prep");
  await fillSources(page);
  const action = page.locator(".start-prep-primary-action");
  const spinner = page.locator(".start-app-topbar .start-spinner");
  await action.click();
  await expect(spinner).toHaveCount(0);
  await page.waitForTimeout(175);
  await expect(spinner).toBeVisible();
  const spinnerObservedAt = Date.now();
  await expect(page.locator(".start-plan-editor")).toBeVisible();
  await expect(spinner).toHaveCount(0, { timeout: 1_000 });
  expect(Date.now() - spinnerObservedAt).toBeGreaterThanOrEqual(250);
});

test("version conflict refreshes the latest plan instead of replaying a stale patch", async ({ page }) => {
  await generatePlan(page);
  let patchCount = 0;
  let refreshCount = 0;
  await page.route("**/api/prep-plans/*", async (route) => {
    if (route.request().method() === "PATCH") {
      patchCount += 1;
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "PREP_PLAN_VERSION_CONFLICT",
            message: "计划版本已变化。",
            retryable: true,
          },
        }),
      });
      return;
    }
    if (route.request().method() === "GET") refreshCount += 1;
    await route.continue();
  });
  await page.locator(".start-plan-question[data-enabled='true']").nth(1)
    .getByRole("button", { name: /上移第 2 题/ }).click();
  await expect.poll(() => refreshCount).toBe(1);
  expect(patchCount).toBe(1);
  await expect(page.locator(".start-notice-warning")).toContainText("已载入服务端最新版本");
});

test("source evidence is honest in both completed and degraded modes", async ({ page }) => {
  await generatePlan(page);
  const evidence = page.locator(".start-plan-question").first().locator(".start-plan-question-evidence");
  await evidence.click();
  await expect(evidence.locator("code")).toContainText("redis_consistency");
  await expect(evidence.locator(".start-plan-source-signals")).toContainText("知识证据");

  await generatePlan(page, `${jobDescription} simulate degraded`);
  const degradedEvidence = page.locator(".start-plan-question").first().locator(".start-plan-question-evidence");
  await degradedEvidence.click();
  await expect(degradedEvidence).toContainText("知识证据不可用");
  await expect(degradedEvidence.locator("code")).toHaveCount(0);
});

test("draft auto-save waits for 900ms of settled input and reports durability", async ({ page }) => {
  const writes = [];
  await page.route("**/api/interview-drafts", async (route) => {
    if (route.request().method() === "POST") writes.push(route.request().postDataJSON());
    await route.continue();
  });
  await page.goto("/prep");
  await fillSources(page);
  await page.waitForTimeout(500);
  expect(writes).toHaveLength(0);
  await expect.poll(() => writes.length, { timeout: 2_500 }).toBe(1);
  await page.waitForTimeout(1_100);
  expect(writes).toHaveLength(1);
  await expect(page.locator(".start-prep-draft-state")).toContainText(/持久保存|进程内临时保存/);
});

test("unsupported documents provide a paste fallback", async ({ page }) => {
  await page.goto("/prep");
  await page.locator('input[type="file"]').first().setInputFiles({
    name: "role.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("unsupported"),
  });
  await expect(page.getByRole("alert")).toContainText("复制其中的文本后粘贴");
});

test("prep geometry is bounded at the frozen Phase 2 widths", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium", "one explicit viewport owner");
  for (const viewport of [
    { width: 360, height: 800 },
    { width: 390, height: 844 },
    { width: 768, height: 1024 },
    { width: 900, height: 900 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto("/prep");
    await expect(page.locator(".start-document-canvas")).toBeVisible();
    const geometry = await page.evaluate(() => {
      const visibleDocuments = [...document.querySelectorAll(".start-document-canvas [data-document]")]
        .filter((element) => getComputedStyle(element).display !== "none").length;
      const controls = [...document.querySelectorAll(".start-prep-app-shell button")]
        .filter((item) => {
          const rect = item.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        });
      return {
        viewport: document.documentElement.clientWidth,
        document: document.documentElement.scrollWidth,
        visibleDocuments,
        controls: controls.map((item) => ({
          className: item.className,
          name: item.getAttribute("aria-label") || item.textContent.trim(),
          height: item.getBoundingClientRect().height,
        })),
      };
    });
    expect(geometry.document).toBeLessThanOrEqual(geometry.viewport);
    expect(geometry.visibleDocuments).toBe(1);
    expect(geometry.controls.filter((item) => item.height < 43.5)).toEqual([]);
  }
});

test("prep motion and focus honor accessibility preferences", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/prep");
  const action = page.getByRole("button", { name: /生成并检查面试计划/ });
  await action.focus();
  await expect(action).toBeFocused();
  const state = await action.evaluate((element) => ({
    duration: getComputedStyle(document.querySelector(".start-editor-workspace")).animationDuration,
    outlineStyle: getComputedStyle(element).outlineStyle,
    outlineWidth: getComputedStyle(element).outlineWidth,
  }));
  expect(["0s", "1e-05s", "none"]).toContain(state.duration);
  expect(state.outlineStyle).toBe("solid");
  expect(state.outlineWidth).toBe("2px");
});

const prepWorkbenchViewports = [
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

  for (const viewport of prepWorkbenchViewports) {
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

test("React preparation validates imports and renders the authoritative plan", async ({ page }) => {
  await page.goto("/prep");
  await page.locator('input[type="file"]').first().setInputFiles({
    name: "role.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("unsupported"),
  });
  await expect(page.getByRole("alert")).toContainText("复制其中的文本后粘贴");
  await page.locator('input[type="file"]').first().setInputFiles({
    name: "role.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(jobDescription),
  });
  await expect(page.getByLabel("岗位 JD")).toHaveValue(jobDescription);
  await page.locator('input[type="file"]').nth(1).setInputFiles({
    name: "resume.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(resumeText),
  });
  await expect(page.getByLabel("简历内容")).toHaveValue(resumeText);
  await page.getByRole("button", { name: /生成并检查面试计划/ }).click();
  await expect(page.locator(".start-plan-question")).toHaveCount(5);
  await expect(page.locator(".start-prep-launch-bar")).toContainText("20–30 分钟");
  const evidence = page.locator(".start-plan-question-evidence").first();
  await evidence.click();
  await expect(evidence.locator("code")).toContainText("redis_consistency");
});

test("preparation validation focuses the missing document and uses restrained feedback", async ({ page }) => {
  await page.goto("/prep");
  await expect(page.locator(".prep-stepper")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "保存草稿", exact: true }).locator("svg")).toHaveCount(1);
  await page.getByRole("button", { name: /生成并检查面试计划/ }).click();
  await expect(page.getByRole("alert")).toContainText("岗位 JD");
  await expect(page.getByLabel("岗位 JD")).toBeFocused();
  await expect(page.getByLabel("岗位 JD")).toHaveAttribute("aria-invalid", "true");

  await page.getByLabel("岗位 JD").fill(jobDescription);
  await page.getByRole("button", { name: /生成并检查面试计划/ }).click();
  await expect(page.getByRole("alert")).toContainText("候选人经历");
  await expect(page.getByLabel("简历内容")).toBeFocused();
  const colors = await page.getByRole("alert").evaluate((element) => ({
    background: getComputedStyle(element).backgroundColor,
    body: getComputedStyle(document.body).backgroundColor,
  }));
  expect(colors.background).not.toBe(colors.body);
});

test("primary preparation action preserves its hierarchy while generating", async ({ page }) => {
  await page.goto("/prep");
  await fillSources(page);
  await page.route("**/api/prep", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 500));
    await route.continue();
  });
  const action = page.locator(".start-prep-primary-action");
  await action.click();
  await expect(action).toHaveAttribute("aria-busy", "true");
  const state = await action.evaluate((element) => ({
    height: element.getBoundingClientRect().height,
    opacity: Number(getComputedStyle(element).opacity),
    iconCount: element.querySelectorAll("svg").length,
  }));
  expect(state.height).toBeGreaterThanOrEqual(48);
  expect(state.opacity).toBeGreaterThanOrEqual(.85);
  expect(state.iconCount).toBe(1);
  await expect(page.locator(".start-plan-question")).toHaveCount(5);
});

test("draft actions save, restore and guard destructive clearing", async ({ page }) => {
  await page.goto("/prep");
  await fillSources(page);
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect.poll(() => page.evaluate(() => localStorage.getItem("interview-agent:draft-id"))).not.toBeNull();
  await expect(page.locator(".start-prep-draft-state")).toContainText(/持久保存|进程内临时保存/);

  await page.getByRole("button", { name: "清空当前画布" }).click();
  await expect(page.locator(".start-notice-warning")).toContainText("再次点击");
  await expect(page.getByLabel("岗位 JD")).toHaveValue(jobDescription);
  await page.getByRole("button", { name: "确认清空画布" }).click();
  await expect(page.getByLabel("岗位 JD")).toHaveValue("");

  await page.reload();
  await page.getByRole("button", { name: "恢复草稿" }).click();
  await expect(page.getByLabel("岗位 JD")).toHaveValue(jobDescription);
  await expect(page.getByLabel("简历内容")).toHaveValue(resumeText);
});

test("degraded knowledge stays honest without blocking launch", async ({ page }) => {
  await page.goto("/prep");
  await fillSources(page, `${jobDescription} simulate degraded`);
  await page.getByRole("button", { name: /生成并检查面试计划/ }).click();
  const evidence = page.locator(".start-plan-question-evidence").first();
  await evidence.click();
  await expect(evidence).toContainText("知识证据不可用");
  await expect(evidence.locator("code")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /确认版本并开始面试/ })).toBeEnabled();
});

test("reduced motion disables preparation animations", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/prep");
  const duration = await page.locator(".start-editor-workspace").evaluate((element) => getComputedStyle(element).animationDuration);
  expect(["0s", "1e-05s"]).toContain(duration);
});
