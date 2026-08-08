export const scoredArtifact = {
  active_artifact: {
    report_id: "report-1",
    score_status: "scored",
    coverage_status: "complete",
    overall_score: 84,
    overall_dimension_scores: { depth: 80, breadth: null, engineering: 72 },
    payload: {},
  },
  latest_job: { job_id: "job-1", status: "completed" },
};

export const unscoredArtifact = {
  active_artifact: {
    report_id: "report-2",
    score_status: "unscored",
    coverage_status: "none",
    overall_score: null,
    overall_dimension_scores: { depth: null, breadth: null },
    payload: {},
  },
  latest_job: { job_id: "job-2", status: "completed" },
};

export const partialArtifact = {
  active_artifact: {
    report_id: "report-partial",
    score_status: "partial",
    coverage_status: "partial",
    overall_score: 76,
    evaluated_count: 2,
    total_eligible_count: 3,
    overall_dimension_scores: { depth: 76, breadth: null },
    dimension_evaluations: {
      depth: { status: "evaluated", score: 76 },
      breadth: { status: "insufficient_evidence", score: null },
    },
    payload: {},
  },
  latest_job: { job_id: "job-partial", status: "completed" },
};

export const legacyReport = {
  session_id: "legacy-session",
  overall_score: 60,
  overall_dimension_scores: { depth: 60 },
};

export const failedInitialJob = {
  active_artifact: null,
  latest_job: { job_id: "job-3", status: "failed", error_code: "provider_timeout" },
};

export const failedRescoreWithActive = {
  active_artifact: scoredArtifact.active_artifact,
  latest_job: { job_id: "job-4", status: "failed", job_kind: "rescore" },
};

export const revisionConflict = {
  current_revision: { plan_revision_id: "revision-2", revision: 2 },
};
