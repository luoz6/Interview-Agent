import { describe, expect, it } from "vitest";

import {
  completionTurnState,
  followupProgressLabel,
  interviewTurnLabel,
  interviewTurnStates,
  reduceTurnState,
  snapshotTurnState,
  submissionTurnState,
} from "./interviewTurnState";


describe("interview turn UI state contract", () => {
  it("shows Decision only for adaptive policy", () => {
    expect(submissionTurnState({ followup_policy_version: "adaptive_v1" })).toBe(
      interviewTurnStates.decisionPending,
    );
    expect(submissionTurnState({ followup_policy_version: "fixed_v1" })).toBe(
      interviewTurnStates.generationPending,
    );
    expect(interviewTurnLabel(interviewTurnStates.decisionPending)).toBe(
      "正在分析这次回答",
    );
  });

  it("keeps recovery stable across replayed status, reset, and chunks", () => {
    for (const event of ["status", "generation_reset", "chunk"]) {
      expect(reduceTurnState(interviewTurnStates.recovery, event)).toBe(
        interviewTurnStates.recovery,
      );
    }
    expect(interviewTurnLabel(interviewTurnStates.recovery)).toBe(
      "正在恢复上一条追问",
    );
  });

  it("moves a normal generation from pending to streaming", () => {
    expect(reduceTurnState(interviewTurnStates.decisionPending, "status")).toBe(
      interviewTurnStates.generationPending,
    );
    expect(reduceTurnState(interviewTurnStates.generationPending, "chunk")).toBe(
      interviewTurnStates.generationStreaming,
    );
  });

  it("distinguishes next, degraded, and same-question follow-up completion", () => {
    expect(completionTurnState("q1", {
      current_question: { id: "q2" },
      followup_ui_state: "idle",
    })).toBe(interviewTurnStates.nextQuestion);
    expect(completionTurnState("q1", {
      current_question: { id: "q2" },
      followup_ui_state: "degraded",
    })).toBe(interviewTurnStates.degraded);
    expect(completionTurnState("q1", {
      current_question: { id: "q1" },
      followup_ui_state: "idle",
    })).toBe(interviewTurnStates.idle);
  });

  it("restores active streams without flashing generation state", () => {
    expect(snapshotTurnState({
      active_command_id: "command-1",
      active_stream_url: "/commands/command-1/stream",
      followup_ui_state: "generation_pending",
    })).toBe(interviewTurnStates.recovery);
  });

  it("keeps main-question numbering independent from 0/1/2 follow-ups", () => {
    expect(followupProgressLabel({ current_followup_count: 0 })).toBe("追问 0 / 2");
    expect(followupProgressLabel({ current_followup_count: 1 })).toBe("追问 1 / 2");
    expect(followupProgressLabel({ current_followup_count: 2 })).toBe("追问 2 / 2");
    expect(followupProgressLabel({ current_followup_count: 9 })).toBe("追问 2 / 2");
  });
});
