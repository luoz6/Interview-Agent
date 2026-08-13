import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StartPage } from "./StartPage";

const familyId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const questionOne = "11111111-1111-4111-8111-111111111111";
const questionTwo = "22222222-2222-4222-8222-222222222222";
const defaultConfiguration = {
  difficulty: "intermediate",
  target_duration_minutes: 30,
  focus_preset: "balanced",
  question_type_budget: {
    project: 1,
    technical: 2,
    "system-design": 1,
    behavioral: 1,
  },
  expected_followup_budget: 5,
  max_followups_per_question: 2,
  generator_version: "plan-generator-v2",
  followup_policy_version: "fixed_v1",
};

function revisionResponse(revision = 1, overrides = {}) {
  const questions = overrides.questions || [
    {
      question_id: questionOne,
      position: 1,
      question_text:
        revision === 1
          ? "How do you prevent a cache stampede?"
          : "How do you safely prevent a cache stampede?",
      focus: "cache resilience",
      question_type: "technical",
      difficulty: "intermediate",
      expected_minutes: 6,
      expected_followups: 1,
      origin: revision === 1 ? "generated" : "edited",
      replaces_question_id: null,
      knowledge_binding: {
        schema_version: "plan-question-knowledge-binding-v1",
        status: "unbound",
        evidence_ids: [],
        reason_code: "no_grounded_evidence",
      },
    },
    {
      question_id: questionTwo,
      position: 2,
      question_text: "Design an idempotent payment workflow.",
      focus: "idempotency",
      question_type: "system-design",
      difficulty: "intermediate",
      expected_minutes: 8,
      expected_followups: 1,
      origin: "generated",
      replaces_question_id: null,
      knowledge_binding: {
        schema_version: "plan-question-knowledge-binding-v1",
        status: "unbound",
        evidence_ids: [],
        reason_code: "no_grounded_evidence",
      },
    },
  ];
  const legacyQuestions = questions.map((question) => ({
    id: question.question_id,
    prompt: question.question_text,
    focus: question.focus,
    kind: question.question_type,
  }));
  const legacy = {
    title: "Backend interview plan",
    questions: legacyQuestions,
    prep_context: {
      knowledge_status: "empty",
      topics: [],
      evidence_refs: [],
    },
  };
  return {
    ...legacy,
    legacy_plan: legacy,
    job_tags: ["Redis"],
    plan_family_id: familyId,
    plan_revision_id:
      revision === 1
        ? "33333333-3333-4333-8333-333333333333"
        : revision === 2
          ? "44444444-4444-4444-8444-444444444444"
          : "55555555-5555-4555-8555-555555555555",
    revision,
    plan_sha256: String(revision).repeat(64),
    plan: {
      schema_version: "interview-plan-v2",
      title: "Backend interview plan",
      configuration_snapshot: defaultConfiguration,
      questions,
    },
    ...overrides,
  };
}

function response(payload, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: async () => payload,
  });
}

async function generatePlan(user, fetchMock) {
  fetchMock.mockImplementationOnce(() => response(revisionResponse(1)));
  await user.type(screen.getByRole("textbox", { name: "岗位 JD" }), "Backend role");
  await user.click(screen.getByRole("tab", { name: /候选人经历/ }));
  await user.type(screen.getByRole("textbox", { name: "简历内容" }), "Backend resume");
  await user.click(screen.getByRole("button", { name: "生成面试计划" }));
  await screen.findByText("R1");
}

describe("StartPage editable plan workflow", () => {
  let fetchMock;

  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("keeps the live generate action retryable after a request failure", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await user.type(screen.getByRole("textbox", { name: "岗位 JD" }), "Backend role");
    await user.click(screen.getByRole("tab", { name: /候选人经历/ }));
    await user.type(screen.getByRole("textbox", { name: "简历内容" }), "Backend resume");

    fetchMock.mockImplementationOnce(() =>
      response({ detail: { code: "provider_timeout", message: "Timed out" } }, 503),
    );
    const generate = screen.getByRole("button", { name: "生成面试计划" });
    await user.click(generate);

    expect(await screen.findByText("Timed out")).toBeInTheDocument();
    expect(generate).toBeEnabled();

    fetchMock.mockImplementationOnce(() => response(revisionResponse(1)));
    await user.click(generate);

    await screen.findByText("R1");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("keeps start disabled for a local draft and adopts the successful server revision", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    const revisionState = screen.getByText("已保存").closest('[role="status"]');
    expect(revisionState).toHaveAttribute("aria-live", "polite");
    expect(revisionState).toHaveTextContent("R1");

    const start = screen.getByRole("button", { name: "开始本次面试" });
    expect(start).toBeEnabled();
    const questionInputs = screen.getAllByRole("textbox", { name: "问题内容" });
    await user.clear(questionInputs[0]);
    await user.type(questionInputs[0], "My safer cache stampede question");
    expect(start).toBeDisabled();
    expect(screen.getByText(/本地修改尚未全部保存/)).toBeInTheDocument();

    fetchMock.mockImplementationOnce(() => response(revisionResponse(2)));
    await user.click(screen.getAllByRole("button", { name: "保存修改" })[0]);

    await screen.findByText("R2");
    expect(start).toBeEnabled();
    const patch = fetchMock.mock.calls.at(-1);
    expect(patch[0]).toBe("/api/interview-plans/" + familyId);
    expect(patch[1].method).toBe("PATCH");
    const body = JSON.parse(patch[1].body);
    expect(body.expected_revision).toBe(1);
    expect(body.operations).toEqual([
      {
        op: "edit_question_text",
        question_id: questionOne,
        question_text: "My safer cache stampede question",
      },
    ]);
  });

  it("binds the readiness memory choice into the single start request", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    fetchMock.mockImplementationOnce(() => response({
      schema_version: "principal-memory-local-status-v1",
      mode: "local_consume",
      global_enabled: true,
      local_consumption_enabled: true,
      deletion_fence_active: false,
      consent: { granted: true, allowed_purposes: ["fact_storage", "local_consume"] },
    }));

    await user.click(screen.getByRole("tab", { name: "就绪" }));
    const memoryChoice = await screen.findByRole("checkbox", {
      name: "在本次面试中使用我的长期记忆",
    });
    expect(memoryChoice).toBeChecked();
    expect(screen.getByRole("link", { name: "查看和管理我的记忆" })).toHaveAttribute(
      "href",
      "/memory-center",
    );
    await user.click(memoryChoice);
    fetchMock.mockImplementationOnce(() =>
      response({ detail: { code: "provider_timeout", message: "start failed" } }, 503),
    );
    await user.click(screen.getByRole("button", { name: "开始本次面试" }));

    await screen.findByText("start failed");
    const [path, options] = fetchMock.mock.calls.at(-1);
    expect(path).toBe("/api/interviews");
    expect(JSON.parse(options.body).principal_memory_mode).toBe("ignore");
  });

  it("inherits the memory setting when readiness was not opened", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    fetchMock.mockImplementationOnce(() =>
      response({ detail: { code: "provider_timeout", message: "start failed" } }, 503),
    );

    await user.click(screen.getByRole("button", { name: "开始本次面试" }));

    await screen.findByText("start failed");
    const [path, options] = fetchMock.mock.calls.at(-1);
    expect(path).toBe("/api/interviews");
    expect(JSON.parse(options.body).principal_memory_mode).toBe("inherit");
  });

  it("preserves local input when save fails", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    const input = screen.getAllByRole("textbox", { name: "问题内容" })[0];
    await user.clear(input);
    await user.type(input, "Keep this local wording");

    fetchMock.mockImplementationOnce(() =>
      response({ detail: { code: "provider_timeout", message: "Timed out" } }, 503),
    );
    await user.click(screen.getAllByRole("button", { name: "保存修改" })[0]);

    await screen.findByText("计划操作失败");
    expect(input).toHaveValue("Keep this local wording");
    expect(screen.getByText(/本地输入没有丢失/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeDisabled();
  });

  it("shows conflict actions without overwriting local input", async () => {
    const user = userEvent.setup();
    const clipboardWrite = vi
      .spyOn(navigator.clipboard, "writeText")
      .mockResolvedValue(undefined);
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    const input = screen.getAllByRole("textbox", { name: "问题内容" })[0];
    await user.clear(input);
    await user.type(input, "My conflicting question");

    fetchMock.mockImplementationOnce(() =>
      response(
        {
          code: "plan_revision_conflict",
          current_revision: {
            plan_revision_id: "44444444-4444-4444-8444-444444444444",
            revision: 2,
            plan_sha256: "2".repeat(64),
          },
        },
        409,
      ),
    );
    await user.click(screen.getAllByRole("button", { name: "保存修改" })[0]);

    const conflictAlert = await screen.findByRole("alert");
    expect(conflictAlert).toHaveTextContent("计划版本冲突");
    expect(screen.getByText("版本冲突").closest('[role="status"]')).toHaveAttribute("aria-live", "polite");
    expect(input).toHaveValue("My conflicting question");
    expect(screen.getByRole("button", { name: "查看服务端版本" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "复制我的内容" }));
    expect(clipboardWrite).toHaveBeenCalledWith(
      expect.stringContaining("My conflicting question"),
    );
  });

  it("uses keyboard-operable move controls and sends the requested position", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    fetchMock.mockImplementationOnce(() =>
      response(
        revisionResponse(2, {
          questions: [
            revisionResponse(1).plan.questions[1],
            revisionResponse(1).plan.questions[0],
          ].map((question, index) => ({ ...question, position: index + 1 })),
        }),
      ),
    );

    const moveDown = screen.getByRole("button", { name: "将第 1 题下移" });
    moveDown.focus();
    await user.keyboard("{Enter}");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const body = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(body.operations[0]).toEqual({
      op: "move_question",
      question_id: questionOne,
      to_position: 2,
    });
  });

  it("requires explicit confirmation before delete", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);

    await user.click(screen.getAllByRole("button", { name: "删除" })[0]);
    const dialog = screen.getByRole("dialog", { name: "删除第 1 题？" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await user.click(screen.getAllByRole("button", { name: "删除" })[0]);
    fetchMock.mockImplementationOnce(() =>
      response(
        revisionResponse(2, {
          questions: [
            { ...revisionResponse(1).plan.questions[1], position: 1 },
          ],
        }),
      ),
    );
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "删除并保存",
      }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).operations[0].op).toBe(
      "delete_question",
    );
  });

  it("loads history and restores only after confirmation", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    fetchMock.mockImplementationOnce(() =>
      response({
        plan_family_id: familyId,
        latest_revision: 2,
        revisions: [
          {
            plan_revision_id: "44444444-4444-4444-8444-444444444444",
            revision: 2,
            title: "Backend interview plan",
            question_count: 2,
            created_reason: "edit_question_text",
            is_latest: true,
          },
          {
            plan_revision_id: "33333333-3333-4333-8333-333333333333",
            revision: 1,
            title: "Backend interview plan",
            question_count: 2,
            created_reason: "initial_generation",
            is_latest: false,
          },
        ],
      }),
    );
    await user.click(screen.getByRole("button", { name: "历史版本" }));
    await screen.findByText(/initial_generation/);

    await user.click(screen.getByRole("button", { name: "恢复" }));
    expect(screen.getByRole("dialog", { name: "恢复到 R1？" })).toBeInTheDocument();
    fetchMock.mockImplementationOnce(() => response(revisionResponse(3)));
    await user.click(screen.getByRole("button", { name: "确认恢复" }));

    await screen.findByText("R3");
    const body = JSON.parse(fetchMock.mock.calls[2][1].body);
    expect(body.operations[0]).toEqual({
      op: "restore_revision",
      target_revision_id: "33333333-3333-4333-8333-333333333333",
    });
  });

  it("adds a custom question with safe ungrounded defaults", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    await user.click(screen.getByRole("button", { name: "添加题目" }));
    const customForm = screen
      .getByRole("button", { name: "添加并保存" })
      .closest("form");
    await user.type(
      within(customForm).getByRole("textbox", { name: "问题内容" }),
      "Describe a production incident review.",
    );
    await user.type(
      within(customForm).getByRole("textbox", { name: "考察重点" }),
      "incident learning",
    );
    const custom = {
      question_id: "66666666-6666-4666-8666-666666666666",
      position: 3,
      question_text: "Describe a production incident review.",
      focus: "incident learning",
      question_type: "technical",
      difficulty: "intermediate",
      expected_minutes: 6,
      expected_followups: 0,
      origin: "custom",
      replaces_question_id: null,
      knowledge_binding: {
        schema_version: "plan-question-knowledge-binding-v1",
        status: "unbound",
        evidence_ids: [],
        reason_code: "custom_question",
      },
    };
    fetchMock.mockImplementationOnce(() =>
      response(
        revisionResponse(2, {
          questions: [...revisionResponse(1).plan.questions, custom],
        }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "添加并保存" }));

    await screen.findByText("自定义题");
    const body = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(body.operations[0]).toEqual({
      op: "add_custom_question",
      question_text: "Describe a production incident review.",
      focus: "incident learning",
      question_type: "technical",
      difficulty: "intermediate",
      expected_minutes: 6,
      expected_followups: 0,
    });
    expect(body.operations[0]).not.toHaveProperty("knowledge_binding");
  });

  it("regenerates one question through the server provider endpoint", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    const replacement = {
      ...revisionResponse(1).plan.questions[0],
      question_id: "77777777-7777-4777-8777-777777777777",
      question_text: "Explain a resilient cache refresh strategy.",
      origin: "regenerated",
      replaces_question_id: questionOne,
    };
    fetchMock.mockImplementationOnce(() =>
      response(
        revisionResponse(2, {
          questions: [replacement, revisionResponse(1).plan.questions[1]],
        }),
      ),
    );

    await user.click(screen.getAllByRole("button", { name: "换题" })[0]);

    await screen.findByDisplayValue("Explain a resilient cache refresh strategy.");
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/interview-plans/" +
        familyId +
        "/questions/" +
        questionOne +
        "/regenerate",
    );
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).expected_revision).toBe(1);
  });

  it("confirms before replacing a question that has unsaved local text", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    const input = screen.getAllByRole("textbox", { name: "问题内容" })[0];
    await user.clear(input);
    await user.type(input, "Unsaved wording");

    await user.click(screen.getAllByRole("button", { name: "换题" })[0]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const dialog = screen.getByRole("dialog", {
      name: "放弃本地修改并替换这道题？",
    });
    expect(input).toHaveValue("Unsaved wording");
    await user.keyboard("{Escape}");
    expect(dialog).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not regenerate the whole plan until the confirmation is accepted", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    await user.click(screen.getByRole("button", { name: "全部换题" }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const dialog = screen.getByRole("dialog", { name: "重新生成整份计划？" });
    fetchMock.mockImplementationOnce(() => response(revisionResponse(2)));
    await user.click(
      within(dialog).getByRole("button", { name: "确认重新生成" }),
    );

    await screen.findByText("R2");
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/interview-plans/" + familyId + "/regenerate",
    );
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).confirmed).toBe(true);
  });

  it("turns a start-time 409 into a visible conflict and disables start", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    fetchMock.mockImplementationOnce(() =>
      response({ detail: "plan revision conflict" }, 409),
    );

    await user.click(screen.getByRole("button", { name: "开始本次面试" }));

    await screen.findByText("计划版本冲突");
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeDisabled();
    expect(screen.getByText(/启动前服务端 revision 已变化/)).toBeInTheDocument();
  });

  it("preserves a local question draft while refreshing after failure", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);
    const input = screen.getAllByRole("textbox", { name: "问题内容" })[0];
    await user.clear(input);
    await user.type(input, "Keep this draft across refresh");
    fetchMock.mockImplementationOnce(() =>
      response({ detail: "temporary failure" }, 503),
    );
    await user.click(screen.getAllByRole("button", { name: "保存修改" })[0]);
    await screen.findByText("计划操作失败");

    fetchMock
      .mockImplementationOnce(() =>
        response({
          plan_family_id: familyId,
          latest_revision: 2,
          revisions: [
            {
              plan_revision_id: "44444444-4444-4444-8444-444444444444",
              revision: 2,
              title: "Backend interview plan",
              question_count: 2,
              created_reason: "edit_question_text",
              is_latest: true,
            },
          ],
        }),
      )
      .mockImplementationOnce(() => response(revisionResponse(2)));

    await user.click(
      screen.getByRole("button", { name: "重新载入服务端版本" }),
    );

    await screen.findByText("R2");
    expect(screen.getAllByRole("textbox", { name: "问题内容" })[0]).toHaveValue(
      "Keep this draft across refresh",
    );
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeDisabled();
  });

  it("sends a complete safe configuration for initial generation", async () => {
    const user = userEvent.setup();
    const requestedConfiguration = {
      ...defaultConfiguration,
      difficulty: "advanced",
      target_duration_minutes: 45,
      focus_preset: "system_design",
      question_type_budget: {
        project: 1,
        technical: 2,
        "system-design": 3,
        behavioral: 1,
      },
      expected_followup_budget: 7,
    };
    const baseResponse = revisionResponse(1);
    fetchMock.mockImplementationOnce(() =>
      response(
        revisionResponse(1, {
          plan: {
            ...baseResponse.plan,
            configuration_snapshot: requestedConfiguration,
          },
        }),
      ),
    );
    render(<StartPage />);

    await user.click(screen.getByRole("radio", { name: /高级/ }));
    await user.click(screen.getByRole("radio", { name: "45 分钟" }));
    await user.click(screen.getByRole("radio", { name: /系统设计 架构/ }));
    await user.click(screen.getByRole("radio", { name: /架构优先/ }));
    expect(screen.getByText("7 道")).toBeInTheDocument();
    expect(screen.getByText(/实际进度取决于回答长度、追问和操作节奏/)).toBeInTheDocument();

    await user.type(screen.getByRole("textbox", { name: "岗位 JD" }), "Platform role");
    await user.click(screen.getByRole("tab", { name: /候选人经历/ }));
    await user.type(screen.getByRole("textbox", { name: "简历内容" }), "Platform resume");
    await user.click(screen.getByRole("button", { name: "生成面试计划" }));
    await screen.findByText("R1");

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.configuration).toEqual(requestedConfiguration);
    expect(body.configuration).not.toHaveProperty("question_mix_preset");
    expect(body.configuration.max_followups_per_question).toBe(2);
  });

  it("marks the current revision stale until configured regeneration succeeds", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    await generatePlan(user, fetchMock);

    await user.click(screen.getByRole("radio", { name: /高级/ }));
    expect(screen.getByText("待重新生成")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeDisabled();

    await user.click(screen.getByRole("radio", { name: /中级/ }));
    expect(screen.getByText("配置已同步")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeEnabled();
    await user.click(screen.getByRole("radio", { name: /高级/ }));

    await user.click(screen.getByRole("button", { name: "应用配置并重新生成" }));
    const dialog = screen.getByRole("dialog", {
      name: "使用新配置重新生成计划？",
    });
    expect(within(dialog).getByText(/新 revision 将采用：难度/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    const configured = { ...defaultConfiguration, difficulty: "advanced" };
    const baseResponse = revisionResponse(2);
    fetchMock.mockImplementationOnce(() =>
      response(
        revisionResponse(2, {
          plan: {
            ...baseResponse.plan,
            configuration_snapshot: configured,
          },
        }),
      ),
    );
    await user.click(
      within(dialog).getByRole("button", { name: "确认采用新配置" }),
    );

    await screen.findByText("R2");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "开始本次面试" })).toBeEnabled(),
    );
    const requestBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(requestBody.confirmed).toBe(true);
    expect(requestBody.configuration).toEqual(configured);
  });

  it("automatically restores the saved revision and its configuration after refresh", async () => {
    const user = userEvent.setup();
    const configured = {
      ...defaultConfiguration,
      difficulty: "advanced",
      target_duration_minutes: 60,
      focus_preset: "project_review",
      question_type_budget: {
        project: 4,
        technical: 2,
        "system-design": 1,
        behavioral: 2,
      },
      expected_followup_budget: 9,
    };
    const baseResponse = revisionResponse(1);
    window.localStorage.setItem("interview-agent:draft-id", "draft_refresh");
    window.localStorage.setItem(
      "interview-agent:plan-configuration-v1",
      JSON.stringify(configured),
    );
    fetchMock
      .mockImplementationOnce(() =>
        response({
          draft_id: "draft_refresh",
          job_description: "Restored platform role",
          resume_text: "Restored platform resume",
          job_tags: ["Platform"],
          plan_status: "active",
          plan_family_id: familyId,
          latest_plan_revision_id: baseResponse.plan_revision_id,
        }),
      )
      .mockImplementationOnce(() =>
        response(
          revisionResponse(1, {
            plan: {
              ...baseResponse.plan,
              configuration_snapshot: configured,
            },
          }),
        ),
      );

    render(<StartPage />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole("tab", { name: "计划" }));
    await screen.findByText("R1");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/interview-drafts/draft_refresh");
    expect(screen.getByRole("radio", { name: /高级/ })).toBeChecked();
    expect(screen.getByRole("radio", { name: "60 分钟" })).toBeChecked();
    expect(screen.getByRole("button", { name: "开始本次面试" })).toBeEnabled();
    expect(screen.getByText("配置已同步")).toBeInTheDocument();
  });

  it("confirms before a manual draft restore can replace the current canvas", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    const jdInput = screen.getByRole("textbox", { name: "岗位 JD" });
    await user.type(jdInput, "New canvas content");
    window.localStorage.setItem("interview-agent:draft-id", "draft-t58");
    const restoreButton = screen.getByRole("button", { name: "恢复草稿" });
    await user.click(restoreButton);

    const dialog = screen.getByRole("dialog", { name: "用已保存草稿替换当前画布？" });
    expect(dialog).toHaveTextContent("当前画布中的岗位 JD 和候选人经历会被替换");
    expect(dialog).toHaveTextContent("已保存的匿名草稿不会被删除");
    expect(fetchMock.mock.calls.every(([path]) => path === "/api/runtime/principal-memory/status")).toBe(true);

    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(jdInput).toHaveValue("New canvas content");
    expect(
      fetchMock.mock.calls.every(
        ([path]) => path === "/api/runtime/principal-memory/status",
      ),
    ).toBe(true);
    await waitFor(() => expect(restoreButton).toHaveFocus());

    fetchMock.mockImplementationOnce(() => response({
      draft_id: "draft-t58",
      job_description: "Restored saved JD",
      resume_text: "Restored saved resume",
      job_tags: [],
      plan_status: "stale",
      plan_family_id: null,
      latest_plan_revision_id: null,
    }));
    await user.click(restoreButton);
    await user.click(screen.getByRole("button", { name: "确认恢复草稿" }));

    await waitFor(() => expect(jdInput).toHaveValue("Restored saved JD"));
    expect(fetchMock.mock.calls[0][0]).toBe("/api/interview-drafts/draft-t58");
  });

  it("uses a dialog before clearing the canvas and keeps saved drafts recoverable", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    const jdInput = screen.getByRole("textbox", { name: "岗位 JD" });
    await user.type(jdInput, "Do not clear without confirmation");
    const clearButton = screen.getByRole("button", { name: "清空当前画布" });

    await user.click(clearButton);
    const dialog = screen.getByRole("dialog", { name: "清空当前画布？" });
    expect(dialog).toHaveTextContent("只清空当前画布");
    expect(dialog).toHaveTextContent("已保存的匿名草稿不会被删除，之后仍可恢复");
    await user.keyboard("{Escape}");
    expect(jdInput).toHaveValue("Do not clear without confirmation");
    await waitFor(() => expect(clearButton).toHaveFocus());

    await user.click(clearButton);
    await user.click(screen.getByRole("button", { name: "确认清空画布" }));
    expect(jdInput).toHaveValue("");
    expect(screen.getByText("当前画布已清空；此前保存的匿名草稿仍可恢复。")).toBeInTheDocument();
    expect(
      fetchMock.mock.calls.every(
        ([path]) => path === "/api/runtime/principal-memory/status",
      ),
    ).toBe(true);
  });

  it("keeps the current canvas and draft link when a confirmed restore fails mid-read", async () => {
    const user = userEvent.setup();
    render(<StartPage />);
    const jdInput = screen.getByRole("textbox", { name: "岗位 JD" });
    await user.type(jdInput, "Keep this current canvas");
    window.localStorage.setItem("interview-agent:draft-id", "draft-atomic");
    fetchMock
      .mockImplementationOnce(() => response({
        draft_id: "draft-atomic",
        job_description: "Do not apply this partial draft",
        resume_text: "Do not apply this partial resume",
        job_tags: ["Platform"],
        plan_status: "active",
        plan_family_id: familyId,
        latest_plan_revision_id: "33333333-3333-4333-8333-333333333333",
      }))
      .mockImplementationOnce(() => response({ detail: "revision temporarily unavailable" }, 500));

    await user.click(screen.getByRole("button", { name: "恢复草稿" }));
    await user.click(screen.getByRole("button", { name: "确认恢复草稿" }));

    expect(await screen.findByText("草稿恢复失败：revision temporarily unavailable")).toBeInTheDocument();
    expect(jdInput).toHaveValue("Keep this current canvas");
    expect(window.localStorage.getItem("interview-agent:draft-id")).toBe("draft-atomic");
  });
});
