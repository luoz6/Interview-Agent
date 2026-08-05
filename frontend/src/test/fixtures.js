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
