import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { interviewTurnStates } from "../interviewTurnState";
import { InterviewTurnStatus } from "./InterviewPage";

afterEach(() => cleanup());


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
