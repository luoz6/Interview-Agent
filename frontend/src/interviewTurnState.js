export const interviewTurnStates = Object.freeze({
  idle: "idle",
  decisionPending: "decision_pending",
  generationPending: "generation_pending",
  generationStreaming: "generation_streaming",
  nextQuestion: "next_question",
  degraded: "degraded",
  recovery: "recovery",
});

export const interviewTurnLabels = Object.freeze({
  idle: "",
  decision_pending: "正在分析这次回答",
  generation_pending: "正在组织追问",
  generation_streaming: "追问生成中",
  next_question: "回答已记录，进入下一题",
  degraded: "本题将继续到下一题",
  recovery: "正在恢复上一条追问",
});

export function isAdaptivePolicy(snapshot) {
  return snapshot?.followup_policy_version === "adaptive_v1";
}

export function submissionTurnState(snapshot) {
  return isAdaptivePolicy(snapshot)
    ? interviewTurnStates.decisionPending
    : interviewTurnStates.generationPending;
}

export function snapshotTurnState(snapshot) {
  if (snapshot?.followup_ui_state === "degraded") {
    return interviewTurnStates.degraded;
  }
  if (snapshot?.active_stream_url && snapshot?.active_command_id) {
    return interviewTurnStates.recovery;
  }
  if (snapshot?.followup_ui_state === "decision_pending" && isAdaptivePolicy(snapshot)) {
    return interviewTurnStates.decisionPending;
  }
  if (snapshot?.followup_ui_state === "generation_pending") {
    return interviewTurnStates.generationPending;
  }
  return interviewTurnStates.idle;
}

export function reduceTurnState(current, event) {
  if (event === "reconnect") return interviewTurnStates.recovery;
  if (current === interviewTurnStates.recovery) {
    if (["status", "generation_reset", "chunk"].includes(event)) return current;
  }
  if (event === "status" || event === "generation_reset") {
    return interviewTurnStates.generationPending;
  }
  if (event === "chunk") return interviewTurnStates.generationStreaming;
  return current;
}

export function completionTurnState(previousQuestionId, nextSnapshot) {
  if (nextSnapshot?.followup_ui_state === "degraded") {
    return interviewTurnStates.degraded;
  }
  const nextQuestionId = nextSnapshot?.current_question?.id || null;
  if (
    nextSnapshot?.status === "finished"
    || (previousQuestionId && nextQuestionId && previousQuestionId !== nextQuestionId)
  ) {
    return interviewTurnStates.nextQuestion;
  }
  return interviewTurnStates.idle;
}

export function normalizedFollowupCount(snapshot) {
  const value = Number(snapshot?.current_followup_count || 0);
  return Number.isInteger(value) ? Math.max(0, Math.min(2, value)) : 0;
}

export function followupProgressLabel(snapshot) {
  return `追问 ${normalizedFollowupCount(snapshot)} / 2`;
}

export function interviewTurnLabel(state) {
  return interviewTurnLabels[state] || "";
}
