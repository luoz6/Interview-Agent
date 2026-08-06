export function unwrapReportResponse(response) {
  if (!response) return { activeArtifact: null, latestJob: null };
  if (Object.prototype.hasOwnProperty.call(response, "active_artifact")) {
    return {
      activeArtifact: response.active_artifact,
      latestJob: response.latest_job || null,
    };
  }
  return {
    activeArtifact: {
      schema_version: "legacy-v1",
      score_status: typeof response.overall_score === "number" ? "scored" : "unscored",
      coverage_status: "none",
      payload: response,
      ...response,
    },
    latestJob: null,
  };
}

const artifactProjectionFields = [
  "report_id",
  "session_id",
  "revision",
  "created_at",
  "active",
  "schema_version",
  "report_schema_version",
  "presentation_version",
  "scoring_rubric_version",
  "generation_status",
  "generation_reason_code",
  "score_status",
  "score_reason_code",
  "coverage_status",
  "report_path",
  "overall_score",
  "overall_dimension_scores",
  "evaluated_count",
  "total_eligible_count",
  "evidence_count",
  "dimension_evaluations",
  "question_evaluations",
];

export function reportDetailData(response) {
  const { activeArtifact, latestJob: responseLatestJob } = unwrapReportResponse(response);
  if (!activeArtifact) {
    return {
      report: null,
      artifact: null,
      latestJob: responseLatestJob,
      updateFailed: false,
      updating: false,
    };
  }
  const payload = activeArtifact.payload && typeof activeArtifact.payload === "object"
    ? activeArtifact.payload
    : {};
  const report = { ...payload };
  artifactProjectionFields.forEach((field) => {
    if (activeArtifact[field] !== undefined) report[field] = activeArtifact[field];
  });
  const latestJob = responseLatestJob || activeArtifact.latest_job || null;
  return {
    report,
    artifact: activeArtifact,
    latestJob,
    updateFailed: latestJob?.status === "failed",
    updating: ["queued", "running"].includes(latestJob?.status),
  };
}

export function reportRevisionLabel(artifact) {
  if (!Number.isInteger(artifact?.revision)) return "历史兼容报告";
  return `第 ${artifact.revision} 版`;
}

export function reportCreatedAtLabel(value) {
  if (!value) return "时间未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未记录";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function numericDimensionEntries(view) {
  const values = view?.overall_dimension_scores || view?.payload?.overall_dimension_scores || {};
  return Object.entries(values).filter(([, value]) => Number.isFinite(value));
}

export function scoreDisplay(view) {
  const score = view?.overall_score ?? view?.payload?.overall_score;
  if (!Number.isFinite(score)) {
    return { hasScore: false, value: null, label: "未评分" };
  }
  const status = view?.score_status || "scored";
  const suffix = status === "partial"
    ? `部分评分 ${view?.evaluated_count ?? "?"}/${view?.total_eligible_count ?? "?"}`
    : "已评分";
  return { hasScore: true, value: score, label: `${score} / 100 · ${suffix}` };
}

export function dimensionDisplay(view, dimension) {
  const values = view?.overall_dimension_scores || view?.payload?.overall_dimension_scores || {};
  const evaluations = view?.dimension_evaluations || view?.payload?.dimension_evaluations || {};
  const value = values[dimension];
  if (Number.isFinite(value)) return { hasScore: true, value, label: `${value} / 100` };
  const status = evaluations[dimension]?.status;
  return {
    hasScore: false,
    value: null,
    label: status === "insufficient_evidence" ? "证据不足" : "未评估",
  };
}

export function weakestDimensions(view, limit = 2) {
  return numericDimensionEntries(view)
    .sort((left, right) => left[1] - right[1])
    .slice(0, limit);
}

export function reportPageState(response) {
  const { activeArtifact, latestJob } = unwrapReportResponse(response);
  if (activeArtifact) {
    return {
      kind: activeArtifact.score_status === "unscored"
        ? "unscored"
        : activeArtifact.score_status === "partial"
          ? "partial"
          : "ready",
      activeArtifact,
      latestJob,
      updateFailed: latestJob?.status === "failed",
      updating: ["queued", "running"].includes(latestJob?.status),
    };
  }
  if (latestJob?.status === "failed") return { kind: "failed", activeArtifact: null, latestJob };
  if (["queued", "running"].includes(latestJob?.status)) return { kind: "processing", activeArtifact: null, latestJob };
  return { kind: "empty", activeArtifact: null, latestJob };
}

export function mergeRevisionConflict(localDraft, conflictResponse) {
  return {
    localDraft: structuredClone(localDraft),
    currentRevision: conflictResponse?.current_revision || null,
    conflict: true,
  };
}

export async function confirmedRequest(confirmAction, requestAction) {
  const confirmed = await confirmAction();
  if (!confirmed) return { sent: false };
  return { sent: true, value: await requestAction() };
}
