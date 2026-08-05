import { describe, expect, it, vi } from "vitest";
import {
  confirmedRequest,
  dimensionDisplay,
  mergeRevisionConflict,
  reportPageState,
  scoreDisplay,
  weakestDimensions,
} from "./reportContract";
import {
  failedInitialJob,
  failedRescoreWithActive,
  legacyReport,
  partialArtifact,
  revisionConflict,
  scoredArtifact,
  unscoredArtifact,
} from "./test/fixtures";

describe("report five-axis contract", () => {
  it("does not coerce null dimensions to zero", () => {
    expect(weakestDimensions(scoredArtifact.active_artifact)).toEqual([
      ["engineering", 72],
      ["depth", 80],
    ]);
  });

  it("keeps an unscored artifact distinct from a failed job", () => {
    expect(reportPageState(unscoredArtifact).kind).toBe("unscored");
    expect(reportPageState(failedInitialJob).kind).toBe("failed");
  });

  it("renders partial score and coverage without filling missing dimensions", () => {
    expect(reportPageState(partialArtifact).kind).toBe("partial");
    expect(scoreDisplay(partialArtifact.active_artifact)).toEqual({
      hasScore: true,
      value: 76,
      label: "76 / 100 · 部分评分 2/3",
    });
    expect(dimensionDisplay(partialArtifact.active_artifact, "breadth")).toEqual({
      hasScore: false,
      value: null,
      label: "证据不足",
    });
  });

  it("keeps legacy fixed scores readable without rewriting them", () => {
    const state = reportPageState(legacyReport);
    expect(state.kind).toBe("ready");
    expect(state.activeArtifact.schema_version).toBe("legacy-v1");
    expect(scoreDisplay(state.activeArtifact).value).toBe(60);
  });

  it("keeps the old active artifact visible when rescore fails", () => {
    const state = reportPageState(failedRescoreWithActive);
    expect(state.kind).toBe("ready");
    expect(state.updateFailed).toBe(true);
    expect(state.activeArtifact.report_id).toBe("report-1");
  });
});

describe("plan revision interactions", () => {
  it("keeps local edits after a 409 conflict", () => {
    const draft = { title: "local", questions: [{ id: "q1", prompt: "edited" }] };
    const merged = mergeRevisionConflict(draft, revisionConflict);
    expect(merged.localDraft).toEqual(draft);
    expect(merged.currentRevision.revision).toBe(2);
  });

  it("does not send a request after confirmation is cancelled", async () => {
    const request = vi.fn();
    const result = await confirmedRequest(() => false, request);
    expect(result.sent).toBe(false);
    expect(request).not.toHaveBeenCalled();
  });
});
