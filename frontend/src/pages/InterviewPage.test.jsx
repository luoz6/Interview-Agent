import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getJson, postJson } from "../api/client";
import { interviewTurnStates } from "../interviewTurnState";
import { InterviewPage, InterviewTurnStatus, QuestionNavigator } from "./InterviewPage";

vi.mock("../api/client", () => ({
  apiUrl: (path) => path,
  getJson: vi.fn(),
  HttpError: class HttpError extends Error {},
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
  vi.unstubAllGlobals();
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
