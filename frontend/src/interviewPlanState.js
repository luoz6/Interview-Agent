function clone(value) {
  return value == null ? value : structuredClone(value);
}

export function normalizePlanResponse(response, inherited = {}) {
  if (!response) return null;
  const legacy = response.legacy_plan || response;
  const publicPlan = response.plan || inherited.plan || null;
  return {
    ...legacy,
    job_tags: response.job_tags || inherited.job_tags || [],
    plan_family_id: response.plan_family_id || inherited.plan_family_id || null,
    plan_revision_id:
      response.plan_revision_id || inherited.plan_revision_id || null,
    revision: response.revision || inherited.revision || null,
    plan_sha256: response.plan_sha256 || inherited.plan_sha256 || null,
    plan: publicPlan,
    audit: response.audit || inherited.audit || null,
    budget_assessment:
      response.budget_assessment || inherited.budget_assessment || null,
  };
}

export function editableQuestions(serverPlan) {
  if (Array.isArray(serverPlan?.plan?.questions)) {
    return serverPlan.plan.questions;
  }
  return (serverPlan?.questions || []).map((question, index) => ({
    question_id: question.id,
    position: index + 1,
    question_text: question.prompt,
    focus: question.focus || "",
    question_type: question.kind || "technical",
    difficulty: "intermediate",
    expected_minutes: 5,
    expected_followups: 0,
    origin: "generated",
    replaces_question_id: null,
    knowledge_binding: {
      schema_version: "plan-question-knowledge-binding-v1",
      status: "unbound",
      evidence_ids: [],
      reason_code: "legacy_public_projection",
    },
  }));
}

export function createPlanEditorState(serverPlan = null) {
  return {
    serverPlan: clone(serverPlan),
    localDrafts: {},
    pendingOperation: null,
    conflict: null,
    failure: null,
    history: [],
    historyStatus: "idle",
    historyError: null,
    serverPreview: null,
  };
}

export function questionDraft(state, question) {
  const local = state.localDrafts[question.question_id] || {};
  return {
    question_text:
      local.question_text === undefined
        ? question.question_text
        : local.question_text,
    focus: local.focus === undefined ? question.focus : local.focus,
  };
}

export function hasLocalChanges(state) {
  return Object.keys(state.localDrafts).length > 0;
}

export function isLatestValidPlan(state) {
  const plan = state.serverPlan;
  return Boolean(
    plan?.plan_family_id &&
      plan?.plan_revision_id &&
      Number.isInteger(plan?.revision) &&
      plan?.plan_sha256 &&
      !state.pendingOperation &&
      !state.conflict &&
      !state.failure &&
      !hasLocalChanges(state),
  );
}

export function planEditorStatus(state) {
  if (state.pendingOperation) return "saving";
  if (state.conflict) return "conflict";
  if (state.failure) return "failed";
  if (hasLocalChanges(state)) return "draft";
  if (state.serverPlan) return "saved";
  return "empty";
}

export function copyableLocalDraft(state) {
  const questions = editableQuestions(state.serverPlan);
  const lines = questions
    .filter((question) => state.localDrafts[question.question_id])
    .map((question) => {
      const draft = questionDraft(state, question);
      return [
        "第 " + question.position + " 题",
        "问题：" + draft.question_text,
        "考察重点：" + draft.focus,
      ].join("\n");
    });
  return lines.join("\n\n");
}

export function interviewPlanReducer(state, action) {
  switch (action.type) {
    case "LOAD_SERVER_PLAN":
      return {
        ...createPlanEditorState(action.plan),
        history: action.keepHistory ? state.history : [],
        historyStatus: action.keepHistory ? state.historyStatus : "idle",
      };
    case "INVALIDATE_SOURCE":
      return createPlanEditorState();
    case "EDIT_LOCAL_QUESTION": {
      const current = state.localDrafts[action.questionId] || {};
      const nextDraft = { ...current, [action.field]: action.value };
      const serverQuestion = editableQuestions(state.serverPlan).find(
        (question) => question.question_id === action.questionId,
      );
      const effectiveQuestionText =
        nextDraft.question_text === undefined
          ? serverQuestion?.question_text
          : nextDraft.question_text;
      const effectiveFocus =
        nextDraft.focus === undefined
          ? serverQuestion?.focus
          : nextDraft.focus;
      if (
        serverQuestion &&
        effectiveQuestionText === serverQuestion.question_text &&
        effectiveFocus === serverQuestion.focus
      ) {
        const localDrafts = { ...state.localDrafts };
        delete localDrafts[action.questionId];
        return { ...state, localDrafts, failure: null };
      }
      return {
        ...state,
        localDrafts: {
          ...state.localDrafts,
          [action.questionId]: nextDraft,
        },
        failure: null,
      };
    }
    case "DISCARD_LOCAL_QUESTION": {
      const localDrafts = { ...state.localDrafts };
      delete localDrafts[action.questionId];
      return { ...state, localDrafts, failure: null };
    }
    case "OPERATION_PENDING":
      return {
        ...state,
        pendingOperation: {
          kind: action.kind,
          requestId: action.requestId,
          questionId: action.questionId || null,
        },
        conflict: null,
        failure: null,
        serverPreview: null,
      };
    case "OPERATION_SUCCESS":
      return {
        ...createPlanEditorState(action.plan),
        localDrafts: clone(action.localDrafts || {}),
        history: action.history || state.history,
        historyStatus: state.historyStatus,
      };
    case "OPERATION_FAILURE":
      return {
        ...state,
        pendingOperation: null,
        failure: {
          message: action.message,
          status: action.status || 0,
          code: action.code || null,
        },
      };
    case "OPERATION_CONFLICT":
      return {
        ...state,
        pendingOperation: null,
        failure: null,
        conflict: {
          currentRevision: action.currentRevision || null,
          message: action.message,
        },
        serverPreview: null,
      };
    case "HISTORY_LOADING":
      return { ...state, historyStatus: "loading", historyError: null };
    case "HISTORY_SUCCESS":
      return {
        ...state,
        history: clone(action.history),
        historyStatus: "ready",
        historyError: null,
      };
    case "HISTORY_FAILURE":
      return {
        ...state,
        historyStatus: "failed",
        historyError: action.message,
      };
    case "SERVER_PREVIEW_LOADED":
      return { ...state, serverPreview: clone(action.plan) };
    case "CLEAR_FAILURE":
      return { ...state, failure: null };
    default:
      return state;
  }
}
