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

async function generatePlan(page, jd = jobDescription, expectedQuestionCount = 5) {
  await page.goto("/prep");
  await fillSources(page, jd);
  await page.getByRole("button", { name: /生成(?:并检查)?面试计划/ }).click();
  const planRegion = page.getByRole("region", { name: "面试计划" });
  await expect(planRegion.getByRole("list")).toBeVisible();
  await expect(planRegion.getByRole("listitem")).toHaveCount(expectedQuestionCount);
}

test("preparation uses one pane state model and makes the plan authoritative", async ({ page }) => {
  await page.goto("/prep");
  await expect(page.locator(".start-app-root")).toBeVisible();
  await expect(page.getByRole("main")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "准备工作区" })).toBeVisible();
  await expect(page.getByRole("tablist", { name: "工作面板视图" })).toBeVisible();
  await expect(page.getByRole("contentinfo", { name: "工作区状态" })).toBeVisible();

  await fillSources(page);
  await page.getByRole("button", { name: /生成(?:并检查)?面试计划/ }).click();
  const planRegion = page.getByRole("region", { name: "面试计划" });
  await expect(planRegion.getByRole("list")).toBeVisible();
  await expect(planRegion.getByRole("listitem")).toHaveCount(5);
  await expect(page.locator(".start-revision-state")).toContainText(/已保存.*R1/);
  await expect(page.getByRole("button", { name: /^(?:确认版本并)?开始(?:本次)?面试$/ })).toBeVisible();
});

test("plan editor presents a compact ledger with integrated metadata and controls", async ({ page }, testInfo) => {
  await page.setViewportSize(
    testInfo.project.name === "mobile-chromium"
      ? { width: 390, height: 844 }
      : { width: 1440, height: 900 },
  );
  await generatePlan(page);

  const planRegion = page.getByRole("region", { name: "面试计划" });
  const first = planRegion.getByRole("listitem").first();
  const toolbar = first.locator(".start-plan-question-actions");
  const tags = page.locator("[aria-label='岗位标签']");

  await expect(page.locator(".start-revision-state")).toContainText(/已保存.*R1/);
  await expect(tags).toHaveCount(1);
  await expect(tags.locator("span")).toHaveCount(3);
  await expect(tags).toContainText("python");
  await expect(first.getByRole("button")).toHaveCount(5);
  for (const button of await first.getByRole("button").all()) {
    await button.scrollIntoViewIfNeeded();
    await expect(button).toBeVisible();
    if (await button.isEnabled()) {
      await button.focus();
      await expect(button).toBeFocused();
    }
  }
  await first.getByLabel("考察重点").focus();
  await expect(first.getByLabel("考察重点")).toBeFocused();
  await expect(first.getByRole("button", { name: /^(?:上移第 1 题|将第 1 题上移)$/ })).toBeVisible();
  await expect(first).toContainText("证据有效");

  const geometry = await first.evaluate((question) => {
    const toolbarElement = question.querySelector(".start-plan-question-actions");
    const focusControl = question.querySelector("input[id$='-focus']");
    const questionRect = question.getBoundingClientRect();
    const nextQuestionRect = question.nextElementSibling?.getBoundingClientRect() || null;
    const toolbarRect = toolbarElement.getBoundingClientRect();
    const focusRect = focusControl.getBoundingClientRect();
    const questionButtons = [...question.querySelectorAll("button")]
      .map((button) => {
        const rect = button.getBoundingClientRect();
        return {
          name: button.getAttribute("aria-label") || button.textContent.trim(),
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
          width: rect.width,
          height: rect.height,
        };
      });
    return {
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      questionHeight: questionRect.height,
      questionClientWidth: question.clientWidth,
      questionScrollWidth: question.scrollWidth,
      questionContentNotClipped: question.scrollHeight <= question.clientHeight + 1,
      nextQuestionFollows: !nextQuestionRect || nextQuestionRect.top >= questionRect.bottom - 1,
      toolbarHeight: toolbarRect.height,
      toolbarInsideQuestion: toolbarRect.left >= questionRect.left - 1 && toolbarRect.right <= questionRect.right + 1 && toolbarRect.top >= questionRect.top - 1 && toolbarRect.bottom <= questionRect.bottom + 1,
      focusInsideQuestion: focusRect.left >= questionRect.left - 1 && focusRect.right <= questionRect.right + 1 && focusRect.top >= questionRect.top - 1 && focusRect.bottom <= questionRect.bottom + 1,
      questionButtonsInside: questionButtons.every((button) => button.left >= questionRect.left - 1 && button.right <= questionRect.right + 1 && button.top >= questionRect.top - 1 && button.bottom <= questionRect.bottom + 1),
      questionButtonMetrics: questionButtons.map(({ name, width, height }) => ({ name, width, height })),
    };
  });

  if (geometry.viewportWidth > 767) {
    expect(geometry.questionHeight).toBeLessThan(geometry.viewportHeight * 0.7);
    expect(geometry.toolbarHeight).toBeLessThanOrEqual(geometry.viewportHeight * 0.125);
  } else {
    expect(geometry.questionHeight).toBeLessThan(geometry.viewportHeight * 0.85);
    expect(geometry.toolbarHeight).toBeLessThan(geometry.viewportHeight * 0.2);
  }
  expect(geometry.questionScrollWidth).toBeLessThanOrEqual(geometry.questionClientWidth + 1);
  expect(geometry.questionContentNotClipped).toBe(true);
  expect(geometry.nextQuestionFollows).toBe(true);
  expect(geometry.toolbarInsideQuestion).toBe(true);
  expect(geometry.focusInsideQuestion).toBe(true);
  expect(geometry.questionButtonsInside).toBe(true);
  expect(geometry.questionButtonMetrics.filter((button) => button.height < 44)).toEqual([]);
});

test("plan editor persists order, focus, and deletion with revision CAS", async ({ page }) => {
  await generatePlan(page);
  const patches = [];
  await page.route("**/api/interview-plans/**", async (route) => {
    if (route.request().method() === "PATCH") {
      patches.push({
        url: route.request().url(),
        payload: route.request().postDataJSON(),
      });
    }
    await route.continue();
  });

  const revisionState = page.locator(".start-revision-state");
  const questions = page.locator(".start-plan-question");
  const secondOriginalText = await questions.nth(1).getByLabel("问题内容").inputValue();
  await questions.nth(1).getByRole("button", { name: "将第 2 题上移" }).click();
  await expect.poll(() => patches.length).toBe(1);
  await expect(revisionState).toContainText("R2");
  await expect(questions.first().getByLabel("问题内容")).toHaveValue(secondOriginalText);
  expect(patches[0].payload).toMatchObject({
    expected_revision: 1,
    operations: [{ op: "move_question", to_position: 1 }],
  });

  const first = questions.first();
  await first.getByLabel("考察重点").fill("新的生产可靠性重点");
  await first.getByRole("button", { name: "保存修改" }).click();
  await expect.poll(() => patches.length).toBe(2);
  await expect(revisionState).toContainText("R3");
  await expect(first.getByLabel("考察重点")).toHaveValue("新的生产可靠性重点");
  expect(patches[1].payload).toMatchObject({
    expected_revision: 2,
    operations: [{ op: "edit_focus", focus: "新的生产可靠性重点" }],
  });

  for (let currentCount = 5; currentCount > 1; currentCount -= 1) {
    const patchCountBefore = patches.length;
    const beforeTexts = await questions.getByLabel("问题内容").evaluateAll(
      (inputs) => inputs.map((input) => input.value),
    );
    const trigger = questions.last().getByRole("button", { name: "删除" });
    await trigger.focus();
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: new RegExp(`删除第 ${currentCount} 题`) });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("删除会创建新的计划修订");
    await expect(questions).toHaveCount(currentCount);
    expect(patches).toHaveLength(patchCountBefore);
    await dialog.getByRole("button", { name: "删除并保存" }).click();
    await expect.poll(() => patches.length).toBe(patchCountBefore + 1);
    await expect(questions).toHaveCount(currentCount - 1);
    expect(await questions.getByLabel("问题内容").evaluateAll(
      (inputs) => inputs.map((input) => input.value),
    )).toEqual(beforeTexts.slice(0, -1));
    await expect(revisionState).toContainText(`R${patchCountBefore + 2}`);
  }

  await expect(questions).toHaveCount(1);
  await expect(revisionState).toContainText("R7");
  await expect(questions.first().getByRole("button", { name: "删除" })).toBeDisabled();
  expect(patches.map(({ payload }) => payload.expected_revision)).toEqual([1, 2, 3, 4, 5, 6]);
  expect(patches.every(({ url }) => /\/api\/interview-plans\//.test(url))).toBe(true);
  expect(patches.every(({ payload }) => typeof payload.request_id === "string" && payload.request_id.length > 0)).toBe(true);
  expect(new Set(patches.map(({ payload }) => payload.request_id)).size).toBe(patches.length);
  expect(patches.slice(2).every(({ payload }) => payload.operations[0].op === "delete_question")).toBe(true);
});

test("maximum plan size exposes visible keyboard-focusable recovery guidance", async ({ page }) => {
  await page.route("**/api/prep", async (route) => {
    const response = await route.fetch();
    const plan = await response.json();
    const template = plan.plan.questions[0];
    for (let position = 6; position <= 10; position += 1) {
      plan.plan.questions.push({
        ...template,
        question_id: `00000000-0000-4000-8000-${String(position).padStart(12, "0")}`,
        position,
        question_text: `容量边界题 ${position}`,
        focus: `容量边界 ${position}`,
      });
    }
    await route.fulfill({
      status: response.status(),
      headers: response.headers(),
      body: JSON.stringify(plan),
    });
  });
  await generatePlan(page, jobDescription, 10);

  const questions = page.getByRole("region", { name: "面试计划" }).getByRole("listitem");
  const add = page.getByRole("button", { name: "添加题目" });
  const help = page.locator("#plan-capacity-note");
  await expect(questions).toHaveCount(10);
  await expect(add).toBeDisabled();
  await expect(add).toHaveAttribute("aria-describedby", "plan-capacity-note");
  await expect(help).toBeVisible();
  await expect(help).toContainText("已达到 10 题上限，请先删除一道题再添加");
  await expect(help).toHaveAttribute("role", "note");
  await expect(help).toHaveAttribute("tabindex", "0");
  await help.focus();
  await expect(help).toBeFocused();
});

test("single-question regeneration replaces only the target and preserves position", async ({ page }) => {
  await generatePlan(page);
  const questions = page.getByRole("region", { name: "面试计划" }).getByRole("listitem");
  const first = questions.first();
  const beforeText = await first.getByLabel("问题内容").inputValue();
  const beforeFocus = await first.getByLabel("考察重点").inputValue();
  const beforeTextId = await first.getByLabel("问题内容").getAttribute("id");
  const secondText = await questions.nth(1).getByLabel("问题内容").inputValue();

  const [request, response] = await Promise.all([
    page.waitForRequest((candidate) =>
      candidate.method() === "POST" &&
      /\/api\/interview-plans\/[^/]+\/questions\/[^/]+\/regenerate$/.test(candidate.url()),
    ),
    page.waitForResponse((candidate) =>
      candidate.request().method() === "POST" &&
      /\/api\/interview-plans\/[^/]+\/questions\/[^/]+\/regenerate$/.test(candidate.url()),
    ),
    first.getByRole("button", { name: "换题" }).click(),
  ]);

  const payload = request.postDataJSON();
  const revision = await response.json();
  const replacement = revision.plan.questions[0];
  expect(payload.expected_revision).toBe(1);
  expect(typeof payload.request_id).toBe("string");
  expect(payload.request_id.length).toBeGreaterThan(0);
  expect(replacement.replaces_question_id).toBe(beforeTextId.replace("plan-question-", ""));
  expect(replacement.origin).toBe("regenerated");
  expect(replacement.position).toBe(1);
  expect(replacement.question_text).not.toBe(beforeText);
  expect(replacement.focus).not.toBe(beforeFocus);
  expect(revision.plan.questions[1].question_text).toBe(secondText);

  await expect(questions).toHaveCount(5);
  await expect(first.getByLabel("问题内容")).toHaveValue(replacement.question_text);
  await expect(first.getByLabel("问题内容")).not.toHaveValue(beforeText);
  await expect(first.getByLabel("考察重点")).toHaveValue(replacement.focus);
  await expect(first.getByLabel("问题内容")).not.toHaveAttribute("id", beforeTextId);
  await expect(first).toContainText("已换题");
  await expect(first).toContainText("证据有效");
  await expect(questions.nth(1).getByLabel("问题内容")).toHaveValue(secondText);
  await expect(page.locator(".start-revision-state")).toContainText(/已保存.*R2/);
});

test("regeneration failure retains the original question and gives a safe error", async ({ page }) => {
  await generatePlan(page);
  const questions = page.getByRole("region", { name: "面试计划" }).getByRole("listitem");
  const first = questions.first();
  const originalText = await first.getByLabel("问题内容").inputValue();
  const originalFocus = await first.getByLabel("考察重点").inputValue();
  const originalTextId = await first.getByLabel("问题内容").getAttribute("id");
  const secondText = await questions.nth(1).getByLabel("问题内容").inputValue();
  let regenerationRequest;
  await page.route("**/api/interview-plans/*/questions/*/regenerate", (route) => {
    regenerationRequest = route.request().postDataJSON();
    return route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          code: "provider_timeout",
          message: "替代题暂时无法生成，原题已保留。",
          retryable: true,
        },
      }),
    });
  });

  await first.getByRole("button", { name: "换题" }).click();

  expect(regenerationRequest.expected_revision).toBe(1);
  expect(typeof regenerationRequest.request_id).toBe("string");
  expect(regenerationRequest.request_id.length).toBeGreaterThan(0);
  await expect(questions).toHaveCount(5);
  await expect(first.getByLabel("问题内容")).toHaveValue(originalText);
  await expect(first.getByLabel("考察重点")).toHaveValue(originalFocus);
  await expect(first.getByLabel("问题内容")).toHaveAttribute("id", originalTextId);
  await expect(first).not.toContainText("已换题");
  await expect(questions.nth(1).getByLabel("问题内容")).toHaveValue(secondText);
  await expect(page.locator(".start-revision-state")).toContainText("R1");
  await expect(page.locator(".start-revision-state")).toHaveAttribute("data-state", "failed");
  const failure = page.locator(".start-plan-state-panel[data-state='failed']");
  await expect(failure).toBeVisible();
  await expect(failure).toContainText("替代题暂时无法生成，原题已保留");
  await expect(failure).toContainText("本地输入没有丢失");
  await expect(failure.getByRole("button", { name: "重新载入服务端版本" })).toBeEnabled();
  await expect(first.getByRole("button", { name: "换题" })).toBeEnabled();
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
  const generate = page.getByRole("button", { name: /生成(?:并检查)?面试计划/ });
  await generate.click();
  await expect(page.getByRole("alert")).toContainText("准备服务暂时不可用");
  await page.getByRole("tab", { name: /岗位 JD/ }).click();
  await expect(page.getByLabel("岗位 JD")).toHaveValue(jobDescription);
  await page.getByRole("tab", { name: /候选人经历/ }).click();
  await expect(page.getByLabel("简历内容")).toHaveValue(resumeText);
  await expect(generate).toHaveCount(1);
  await expect(generate).toBeEnabled();
  await expect(page.getByRole("button", { name: "重试生成" })).toHaveCount(0);
  await generate.click();
  const questions = page.getByRole("region", { name: "面试计划" }).getByRole("listitem");
  await expect(questions).toHaveCount(5);
  await expect(page.locator(".start-revision-state")).toContainText(/已保存.*R1/);
  expect(attempts).toBe(2);
});

test("pending feedback delays fast spinners and keeps slow spinners stable", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium", "desktop owns the exact pending timing window");
  let attempts = 0;
  await page.route("**/api/prep", async (route) => {
    attempts += 1;
    await new Promise((resolve) => setTimeout(resolve, attempts === 1 ? 50 : 260));
    await route.continue();
  });

  const spinner = page.locator(".start-app-topbar .start-runtime .start-spinner");

  await page.goto("/prep");
  await fillSources(page);
  await page.getByRole("button", { name: /生成(?:并检查)?面试计划/ }).click();
  await expect(spinner).toHaveCount(0);
  await expect(page.getByRole("region", { name: "面试计划" }).getByRole("listitem")).toHaveCount(5);
  await page.waitForTimeout(150);
  await expect(spinner).toHaveCount(0);

  await page.goto("/prep");
  await fillSources(page);
  const action = page.getByRole("button", { name: /生成(?:并检查)?面试计划/ });
  await page.evaluate(() => {
    const events = [];
    let status = document.body.dataset.prepState;
    let spinnerVisible = Boolean(document.querySelector(".start-app-topbar .start-runtime .start-spinner"));
    const sample = () => {
      const nextStatus = document.body.dataset.prepState;
      const nextSpinnerVisible = Boolean(document.querySelector(".start-app-topbar .start-runtime .start-spinner"));
      if (nextStatus !== status) {
        status = nextStatus;
        events.push({ type: status === "generating" ? "pending" : `status:${status}`, at: performance.now() });
      }
      if (nextSpinnerVisible !== spinnerVisible) {
        spinnerVisible = nextSpinnerVisible;
        events.push({ type: spinnerVisible ? "shown" : "hidden", at: performance.now() });
      }
    };
    const observer = new MutationObserver(sample);
    observer.observe(document.body, { attributes: true, childList: true, subtree: true });
    window.__phase2RuntimeSpinner = { events, observer };
  });
  await action.click();
  await expect(spinner).toHaveCount(0);
  await page.waitForTimeout(175);
  await expect(spinner).toBeVisible();
  await expect(page.getByRole("region", { name: "面试计划" }).getByRole("listitem")).toHaveCount(5);
  await expect(spinner).toHaveCount(0, { timeout: 1_000 });
  const spinnerEvents = await page.evaluate(() => {
    window.__phase2RuntimeSpinner.observer.disconnect();
    return window.__phase2RuntimeSpinner.events;
  });
  const pendingEvent = spinnerEvents.find((event) => event.type === "pending");
  const shownEvent = spinnerEvents.find((event) => event.type === "shown");
  const hiddenEvent = shownEvent
    ? spinnerEvents.find((event) => event.type === "hidden" && event.at > shownEvent.at)
    : null;
  expect(pendingEvent).toBeTruthy();
  expect(shownEvent).toBeTruthy();
  expect(hiddenEvent).toBeTruthy();
  expect(shownEvent.at - pendingEvent.at).toBeGreaterThanOrEqual(145);
  expect(hiddenEvent.at - shownEvent.at).toBeGreaterThanOrEqual(295);
  expect(attempts).toBe(2);
});

test("version conflict preserves local input and adopts a fetched server revision without replay", async ({ page }) => {
  let initialPlan;
  await page.route("**/api/prep", async (route) => {
    const response = await route.fetch();
    initialPlan = await response.json();
    await route.fulfill({
      status: response.status(),
      headers: response.headers(),
      body: JSON.stringify(initialPlan),
    });
  });
  await generatePlan(page);
  const serverRevisionId = "22222222-2222-4222-8222-222222222222";
  const serverQuestionText = "Server-authored concurrent wording.";
  const serverPlan = structuredClone(initialPlan);
  serverPlan.plan_revision_id = serverRevisionId;
  serverPlan.revision = 2;
  serverPlan.plan_sha256 = "2".repeat(64);
  serverPlan.plan.title = "Server R2 plan";
  serverPlan.plan.questions[0].question_text = serverQuestionText;
  serverPlan.legacy_plan.title = "Server R2 plan";
  serverPlan.legacy_plan.questions[0].prompt = serverQuestionText;
  let patchCount = 0;
  let previewCount = 0;
  let patchPayload;
  await page.route("**/api/interview-plans/**", async (route) => {
    if (route.request().method() === "PATCH") {
      patchCount += 1;
      patchPayload = route.request().postDataJSON();
      await route.fulfill({
        status: 409,
        contentType: "application/json",
        body: JSON.stringify({
          code: "plan_revision_conflict",
          current_revision: {
            plan_revision_id: serverRevisionId,
            revision: 2,
            plan_sha256: serverPlan.plan_sha256,
          },
        }),
      });
      return;
    }
    if (route.request().method() === "GET" && route.request().url().endsWith(`/revisions/${serverRevisionId}`)) {
      previewCount += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(serverPlan),
      });
      return;
    }
    await route.continue();
  });

  const first = page.getByRole("region", { name: "面试计划" }).getByRole("listitem").first();
  const localQuestionText = "Keep this local wording through the conflict.";
  await first.getByLabel("问题内容").fill(localQuestionText);
  await first.getByRole("button", { name: "保存修改" }).click();

  const conflict = page.locator(".start-plan-state-panel[data-state='conflict']");
  await expect(conflict).toBeVisible();
  await expect(conflict).toContainText("服务端当前为 R2");
  await expect(first.getByLabel("问题内容")).toHaveValue(localQuestionText);
  await expect(page.locator(".start-revision-state")).toContainText(/版本冲突.*R1/);
  expect(patchCount).toBe(1);
  expect(patchPayload.expected_revision).toBe(1);
  expect(typeof patchPayload.request_id).toBe("string");
  expect(patchPayload.request_id.length).toBeGreaterThan(0);

  await conflict.getByRole("button", { name: "查看服务端版本" }).click();
  await expect.poll(() => previewCount).toBe(1);
  await expect(conflict).toContainText("服务端预览 · R2");
  await expect(conflict).toContainText("Server R2 plan");
  await expect(first.getByLabel("问题内容")).toHaveValue(localQuestionText);
  expect(patchCount).toBe(1);

  await conflict.getByRole("button", { name: "使用服务端版本" }).click();
  await expect(conflict).toHaveCount(0);
  await expect(page.getByRole("region", { name: "面试计划" }).getByRole("listitem").first().getByLabel("问题内容")).toHaveValue(serverQuestionText);
  await expect(page.locator(".start-revision-state")).toContainText(/已保存.*R2/);
  expect(patchCount).toBe(1);
});

test("source evidence is honest in both completed and degraded modes", async ({ page }) => {
  await generatePlan(page);
  const first = page.getByRole("region", { name: "面试计划" }).getByRole("listitem").first();
  await expect(first).toContainText("证据有效");
  await page.getByRole("tab", { name: "证据" }).click();
  const evidence = page.getByRole("region", { name: "知识证据" });
  await expect(evidence).toContainText("检索完成");
  await expect(evidence.getByLabel("考察主题")).toContainText("Redis");
  const redisEvidence = evidence.locator("[data-evidence-id='redis_consistency']");
  await expect(redisEvidence).toBeVisible();
  await expect(redisEvidence).toContainText("Redis Cache Consistency");
  await expect(redisEvidence.locator("code")).toContainText("理论资料 / redis_consistency");

  await generatePlan(page, `${jobDescription} simulate degraded`);
  const degradedFirst = page.getByRole("region", { name: "面试计划" }).getByRole("listitem").first();
  await expect(degradedFirst).toContainText("未绑定证据");
  await page.getByRole("tab", { name: "证据" }).click();
  const degradedEvidence = page.getByRole("region", { name: "知识证据" });
  await expect(degradedEvidence).toContainText("知识检索已降级");
  await expect(degradedEvidence).toContainText("系统不会展示不存在的引用");
  await expect(degradedEvidence.locator("[data-evidence-id]")).toHaveCount(0);
  await expect(degradedEvidence.locator("code")).toHaveCount(0);

  await page.route("**/api/prep", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    for (const context of [payload.plan.prep_context, payload.legacy_plan.prep_context]) {
      context.knowledge_status = "keyword";
      context.topics = [];
      context.evidence_refs = [];
      context.degraded_reason = "internal-keyword-reason";
      if (context.binding_snapshot) {
        context.binding_snapshot.degraded_reason = "internal-keyword-reason";
      }
    }
    for (const question of payload.plan.questions) {
      question.knowledge_binding = {
        ...question.knowledge_binding,
        status: "unbound",
        evidence_ids: [],
        reason_code: "keyword_only",
      };
    }
    await route.fulfill({
      status: response.status(),
      headers: response.headers(),
      body: JSON.stringify(payload),
    });
  });
  await generatePlan(page, `${jobDescription} keyword mode`);
  const keywordFirst = page.getByRole("region", { name: "面试计划" }).getByRole("listitem").first();
  await expect(keywordFirst).toContainText("未绑定证据");
  await page.getByRole("tab", { name: "证据" }).click();
  const keywordEvidence = page.getByRole("region", { name: "知识证据" });
  await expect(keywordEvidence).toContainText("关键词准备");
  await expect(keywordEvidence).toContainText("本次计划仅使用关键词信号");
  await expect(keywordEvidence).toContainText("没有可展示的证据引用");
  await expect(keywordEvidence.locator("[data-evidence-id]")).toHaveCount(0);
  await expect(page.getByRole("contentinfo", { name: "工作区状态" })).toContainText("关键词准备");
  await expect(page.locator("body")).not.toContainText("internal-keyword-reason");
  await expect(page.locator("body")).not.toContainText("degraded_reason");
});

test("draft save is explicit, singular, and reports actual durability", async ({ page }) => {
  const writes = [];
  await page.route("**/api/interview-drafts", async (route) => {
    if (route.request().method() === "POST") writes.push(route.request().postDataJSON());
    await route.continue();
  });
  await page.goto("/prep");
  await fillSources(page);
  await page.waitForTimeout(1_100);
  expect(writes).toHaveLength(0);
  const save = page.getByRole("button", { name: "保存草稿" });
  await expect(save).toHaveCount(1);
  await expect(save).toBeEnabled();
  await save.click();
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toMatchObject({
    draft_id: null,
    job_description: jobDescription,
    resume_text: resumeText,
    title: null,
    job_tags: null,
    plan_family_id: null,
    latest_plan_revision_id: null,
  });
  await expect(page.locator(".start-notice-success")).toContainText(/持久保存|进程内临时保存/);
  await expect(page.getByRole("contentinfo", { name: "工作区状态" })).toContainText(/草稿(?:持久保存|进程内临时保存)/);
  await expect.poll(() => page.evaluate(() => localStorage.getItem("interview-agent:draft-id"))).toMatch(/^draft_/);
  await page.waitForTimeout(1_100);
  expect(writes).toHaveLength(1);
});

test("source imports use exact targets, invalidate plans only on success, and preserve state on safe failure", async ({ page }) => {
  const observedTargets = [];
  const mutations = [];
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (request.method() === "POST" && pathname === "/api/prep") mutations.push("generate");
    if (request.method() === "POST" && pathname === "/api/interview-drafts") mutations.push("draft");
    if (request.method() === "PATCH" && pathname.startsWith("/api/interview-plans/")) mutations.push("plan-patch");
  });
  await page.route("**/api/prep/source-imports", async (route) => {
    const body = route.request().postDataBuffer()?.toString("utf8") || "";
    const target = body.match(/name="target"\r?\n\r?\n([^\r\n]+)/)?.[1];
    observedTargets.push(target);
    if (body.includes('filename="legacy-role.doc"')) {
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          detail: {
            code: "unsupported_file_type",
            message: "仅支持 PDF、DOCX、Markdown 或 TXT 文件；请复制文本后粘贴。",
            parser: "private parser traceback",
            content_sha256: "secret-hash",
          },
        }),
      });
      return;
    }
    const imported = target === "job_description"
      ? { filename: "backend-role.pdf", mediaType: "application/pdf", text: "Imported backend role" }
      : { filename: "production-resume.txt", mediaType: "text/plain", text: "Imported production resume" };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        target,
        filename: imported.filename,
        media_type: imported.mediaType,
        text: imported.text,
        character_count: imported.text.length,
        truncated: false,
        warning_codes: [],
      }),
    });
  });

  await generatePlan(page);
  const start = page.getByRole("button", { name: "开始本次面试" });
  await expect(page.locator(".start-revision-state")).toContainText(/已保存.*R1/);
  await expect(start).toBeEnabled();

  await page.getByRole("tab", { name: /岗位 JD/ }).click();
  await page.getByLabel("导入当前岗位文档").setInputFiles({
    name: "backend-role.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("mock pdf"),
  });
  await expect(page.getByLabel("岗位 JD")).toHaveValue("Imported backend role");
  await expect(page.locator(".start-revision-state")).toHaveCount(0);
  await expect(page.locator(".start-plan-question")).toHaveCount(0);
  await expect(start).toBeDisabled();
  await expect(page.getByRole("button", { name: "生成面试计划" })).toBeEnabled();

  await page.getByRole("tab", { name: /候选人经历/ }).click();
  await page.getByLabel("导入当前经历文档").setInputFiles({
    name: "production-resume.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("mock resume"),
  });
  await expect(page.getByLabel("简历内容")).toHaveValue("Imported production resume");
  expect(observedTargets).toEqual(["job_description", "resume_text"]);
  await page.waitForTimeout(200);
  expect(mutations).toEqual(["generate"]);

  await page.getByRole("button", { name: "生成面试计划" }).click();
  await expect(page.locator(".start-plan-question")).toHaveCount(5);
  await expect(page.locator(".start-revision-state")).toContainText(/已保存.*R1/);
  await expect(start).toBeEnabled();
  expect(mutations).toEqual(["generate", "generate"]);

  await page.getByRole("tab", { name: /岗位 JD/ }).click();
  const jdEditor = page.locator(".start-document-editor").filter({ has: page.getByLabel("岗位 JD") });
  await expect(jdEditor.locator(".start-document-file")).toContainText("backend-role.pdf · 已导入");
  await page.getByLabel("导入当前岗位文档").setInputFiles({
    name: "legacy-role.doc",
    mimeType: "application/msword",
    buffer: Buffer.from("legacy document"),
  });

  const alert = page.getByRole("alert");
  await expect(alert).toContainText("仅支持 PDF、DOCX、Markdown 或 TXT 文件；请复制文本后粘贴。");
  await expect(page.getByLabel("岗位 JD")).toHaveValue("Imported backend role");
  await expect(jdEditor.locator(".start-document-file")).toContainText("backend-role.pdf · 已导入");
  await expect(page.locator(".start-plan-question")).toHaveCount(5);
  await expect(page.locator(".start-revision-state")).toContainText(/已保存.*R1/);
  await expect(start).toBeEnabled();
  expect(mutations).toEqual(["generate", "generate"]);
  expect(observedTargets).toEqual(["job_description", "resume_text", "job_description"]);
  await expect(page.locator("body")).not.toContainText("unsupported_file_type");
  await expect(page.locator("body")).not.toContainText("private parser traceback");
  await expect(page.locator("body")).not.toContainText("secret-hash");
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
      const visibleDocuments = [...document.querySelectorAll(".start-document-canvas > .start-document-editor")]
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          return getComputedStyle(element).display !== "none" && rect.width > 0 && rect.height > 0;
        }).length;
      const controls = [...document.querySelectorAll(".start-app-shell button")]
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
  const action = page.getByRole("button", { name: /生成(?:并检查)?面试计划/ });
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
