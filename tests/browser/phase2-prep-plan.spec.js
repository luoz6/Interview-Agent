const { test, expect } = require("@playwright/test");

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
  await expect(requiredCandidate.getByRole("button", { name: "排除" })).toBeDisabled();
  await requiredCandidate.getByRole("button", { name: "取消必考" }).click();
  await requiredCandidate.getByRole("button", { name: "排除" }).click();
  await expect(page.locator(".start-plan-question[data-enabled='true']")).toHaveCount(4);
  await expect(page.locator(".start-plan-question[data-enabled='false']")).toHaveCount(1);
  expect(patches.every((payload) => Number.isInteger(payload.expected_version))).toBe(true);
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
  await action.click();
  await expect(page.locator(".start-app-topbar .start-spinner")).toHaveCount(0);
  await page.waitForTimeout(175);
  await expect(page.locator(".start-app-topbar .start-spinner")).toBeVisible();
  await expect(page.locator(".start-plan-editor")).toBeVisible();
  await expect(page.locator(".start-app-topbar .start-spinner")).toBeVisible();
  await expect(page.locator(".start-app-topbar .start-spinner")).toHaveCount(0, { timeout: 1_000 });
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
