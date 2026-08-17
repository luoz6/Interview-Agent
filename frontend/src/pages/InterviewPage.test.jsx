import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getJson, postJson, readSse } from "../api/client";
import { interviewTurnStates } from "../interviewTurnState";
import { InterviewPage, InterviewTurnStatus, QuestionNavigator } from "./InterviewPage";

vi.mock("../api/client", () => ({
  apiUrl: (path) => path,
  getJson: vi.fn(),
  HttpError: class HttpError extends Error {
    constructor(message, { status = 0, body = {} } = {}) {
      super(message);
      this.status = status;
      this.body = body;
    }
  },
  postJson: vi.fn(),
  postSse: vi.fn(),
  readSse: vi.fn(),
}));

vi.mock("../hooks/useSessionId", () => ({
  useSessionId: () => "session-t58",
}));

const activeSnapshot = {
  session_id: "session-t58",
  status: "active",
  phase: "interview",
  state_version: 8,
  total_questions: 4,
  completed_questions: 2,
  answered_questions: 1,
  skipped_questions: 1,
  unanswered_questions: 2,
  followup_policy_version: "fixed_v1",
  current_followup_count: 0,
  current_question: {
    id: "q3",
    prompt: "如何设计幂等写入？",
    focus: "幂等与故障恢复",
    kind: "system-design",
  },
  questions: [
    { id: "q1", prompt: "说明缓存策略", state: "answered" },
    { id: "q2", prompt: "说明降级策略", state: "skipped" },
    { id: "q3", prompt: "如何设计幂等写入？", state: "current" },
    { id: "q4", prompt: "如何验证恢复流程？", state: "pending" },
  ],
  messages: [],
  elapsed_seconds: 180,
  estimated_remaining_seconds: 720,
};

beforeEach(() => {
  window.localStorage.clear();
  vi.clearAllMocks();
  readSse.mockReset();
  getJson.mockImplementation((path) => path.endsWith("/question-evaluations")
    ? Promise.resolve({ items: [] })
    : Promise.resolve(activeSnapshot));
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: vi.fn(),
  });
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    matches: true,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("InterviewPage recovery and browser storage", () => {
  it.each([
    ["initial error", [{ type: "error", data: { code: "workflow_failed" } }]],
    ["initial conflict", [{ type: "conflict", data: { code: "state_version_conflict" } }]],
    ["error after reconnect", [
      { type: "reconnect", data: { last_event_id: "gen-1:1:2" } },
      { type: "error", data: { code: "workflow_failed" } },
    ]],
    ["conflict after reconnect", [
      { type: "reconnect", data: { last_event_id: "gen-1:1:2" } },
      { type: "conflict", data: { code: "state_version_conflict" } },
    ]],
  ])("refreshes the authoritative snapshot after an %s terminal", async (_label, terminals) => {
    const user = userEvent.setup();
    const recoverySnapshot = {
      ...activeSnapshot,
      active_command_id: "command-recovery",
      active_stream_url: "/api/interviews/session-t58/commands/command-recovery/stream",
    };
    const convergedSnapshot = {
      ...activeSnapshot,
      state_version: 9,
      completed_questions: 3,
      answered_questions: 2,
      unanswered_questions: 1,
      current_question: {
        id: "q4",
        prompt: "如何验证恢复流程？",
        focus: "故障恢复验证",
        kind: "reliability",
      },
      questions: [
        ...activeSnapshot.questions.slice(0, 2),
        { id: "q3", prompt: "如何设计幂等写入？", state: "answered" },
        { id: "q4", prompt: "如何验证恢复流程？", state: "current" },
      ],
    };
    const snapshots = [recoverySnapshot, convergedSnapshot];
    let snapshotIndex = 0;
    getJson.mockImplementation((path) => path.endsWith("/question-evaluations")
      ? Promise.resolve({ items: [] })
      : Promise.resolve(snapshots[Math.min(snapshotIndex++, snapshots.length - 1)]));
    terminals.forEach((terminal) => readSse.mockResolvedValueOnce(terminal));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));
    postJson.mockReturnValue(new Promise(() => {}));

    render(<InterviewPage />);

    await screen.findByRole("heading", { name: "如何验证恢复流程？" });
    await waitFor(() => expect(document.body.dataset.interviewState).toBe("active"));
    const snapshotCalls = getJson.mock.calls.filter(([path]) => path === "/api/interviews/session-t58");
    expect(snapshotCalls).toHaveLength(2);
    expect(readSse).toHaveBeenCalledTimes(terminals.length);
    expect(screen.getByText(/流式回答恢复失败/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "跳过此题" }));
    await user.click(screen.getByRole("button", { name: "确认跳过此题" }));
    expect(postJson).toHaveBeenCalledWith(
      "/api/interviews/session-t58/skip",
      expect.objectContaining({ expected_version: 9 }),
    );
  });

  it("keeps commands disabled when the recovery snapshot refresh fails", async () => {
    const recoverySnapshot = {
      ...activeSnapshot,
      active_command_id: "command-recovery",
      active_stream_url: "/api/interviews/session-t58/commands/command-recovery/stream",
    };
    let snapshotCalls = 0;
    getJson.mockImplementation((path) => {
      if (path.endsWith("/question-evaluations")) return Promise.resolve({ items: [] });
      snapshotCalls += 1;
      return snapshotCalls === 1
        ? Promise.resolve(recoverySnapshot)
        : Promise.reject(new Error("snapshot unavailable"));
    });
    readSse.mockResolvedValue({ type: "conflict", data: { code: "state_version_conflict" } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));

    render(<InterviewPage />);

    await waitFor(() => expect(document.body.dataset.interviewState).toBe("error"));
    expect(snapshotCalls).toBe(2);
    expect(readSse).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("textbox", { name: "你的回答" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交回答" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "跳过此题" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "结束面试" })).toBeDisabled();
    expect(screen.getByText(/无法加载最新会话状态：snapshot unavailable/)).toBeInTheDocument();
  });

  it("stays disabled while a same-command recovery snapshot waits for auxiliary data", async () => {
    const recoverySnapshot = {
      ...activeSnapshot,
      active_command_id: "command-recovery",
      active_stream_url: "/api/interviews/session-t58/commands/command-recovery/stream",
    };
    let snapshotCalls = 0;
    let evaluationCalls = 0;
    let resolveRecoveryEvaluations;
    getJson.mockImplementation((path) => {
      if (path.endsWith("/question-evaluations")) {
        evaluationCalls += 1;
        if (evaluationCalls === 1) return Promise.resolve({ items: [] });
        return new Promise((resolve) => { resolveRecoveryEvaluations = resolve; });
      }
      snapshotCalls += 1;
      return Promise.resolve(recoverySnapshot);
    });
    readSse.mockResolvedValue({ type: "conflict", data: { code: "state_version_conflict" } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));

    render(<InterviewPage />);

    await waitFor(() => expect(resolveRecoveryEvaluations).toEqual(expect.any(Function)));
    await waitFor(() => expect(document.body.dataset.interviewState).toBe("error"));
    expect(screen.getByRole("textbox", { name: "你的回答" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "提交回答" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "跳过此题" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "结束面试" })).toBeDisabled();

    await act(async () => { resolveRecoveryEvaluations({ items: [] }); });

    await screen.findByText(/最新会话仍在处理中，请稍后刷新页面/);
    expect(document.body.dataset.interviewState).toBe("error");
    expect(snapshotCalls).toBe(2);
    expect(readSse).toHaveBeenCalledTimes(1);
    expect(postJson).not.toHaveBeenCalled();
  });

  it("aborts a recovery refresh and does not navigate after unmount", async () => {
    const recoverySnapshot = {
      ...activeSnapshot,
      active_command_id: "command-recovery",
      active_stream_url: "/api/interviews/session-t58/commands/command-recovery/stream",
    };
    const finishedSnapshot = {
      ...activeSnapshot,
      status: "finished",
      state_version: 9,
    };
    const navigateToReportProcessing = vi.fn();
    let snapshotCalls = 0;
    let evaluationCalls = 0;
    let recoverySignal;
    let resolveRecoveryEvaluations;
    getJson.mockImplementation((path, options = {}) => {
      if (path.endsWith("/question-evaluations")) {
        evaluationCalls += 1;
        if (evaluationCalls === 1) return Promise.resolve({ items: [] });
        recoverySignal = options.signal;
        return new Promise((resolve) => { resolveRecoveryEvaluations = resolve; });
      }
      snapshotCalls += 1;
      return Promise.resolve(snapshotCalls === 1 ? recoverySnapshot : finishedSnapshot);
    });
    readSse.mockResolvedValue({ type: "conflict", data: { code: "state_version_conflict" } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));

    const { unmount } = render(
      <InterviewPage navigateToReportProcessing={navigateToReportProcessing} />,
    );
    await waitFor(() => expect(resolveRecoveryEvaluations).toEqual(expect.any(Function)));
    expect(recoverySignal).toBeInstanceOf(AbortSignal);
    expect(recoverySignal.aborted).toBe(false);

    unmount();
    expect(recoverySignal.aborted).toBe(true);
    await act(async () => { resolveRecoveryEvaluations({ items: [] }); });

    expect(navigateToReportProcessing).not.toHaveBeenCalled();
    expect(document.body.dataset.interviewState).toBe("error");
    expect(snapshotCalls).toBe(2);
    expect(evaluationCalls).toBe(2);
    expect(readSse).toHaveBeenCalledTimes(1);
  });

  it("keeps the interview usable when localStorage reads throw SecurityError", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("storage denied", "SecurityError");
    });
    getJson.mockImplementation((path) => path.endsWith("/question-evaluations")
      ? Promise.resolve({ items: [] })
      : Promise.resolve({
        ...activeSnapshot,
        assistance_mode: "basic",
        policy_version: "memory-v1",
        user_notice_required: true,
      }));

    render(<InterviewPage />);

    await waitFor(() => expect(document.body.dataset.interviewState).toBe("active"));
    expect(document.querySelector("textarea")).toBeEnabled();
  });

  it("keeps an in-memory answer when localStorage writes exceed quota", async () => {
    const user = userEvent.setup();
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota exceeded", "QuotaExceededError");
    });
    render(<InterviewPage />);

    const answer = await waitFor(() => {
      const element = document.querySelector("textarea");
      expect(element).toBeEnabled();
      return element;
    });
    await user.type(answer, "local-only answer");

    expect(answer).toHaveValue("local-only answer");
    expect(document.body.dataset.interviewState).toBe("active");
  });
});

describe("InterviewPage safe citation indicator", () => {
  it("shows the indicator only for an actual available user-document citation", async () => {
    getJson.mockImplementation((path) => path.endsWith("/question-evaluations")
      ? Promise.resolve({
        items: [{
          question_id: "q1",
          status: "completed",
          feedback: {
            knowledge_citations: [{
              citation_id: "citation-user-private",
              source_scope: "user_document",
              document_safe_ref: "material-safe-ref-private",
              display_title: "我的内部复盘",
              location_label: "第 2 页",
              excerpt: "不应在面试提示中展开的摘录",
              usage: "feedback",
              availability: "available",
              chunk_id: "chunk-private",
              owner: "principal-private",
            }],
          },
        }],
      })
      : Promise.resolve(activeSnapshot));

    render(<InterviewPage />);

    expect(await screen.findByText("参考了你的资料")).toBeInTheDocument();
    const ordinaryDom = document.body.innerHTML;
    [
      "citation-user-private",
      "material-safe-ref-private",
      "我的内部复盘",
      "第 2 页",
      "不应在面试提示中展开的摘录",
      "chunk-private",
      "principal-private",
    ].forEach((value) => expect(ordinaryDom).not.toContain(value));
  });

  it("does not infer a reference from selected materials or scope", async () => {
    getJson.mockImplementation((path) => path.endsWith("/question-evaluations")
      ? Promise.resolve({ items: [] })
      : Promise.resolve({
        ...activeSnapshot,
        knowledge_scope: {
          document_ids: ["selected-document-not-referenced"],
          frozen: true,
        },
        selected_materials: [{
          document_safe_ref: "selected-material-safe-ref",
          display_title: "仅被选择的资料",
        }],
      }));

    render(<InterviewPage />);

    await screen.findByRole("heading", { name: "如何设计幂等写入？" });
    expect(screen.queryByText("参考了你的资料")).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain("selected-document-not-referenced");
    expect(document.body.innerHTML).not.toContain("selected-material-safe-ref");
    expect(document.body.innerHTML).not.toContain("仅被选择的资料");
  });

  it("fails closed for system-only, deleted, and malformed citations", async () => {
    getJson.mockImplementation((path) => path.endsWith("/question-evaluations")
      ? Promise.resolve({
        items: [{
          question_id: "q1",
          status: "completed",
          feedback: {
            knowledge_citations: [
              {
                citation_id: "citation-system-only",
                source_scope: "system_knowledge",
                document_safe_ref: null,
                display_title: "系统知识标题",
                usage: "feedback",
                availability: "available",
              },
              {
                citation_id: "citation-deleted-private",
                source_scope: "user_document",
                document_safe_ref: "deleted-material-private",
                display_title: "已删除资料原标题",
                location_label: "已删除位置",
                excerpt: "已删除摘录",
                usage: "feedback",
                availability: "deleted",
              },
              {
                source_scope: "user_document",
                document_safe_ref: "malformed-material-private",
                display_title: "缺少 citation_id",
                usage: "feedback",
                availability: "available",
              },
            ],
          },
        }],
      })
      : Promise.resolve(activeSnapshot));

    render(<InterviewPage />);

    await screen.findByRole("heading", { name: "如何设计幂等写入？" });
    expect(screen.queryByText("参考了你的资料")).not.toBeInTheDocument();
    const ordinaryDom = document.body.innerHTML;
    [
      "citation-system-only",
      "系统知识标题",
      "citation-deleted-private",
      "deleted-material-private",
      "已删除资料原标题",
      "已删除位置",
      "已删除摘录",
      "malformed-material-private",
      "缺少 citation_id",
    ].forEach((value) => expect(ordinaryDom).not.toContain(value));
  });
});


describe("InterviewTurnStatus", () => {
  it.each([
    [interviewTurnStates.decisionPending, "正在分析这次回答"],
    [interviewTurnStates.generationPending, "正在组织追问"],
    [interviewTurnStates.generationStreaming, "追问生成中"],
    [interviewTurnStates.nextQuestion, "回答已记录，进入下一题"],
    [interviewTurnStates.degraded, "本题将继续到下一题"],
    [interviewTurnStates.recovery, "正在恢复上一条追问"],
  ])("renders the public %s state without internal reasoning", (state, label) => {
    render(<InterviewTurnStatus state={state} followupCount={1} />);

    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveAttribute("aria-atomic", "true");
    expect(status).toHaveAttribute("data-turn-state", state);
    expect(status).toHaveTextContent(label);
    expect(status).toHaveTextContent("当前主问题 · 追问 1 / 2");
    expect(status).not.toHaveTextContent(/gap|confidence|reason|chain.of.thought/i);
  });

  it("keeps one mounted live region while idle", () => {
    const { rerender } = render(
      <InterviewTurnStatus state={interviewTurnStates.idle} followupCount={0} />,
    );

    const status = screen.getByRole("status");
    expect(status).toBeEmptyDOMElement();
    rerender(
      <InterviewTurnStatus state={interviewTurnStates.recovery} followupCount={1} />,
    );
    expect(status).toHaveTextContent("正在恢复上一条追问");
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });
});

describe("InterviewPage destructive command confirmations", () => {
  it("shows outcome counts and sends finish only after explicit confirmation", async () => {
    const user = userEvent.setup();
    postJson.mockReturnValue(new Promise(() => {}));
    render(<InterviewPage />);

    await screen.findByRole("heading", { name: "如何设计幂等写入？" });
    const finishButton = screen.getByRole("button", { name: "结束面试" });
    await user.click(finishButton);

    const dialog = screen.getByRole("dialog", { name: "结束面试并生成报告？" });
    expect(dialog).toHaveTextContent("已回答 1 道");
    expect(dialog).toHaveTextContent("已跳过 1 道");
    expect(dialog).toHaveTextContent("仍未完成 2 道");
    expect(dialog).toHaveTextContent("不会产生对应题目的能力分，并会降低报告覆盖");
    expect(postJson).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(postJson).not.toHaveBeenCalled();
    await waitFor(() => expect(finishButton).toHaveFocus());

    await user.click(finishButton);
    await user.click(screen.getByRole("button", { name: "确认结束面试" }));
    expect(postJson).toHaveBeenCalledTimes(1);
    expect(postJson.mock.calls[0][0]).toBe("/api/interviews/session-t58/finish");
  });

  it("explains skip scoring and coverage before sending the command", async () => {
    const user = userEvent.setup();
    postJson.mockReturnValue(new Promise(() => {}));
    render(<InterviewPage />);

    await screen.findByRole("heading", { name: "如何设计幂等写入？" });
    const skipButton = screen.getByRole("button", { name: "跳过此题" });
    await user.click(skipButton);

    const dialog = screen.getByRole("dialog", { name: "跳过当前题？" });
    expect(dialog).toHaveTextContent("不产生该题能力分并降低报告覆盖");
    expect(dialog).toHaveTextContent("而不是已回答或评分为 0 分");
    expect(postJson).not.toHaveBeenCalled();

    await user.keyboard("{Escape}");
    expect(postJson).not.toHaveBeenCalled();
    await waitFor(() => expect(skipButton).toHaveFocus());

    await user.click(skipButton);
    await user.click(screen.getByRole("button", { name: "确认跳过此题" }));
    expect(postJson).toHaveBeenCalledTimes(1);
    expect(postJson.mock.calls[0][0]).toBe("/api/interviews/session-t58/skip");
  });

  it("traps modal focus, closes with Escape, and returns to the trigger", async () => {
    const user = userEvent.setup();
    render(<InterviewPage />);

    await screen.findByRole("heading", { name: "如何设计幂等写入？" });
    const finishButton = screen.getByRole("button", { name: "结束面试" });
    await user.click(finishButton);
    const dialog = screen.getByRole("dialog", { name: "结束面试并生成报告？" });
    const cancel = within(dialog).getByRole("button", { name: "取消" });
    const confirm = within(dialog).getByRole("button", { name: "确认结束面试" });
    expect(cancel).toHaveFocus();

    await user.tab();
    expect(confirm).toHaveFocus();
    await user.tab();
    expect(cancel).toHaveFocus();
    await user.tab({ shift: true });
    expect(confirm).toHaveFocus();

    finishButton.focus();
    expect(cancel).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(dialog).not.toBeInTheDocument();
    await waitFor(() => expect(finishButton).toHaveFocus());
    expect(postJson).not.toHaveBeenCalled();
  });

  it("returns focus to the focus-mode trigger when Escape exits", async () => {
    const user = userEvent.setup();
    render(<InterviewPage />);

    const focusButton = await screen.findByRole("button", { name: "专注模式" });
    await user.click(focusButton);
    expect(screen.getByRole("button", { name: "退出专注" })).toHaveAttribute("aria-pressed", "true");

    await user.keyboard("{Escape}");
    const restoredButton = screen.getByRole("button", { name: "专注模式" });
    await waitFor(() => expect(restoredButton).toHaveFocus());
  });
});

describe("QuestionNavigator policy copy", () => {
  it("uses fixed cadence copy by default and dynamic copy only for adaptive_v1", () => {
    const { rerender } = render(<QuestionNavigator snapshot={{
      total_questions: 1,
      completed_questions: 0,
      followup_policy_version: "fixed_v1",
      questions: [],
    }} />);

    expect(screen.getByText("固定节奏")).toBeInTheDocument();
    expect(screen.getByText("每道主问题按固定追问策略推进，回答不会切换为动态决策路径。")).toBeInTheDocument();
    expect(screen.queryByText("动态路径")).not.toBeInTheDocument();

    rerender(<QuestionNavigator snapshot={{
      total_questions: 1,
      completed_questions: 0,
      followup_policy_version: "adaptive_v1",
      questions: [],
    }} />);
    expect(screen.getByText("动态路径")).toBeInTheDocument();
    expect(screen.getByText("回答会决定追问或进入下一题。")).toBeInTheDocument();
  });
});
