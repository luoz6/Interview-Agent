import { describe, expect, it } from "vitest";
import {
  configurationMatchesSnapshot,
  createPlanConfiguration,
  describeConfigurationChanges,
  PLAN_DURATIONS,
  planConfigurationEstimate,
  planConfigurationPayload,
  QUESTION_MIX_PRESETS,
  safeQuestionTypeBudget,
  updatePlanConfiguration,
} from "./interviewPlanConfiguration";

describe("interview plan configuration presets", () => {
  it("maps every duration and mix preset to a bounded safe question count", () => {
    const expectedCounts = { 15: 3, 30: 5, 45: 7, 60: 9 };
    for (const duration of PLAN_DURATIONS) {
      for (const preset of QUESTION_MIX_PRESETS) {
        const budget = safeQuestionTypeBudget(duration, preset.value);
        expect(Object.values(budget).reduce((sum, count) => sum + count, 0)).toBe(
          expectedCounts[duration],
        );
        expect(Object.keys(budget).every((key) =>
          ["project", "technical", "system-design", "behavioral"].includes(key),
        )).toBe(true);
      }
    }
  });

  it("creates the frozen backend default without exposing arbitrary percentages", () => {
    const configuration = createPlanConfiguration();
    expect(planConfigurationPayload(configuration)).toEqual({
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
    });
  });

  it("recomputes counts and follow-up estimates from safe choices", () => {
    const configured = updatePlanConfiguration(
      updatePlanConfiguration(createPlanConfiguration(), "target_duration_minutes", 60),
      "question_mix_preset",
      "architecture",
    );
    expect(planConfigurationEstimate(configured)).toEqual({
      questionCount: 9,
      targetMinutes: 60,
      expectedFollowups: 9,
      maxFollowupsPerQuestion: 2,
    });
    expect(configured.question_type_budget["system-design"]).toBe(4);
  });

  it("locks the per-question follow-up ceiling to two", () => {
    const tampered = { ...createPlanConfiguration(), max_followups_per_question: 99 };
    expect(planConfigurationPayload(tampered).max_followups_per_question).toBe(2);
    expect(updatePlanConfiguration(tampered, "difficulty", "advanced").max_followups_per_question).toBe(2);
  });

  it("restores a matching configuration snapshot and detects meaningful changes", () => {
    const snapshot = planConfigurationPayload(
      updatePlanConfiguration(createPlanConfiguration(), "difficulty", "advanced"),
    );
    const restored = createPlanConfiguration(snapshot);
    expect(configurationMatchesSnapshot(restored, snapshot)).toBe(true);

    const changed = updatePlanConfiguration(restored, "focus_preset", "system_design");
    expect(configurationMatchesSnapshot(changed, snapshot)).toBe(false);
    expect(describeConfigurationChanges(changed, snapshot)).toEqual(["考察重点"]);
  });

  it("preserves an older backend-valid snapshot that is not one of the new UI presets", () => {
    const snapshot = {
      ...planConfigurationPayload(createPlanConfiguration()),
      question_type_budget: { technical: 5 },
      expected_followup_budget: 4,
    };

    const restored = createPlanConfiguration(snapshot);

    expect(restored.question_mix_preset).toBe("saved");
    expect(restored.question_type_budget).toEqual({ technical: 5 });
    expect(restored.expected_followup_budget).toBe(4);
    expect(configurationMatchesSnapshot(restored, snapshot)).toBe(true);
    expect(
      updatePlanConfiguration(restored, "question_mix_preset", "balanced")
        .question_type_budget,
    ).toEqual(defaultQuestionBudget());
  });
});

function defaultQuestionBudget() {
  return {
    project: 1,
    technical: 2,
    "system-design": 1,
    behavioral: 1,
  };
}
