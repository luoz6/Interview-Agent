import { getJson, postJson } from "../api/client";

export const getRagOverview = (options) => getJson("/api/rag/overview", options);
export const runRagInspection = (payload, options) => postJson("/api/rag/inspections", payload, options);
export const getRagEvaluations = (options) => getJson("/api/rag/evaluations", options);
export const getRagPairedEvaluations = (options) => getJson("/api/rag/evaluations-paired", options);
export const getRagEvaluationCases = (sha, options) => getJson(`/api/rag/evaluations/${encodeURIComponent(sha)}/cases`, options);
export const getRagNoEvidence = (sha, options) => getJson(`/api/rag/evaluations/${encodeURIComponent(sha)}/no-evidence`, options);
export const getRagReplay = (sha, caseId, options) => getJson(`/api/rag/evaluations/${encodeURIComponent(sha)}/cases/${encodeURIComponent(caseId)}/diagnostic-snapshot`, options);
export const getRagCorpus = (options) => getJson("/api/rag/corpus", options);
export const validateRagCorpusDraft = (payload, options) => postJson("/api/rag/corpus/drafts/validate", payload, options);
export const activateRagCorpusRelease = (payload, options) => postJson("/api/rag/corpus/releases/activate", payload, { timeoutMs: 120000, ...options });
export const getRagEvidenceTrace = (traceId, options) => getJson(`/api/rag/evidence-traces/${encodeURIComponent(traceId)}`, options);
