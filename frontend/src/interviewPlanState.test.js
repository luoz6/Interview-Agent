import { describe, expect, it } from "vitest";
import {
  copyableLocalDraft,
  createPlanEditorState,
  editableQuestions,
  hasLocalChanges,
  interviewPlanReducer,
  isLatestValidPlan,
  normalizePlanResponse,
  planEditorStatus,
  questionDraft,
} from "./interviewPlanState";

const questionId = "11111111-1111-4111-8111-111111111111";

function plan(revision = 1) {
  return {
    title: "Backend plan",
    questions: [{ id: questionId, prompt: "Legacy prompt", focus: "legacy focus" }],
    job_tags: ["Redis"],
    plan_family_id: "22222222-2222-4222-8222-222222222222",
    plan_revision_id:
      revision === 1
        ? "33333333-3333-4333-8333-333333333333"
        : "44444444-4444-4444-8444-444444444444",
    revision,
    plan_sha256: String(revision).repeat(64),
    plan: {
      schema_version: "interview-plan-v2",
      title: "Backend plan",
      questions: [
        {
          question_id: questionId,
          position: 1,
          question_text: revision === 1 ? "Server question" : "Server question v2",
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
      ],
    },
  };
}

describe("interview plan editor reducer", () => {
  it("normalizes revision-only responses while preserving workspace metadata", () => {
    const normalized = normalizePlanResponse(
      {
        legacy_plan: { title: "Revision response", questions: [] },
        plan_family_id: "family",
        plan_revision_id: "revision",
        revision: 2,
        plan_sha256: "a".repeat(64),
        plan: { questions: [] },
      },
      { job_tags: ["Python"] },
    );

    expect(normalized.title).toBe("Revision response");
    expect(normalized.job_tags).toEqual(["Python"]);
    expect(normalized.revision).toBe(2);
  });

  it("uses V2 question identity and fields as the editable authority", () => {
    const questions = editableQuestions(plan());
    expect(questions[0].question_id).toBe(questionId);
    expect(questions[0].question_text).toBe("Server question");
  });

  it("tracks a local draft and removes it when values return to the server version", () => {
    let state = createPlanEditorState(plan());
    state = interviewPlanReducer(state, {
      type: "EDIT_LOCAL_QUESTION",
      questionId,
      field: "question_text",
      value: "Local question",
    });
    expect(hasLocalChanges(state)).toBe(true);
    expect(questionDraft(state, editableQuestions(plan())[0]).question_text).toBe(
      "Local question",
    );

    state = interviewPlanReducer(state, {
      type: "EDIT_LOCAL_QUESTION",
      questionId,
      field: "question_text",
      value: "Server question",
    });
    expect(hasLocalChanges(state)).toBe(false);
  });

  it("preserves local input after an operation failure", () => {
    let state = createPlanEditorState(plan());
    state = interviewPlanReducer(state, {
      type: "EDIT_LOCAL_QUESTION",
      questionId,
      field: "focus",
      value: "local focus",
    });
    state = interviewPlanReducer(state, {
      type: "OPERATION_PENDING",
      kind: "edit_question",
      requestId: "request-1",
      questionId,
    });
    state = interviewPlanReducer(state, {
      type: "OPERATION_FAILURE",
      message: "network unavailable",
      status: 503,
    });

    expect(state.localDrafts[questionId].focus).toBe("local focus");
    expect(state.failure.message).toBe("network unavailable");
    expect(planEditorStatus(state)).toBe("failed");
    expect(isLatestValidPlan(state)).toBe(false);
  });

  it("preserves local input and records the winner after a conflict", () => {
    let state = createPlanEditorState(plan());
    state = interviewPlanReducer(state, {
      type: "EDIT_LOCAL_QUESTION",
      questionId,
      field: "question_text",
      value: "my unsaved wording",
    });
    state = interviewPlanReducer(state, {
      type: "OPERATION_CONFLICT",
      currentRevision: { revision: 2, plan_revision_id: "winner" },
      message: "conflict",
    });

    expect(state.localDrafts[questionId].question_text).toBe(
      "my unsaved wording",
    );
    expect(state.conflict.currentRevision.revision).toBe(2);
    expect(planEditorStatus(state)).toBe("conflict");
    expect(copyableLocalDraft(state)).toContain("my unsaved wording");
  });

  it("treats the successful server response as authority while preserving unrelated drafts", () => {
    const otherId = "55555555-5555-4555-8555-555555555555";
    const localDrafts = { [otherId]: { focus: "keep me" } };
    const state = interviewPlanReducer(createPlanEditorState(plan()), {
      type: "OPERATION_SUCCESS",
      plan: plan(2),
      localDrafts,
    });

    expect(state.serverPlan.revision).toBe(2);
    expect(state.serverPlan.plan.questions[0].question_text).toBe(
      "Server question v2",
    );
    expect(state.localDrafts).toEqual(localDrafts);
  });

  it("allows start only for a saved revision without draft, pending, conflict, or failure", () => {
    const ready = createPlanEditorState(plan());
    expect(isLatestValidPlan(ready)).toBe(true);
    expect(planEditorStatus(ready)).toBe("saved");

    const pending = interviewPlanReducer(ready, {
      type: "OPERATION_PENDING",
      kind: "move_question",
      requestId: "request-2",
    });
    expect(isLatestValidPlan(pending)).toBe(false);
    expect(planEditorStatus(pending)).toBe("saving");
  });

  it("keeps history separate from the active server revision", () => {
    const state = interviewPlanReducer(createPlanEditorState(plan()), {
      type: "HISTORY_SUCCESS",
      history: [{ revision: 1 }, { revision: 0 }],
    });

    expect(state.serverPlan.revision).toBe(1);
    expect(state.history).toEqual([{ revision: 1 }, { revision: 0 }]);
    expect(state.historyStatus).toBe("ready");
  });
});
