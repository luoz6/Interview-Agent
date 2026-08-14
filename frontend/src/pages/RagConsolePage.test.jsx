import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RagRetrievalPage } from "./RagRetrievalPage";
import { RagEvidenceTracePage } from "./RagEvidenceTracePage";
import { RagEvaluationPage } from "./RagEvaluationPage";
import { RagCorpusPage } from "./RagCorpusPage";
import { RagOverviewPage } from "./RagOverviewPage";
import {
  CandidateTable,
  IdentityValue,
  StatusPill,
} from "../components/rag/RagPrimitives";

function response(payload, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    text: async () => JSON.stringify(payload),
  });
}

const inspection = {
  schema_version: "rag-retrieval-inspection-v1",
  request_id: "req-1",
  mode: "live",
  created_at: "2026-08-13T00:00:00Z",
  diagnostic_fidelity: "live",
  engine: "hybrid-v2",
  profile_id: "question-review",
  profile_version: "hybrid-v1",
  trace_schema_version: "retrieval-trace-v3",
  inspection_inputs: { intent: "question_review", requested_domains: [], requested_topics: [], canonical_tags: [], source_types: [] },
  query_facts: { query_sha256: "a".repeat(64) },
  resolved_profile: {},
  routing_summary: {},
  fusion_summary: {
    strategy: "weighted_rrf",
    semantic_candidate_count: 8,
    lexical_candidate_count: 5,
    fused_candidate_count: 10,
    candidate_limit: 12,
    rrf_k: 60,
    semantic_weight: 0.8,
    lexical_weight: 1.4,
    query_signal: "lexical_dominant",
    reason_codes: ["exact_alias_match"],
  },
  channel_summary: [],
  candidates: [{
    candidate_id: "redis-lock", title: "Redis lock", safe_excerpt: "Safe summary",
    domain: "redis", topic: "locking", tags: ["redis"], source_type: "theory",
    authority_status: "approved", content_sha256: "b".repeat(64),
    corpus_manifest_sha256: "c".repeat(64), semantic_rank: 1, semantic_score: 0.9,
    lexical_rank: null, lexical_score: null, fusion_rank: 1, fusion_score: 0.03,
    rerank_rank: 1, rerank_score: 0.95, channel_hits: ["semantic"], matched_terms: [],
    ranking_explanation: null, selected: true,
  }],
  evidence_decision: { availability: "available", sufficiency: "sufficient", consistency: "possible_conflict", evaluation_confidence: "medium", covered_signals: ["redis"], missing_signals: ["failure mode"], reason_codes: ["signal_gap"], gate_version: "gate-v2" },
  consumer_action: { recording_status: "not_recorded", public_message: "Not recorded / no unified policy", reason_codes: [] },
  latency_ms: { semantic: 1, lexical: null, fusion: 0.1, rerank: 0.2, evidence_gate: 0.1, total: 2 },
  degraded_reasons: [], component_versions: {}, artifact_sha256: null, case_id: null,
  provider_call_possible: true, artifact_identity: {},
};

const fullSnapshotReplay = {
  ...inspection,
  request_id: "acceptance-snapshot",
  mode: "artifact_replay",
  diagnostic_fidelity: "full_snapshot",
  query_facts: { query_sha256: "f".repeat(64), character_count: 17 },
  candidates: [{
    ...inspection.candidates[0],
    semantic_rank: 1, semantic_score: 0.9,
    lexical_rank: 2, lexical_score: 3.5,
    fusion_rank: 1, fusion_score: 0.03,
    rerank_rank: 1, rerank_score: 0.09,
    channel_hits: ["semantic", "lexical"], matched_terms: ["redis"],
    ranking_explanation: {
      base_score_source: "fusion_score", base_score: 0.03,
      exact_term_boost: 0.06, routing_tag_boost: 0,
      eligibility_score: 0.96, eligible: true, final_rerank_score: 0.09,
      tie_break_fusion_rank: 1, reason_codes: ["eligible", "exact_term_boost"],
    },
    selected: true,
  }],
  evidence_decision: {
    availability: "available", sufficiency: "sufficient", consistency: "consistent",
    evaluation_confidence: "high", covered_signals: ["redis"], missing_signals: [],
    reason_codes: ["evidence_sufficient"], gate_version: "acceptance-gate-v1",
  },
  latency_ms: { semantic: 1, lexical: 0.8, fusion: 0.1, rerank: 0.2, evidence_gate: 0.1, total: 2 },
  provider_call_possible: false,
  artifact_identity: {
    artifact_sha256: "a".repeat(64),
    corpus_manifest_sha256: "c".repeat(64),
    code_revision: "acceptance-revision-v1",
  },
  artifact_sha256: "a".repeat(64),
  case_id: "full-snapshot-case",
};

const comparison = {
  schema_version: "rag-retrieval-compare-v1",
  created_at: "2026-08-14T00:00:00Z",
  request_id: "req-compare",
  requested_profile_id: "question-review",
  corpus_manifest_sha256: "c".repeat(64),
  legacy: {
    status: "success",
    failure_code: null,
    inspection: {
      ...inspection,
      engine: "legacy",
      candidates: [{
        ...inspection.candidates[0],
        semantic_rank: null,
        semantic_score: null,
        lexical_rank: 1,
        lexical_score: 2.4,
        fusion_rank: null,
        fusion_score: null,
        rerank_rank: 1,
        selected: true,
      }],
      latency_ms: { ...inspection.latency_ms, total: 1.5 },
    },
  },
  hybrid: { status: "success", failure_code: null, inspection },
  top_k_overlap: { k: 5, overlap_count: 1, overlap_ratio: 0.2, candidate_ids: ["redis-lock"] },
  rank_changes: [{
    candidate_id: "redis-lock",
    legacy_rank: 1,
    hybrid_rank: 1,
    rank_delta: 0,
    legacy_selected: true,
    hybrid_selected: true,
  }],
  selected_evidence_changed: false,
  evidence_decision_changed: false,
  latency_delta_ms: 0.5,
};

const metrics = {
  recall_at_5: 0.8, mrr_at_5: 0.7, ndcg_at_5: 0.72, hit_at_1: 0.65,
  filter_correctness_rate: 1, no_evidence_precision: 0, no_evidence_recall: 0,
  no_evidence_f1: 0, evidence_replay_stability_rate: 1, p95_latency_ms: 14,
  case_type_breakdown: {
    alias_only: { case_count: 1, recall_at_5: 1, mrr_at_5: 1, ndcg_at_5: 1, hit_at_1: 1, evidence_precision_at_5: 1, domain_routing_accuracy: 1, topic_routing_accuracy: 1, no_evidence_precision: 0, no_evidence_recall: 0 },
    hard_negative: { case_count: 1, recall_at_5: 0, mrr_at_5: 0, ndcg_at_5: 0, hit_at_1: 0, evidence_precision_at_5: 0, domain_routing_accuracy: 1, topic_routing_accuracy: 1, no_evidence_precision: 0, no_evidence_recall: 0 },
  },
};

function artifact(overrides = {}) {
  return {
    artifact_sha256: "a".repeat(64), schema_version: "knowledge-eval-artifact-v3",
    dataset_version: "eval-v3", split: "tuning", engine_version: "legacy",
    created_at: "2026-08-13T00:00:00Z", case_count: 75,
    benchmark_type: "demo_diagnostic_dataset", label_source: "curated_machine_assisted",
    purpose: "engineering_comparison", diagnostic_status: "current",
    corpus_manifest_sha256: "b".repeat(64), embedding_provider: "siliconflow",
    embedding_model: "BAAI/bge-m3", embedding_revision: "revision-v1", embedding_dimension: 1024,
    code_revision: "revision", code_tree_sha256: "c".repeat(64), profile_id: "question-review",
    profile_version: "hybrid-v1", profile_sha256: "d".repeat(64),
    diagnostic_fidelity: "partial_historical", metrics,
    ...overrides,
  };
}

function routeFetch(routes) {
  fetch.mockImplementation((url) => {
    const match = Object.entries(routes).find(([path]) => url === path);
    if (!match) throw new Error(`Unexpected request: ${url}`);
    return response(match[1]);
  });
}

describe("RAG console diagnostics", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState({}, "", "/"); });

  it("keeps a live query out of the URL and response, and restores drawer focus", async () => {
    const user = userEvent.setup();
    fetch.mockImplementationOnce((url, options) => {
      expect(url).toBe("/api/rag/inspections");
      expect(JSON.parse(options.body).query_text).toBe("private Redis query");
      return response(inspection);
    });
    window.history.replaceState({}, "", "/rag/retrieval");
    render(<RagRetrievalPage />);
    await user.type(screen.getByRole("textbox", { name: "诊断问题" }), "private Redis query");
    await user.selectOptions(screen.getByLabelText("诊断方式"), "single");
    await user.click(screen.getByRole("button", { name: "开始诊断" }));
    expect(await screen.findByText("Redis lock")).toBeInTheDocument();
    expect(window.location.search).toBe("");
    expect(JSON.stringify(inspection)).not.toContain("private Redis query");
    const explain = screen.getByRole("button", { name: "查看解释" });
    await user.click(explain);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(explain).toHaveFocus();
    expect(screen.getByText("可能冲突")).toBeInTheDocument();
    expect(screen.getByText("failure mode")).toBeInTheDocument();
    expect(screen.getByText("signal_gap")).toBeInTheDocument();
    expect(screen.getByText("gate-v2")).toBeInTheDocument();
    expect(screen.getByText("本次问题如何分配检索权重")).toBeInTheDocument();
    expect(screen.getByText("词法优先")).toBeInTheDocument();
    expect(screen.getByText("exact_alias_match")).toBeInTheDocument();
  });

  it("cancels a live inspection without persisting the query", async () => {
    const user = userEvent.setup();
    fetch.mockImplementationOnce((_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    window.history.replaceState({}, "", "/rag/retrieval");
    render(<RagRetrievalPage />);
    await user.type(screen.getByRole("textbox", { name: "诊断问题" }), "ephemeral query");
    await user.selectOptions(screen.getByLabelText("诊断方式"), "single");
    await user.click(screen.getByRole("button", { name: "开始诊断" }));
    await user.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "开始诊断" })).toBeEnabled());
    expect(window.location.search).toBe("");
    expect(localStorage.length).toBe(0);
  });

  it("compares Legacy and Hybrid in one cancellable request and renders stage differences", async () => {
    const user = userEvent.setup();
    fetch.mockImplementationOnce((url, options) => {
      expect(url).toBe("/api/rag/inspections/compare");
      const payload = JSON.parse(options.body);
      expect(payload.query_text).toBe("private compare query");
      expect(payload.engine).toBeUndefined();
      expect(options.signal).toBeInstanceOf(AbortSignal);
      return response(comparison);
    });
    window.history.replaceState({}, "", "/rag/retrieval");
    render(<RagRetrievalPage />);
    await user.type(screen.getByRole("textbox", { name: "诊断问题" }), "private compare query");
    await user.click(screen.getByRole("button", { name: "比较两种引擎" }));

    expect(await screen.findByText("同一问题的服务端差异")).toBeInTheDocument();
    expect(screen.getByText("1 / 5")).toBeInTheDocument();
    expect(screen.getAllByText("保持一致")).toHaveLength(2);
    expect(screen.getByText("+0.5 ms")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Legacy：语义/ })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: /Hybrid：语义/ })).toBeInTheDocument();
    expect(screen.getByText("排名不变")).toBeInTheDocument();
    expect(window.location.search).toBe("");
    expect(JSON.stringify(comparison)).not.toContain("private compare query");
  });

  it("cancels the default dual-engine comparison without storing the query", async () => {
    const user = userEvent.setup();
    fetch.mockImplementationOnce((url, options) => new Promise((_resolve, reject) => {
      expect(url).toBe("/api/rag/inspections/compare");
      options.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
    }));
    window.history.replaceState({}, "", "/rag/retrieval");
    render(<RagRetrievalPage />);
    await user.type(screen.getByRole("textbox", { name: "诊断问题" }), "ephemeral compare query");
    await user.click(screen.getByRole("button", { name: "比较两种引擎" }));
    await user.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "比较两种引擎" })).toBeEnabled());
    expect(window.location.search).toBe("");
    expect(localStorage.length).toBe(0);
  });

  it("renders every frozen snapshot stage, final evidence, gate, latency, and explanation", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    fetch.mockImplementationOnce((url) => {
      expect(url).toBe(
        `/api/rag/evaluations/${"a".repeat(64)}/cases/full-snapshot-case/diagnostic-snapshot`,
      );
      return response(fullSnapshotReplay);
    });
    window.history.replaceState(
      {},
      "",
      `/rag/retrieval?artifact=${"a".repeat(64)}&case=full-snapshot-case`,
    );

    render(<RagRetrievalPage />);

    expect(await screen.findAllByText("冻结制品回放")).not.toHaveLength(0);
    expect(screen.getByText(/不调用模型服务/)).toBeInTheDocument();
    for (const stage of ["语义检索", "词法检索", "融合", "重排", "最终结果"]) {
      expect(screen.getByRole("columnheader", { name: stage })).toBeInTheDocument();
    }
    expect(screen.getByText("已选用")).toBeInTheDocument();
    expect(screen.getByText("acceptance-gate-v1")).toBeInTheDocument();
    expect(screen.getByText("evidence_sufficient")).toBeInTheDocument();
    expect(
      screen.getAllByText("词法检索").find((node) => node.tagName === "SPAN")
        .nextElementSibling,
    ).toHaveTextContent("0.80 ms");
    expect(
      screen.getAllByText("证据门禁").find((node) => node.tagName === "SPAN")
        .nextElementSibling,
    ).toHaveTextContent("0.10 ms");

    await user.click(screen.getByRole("button", { name: "查看解释" }));
    expect(screen.getByText("融合分数")).toBeInTheDocument();
    expect(screen.getByText("精确词加分")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "复制制品 SHA" }));
    expect(writeText).toHaveBeenCalledWith("a".repeat(64));
    expect(screen.getByRole("button", { name: "复制制品 SHA" })).toHaveTextContent("已复制");
  });

  it("shows not-recorded lineage without reconstructing sensitive fields", async () => {
    const user = userEvent.setup();
    fetch.mockImplementationOnce(() => response({
      schema_version: "rag-evidence-trace-v1",
      trace_id: "session-1",
      generated_at: "2026-08-13T00:00:00Z",
      stages: [{ stage: "followup_decision", recording_status: "not_recorded", record_id: null, parent_record_id: null, evidence_ids: [], corpus_manifest_sha256: "", decision: null, created_at: null, note: "No persisted record is available; no value was inferred." }],
      safe_boundary: ["raw_query_excluded", "chain_of_thought_excluded"],
    }));
    window.history.replaceState({}, "", "/rag/evidence-trace");
    render(<RagEvidenceTracePage />);
    await user.type(screen.getByRole("textbox", { name: "不透明会话 / Trace ID" }), "session-1");
    await user.click(screen.getByRole("button", { name: "查询证据链" }));
    expect(await screen.findByText("Follow-up Decision（追问结论）")).toBeInTheDocument();
    expect(screen.getAllByText("未记录").length).toBeGreaterThan(0);
    await waitFor(() => expect(fetch).toHaveBeenCalledWith("/api/rag/evidence-traces/session-1", expect.any(Object)));
  });

  it("renders diagnostic identity before KPI, paired summaries, and partial replay", async () => {
    const user = userEvent.setup();
    const tuning = artifact();
    const historical = artifact({ artifact_sha256: "e".repeat(64), split: "holdout", case_count: 25, diagnostic_status: "historical_compatible" });
    routeFetch({
      "/api/rag/evaluations": { schema_version: "rag-artifact-catalog-v2", artifacts: [tuning, historical] },
      "/api/rag/evaluations-paired": { schema_version: "rag-paired-evaluations-v2", comparisons: [{ artifact_sha256: "f".repeat(64), dataset_version: "eval-v3", split: "tuning", baseline_artifact_sha256: tuning.artifact_sha256, candidate_artifact_sha256: "9".repeat(64), baseline_engine_version: "legacy", candidate_engine_version: "hybrid-v2", comparison_status: "diagnostic", metrics: [], case_type_deltas: {} }] },
      [`/api/rag/evaluations/${tuning.artifact_sha256}/cases`]: { artifact_sha256: tuning.artifact_sha256, cases: [
        { case_id: "alias-case", case_type: "alias_only", evaluation_group: "alias", primary_relevant_chunk_ids: ["redis-lock"], accepted_related_chunk_ids: [], excluded_chunk_ids: [], expected_no_evidence: false, availability: "available", selected_evidence_ids: ["redis-lock"], declared_no_evidence: false, latency_ms: 4, reason_codes: [], diagnostic_fidelity: "partial_historical", diagnostic_snapshot_ref: null },
        { case_id: "negative-case", case_type: "hard_negative", evaluation_group: "negative", primary_relevant_chunk_ids: [], accepted_related_chunk_ids: [], excluded_chunk_ids: ["redis-lock"], expected_no_evidence: true, availability: "available", selected_evidence_ids: ["redis-lock"], declared_no_evidence: false, latency_ms: 5, reason_codes: ["false_evidence"], diagnostic_fidelity: "partial_historical", diagnostic_snapshot_ref: null },
      ] },
      [`/api/rag/evaluations/${tuning.artifact_sha256}/no-evidence`]: { correct_evidence: 67, false_abstention: 0, false_evidence: 8, correct_abstention: 0, total_case_count: 75, expected_no_evidence_count: 8, abstention_count: 0, no_evidence_prevalence: 8 / 75, abstention_rate: 0, precision: 0, recall: 0, f1: 0, false_abstention_case_ids: [], false_evidence_case_ids: ["negative-case"], correct_abstention_case_ids: [], reason_code_breakdown: { exact_lexical_evidence_missing: 8 } },
    });
    window.history.replaceState({}, "", "/rag/evaluation");
    render(<RagEvaluationPage />);

    const identity = await screen.findByText("冻结执行身份");
    const matrix = screen.getByText("Legacy 与检索引擎对比");
    expect(identity.compareDocumentPosition(matrix) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText("历史兼容的最终诊断集")).toBeInTheDocument();
    expect(await screen.findByText("诊断比较")).toBeInTheDocument();
    expect(screen.queryByText(/发布证据|证据资格|人工标注人数/)).not.toBeInTheDocument();
    expect(screen.getByText("错误返回证据").nextElementSibling).toHaveTextContent("8");
    expect(screen.getByText("错误取证案例").nextElementSibling).toHaveTextContent("negative-case");
    expect(screen.getByText("原因代码分布").nextElementSibling).toHaveTextContent("exact_lexical_evidence_missing × 8");
    expect(screen.getAllByText(/alias_only/).length).toBeGreaterThan(1);
    expect(await screen.findAllByText("缺失阶段不会被重建。")).not.toHaveLength(0);

    await user.selectOptions(screen.getByLabelText("案例类型"), "hard_negative");
    expect(screen.getAllByText("negative-case").length).toBeGreaterThan(0);
    expect(screen.queryByText("alias-case")).not.toBeInTheDocument();
    const replay = screen.getByRole("link", { name: "在检索诊断中打开冻结回放" });
    expect(replay.getAttribute("href")).not.toContain("query");
  });

  it("does not select a rejected rank-normalized artifact by default", async () => {
    const rejected = artifact({ artifact_sha256: "8".repeat(64), engine_version: "hybrid-v2:rank-normalized-score" });
    const candidate = artifact({ artifact_sha256: "7".repeat(64), engine_version: "hybrid-v2:weighted-rrf" });
    routeFetch({
      "/api/rag/evaluations": { schema_version: "rag-artifact-catalog-v2", artifacts: [rejected, candidate] },
      "/api/rag/evaluations-paired": { schema_version: "rag-paired-evaluations-v2", comparisons: [] },
      [`/api/rag/evaluations/${candidate.artifact_sha256}/cases`]: { artifact_sha256: candidate.artifact_sha256, cases: [] },
      [`/api/rag/evaluations/${candidate.artifact_sha256}/no-evidence`]: { correct_evidence: 0, false_abstention: 0, false_evidence: 0, correct_abstention: 0, total_case_count: 0, expected_no_evidence_count: 0, abstention_count: 0, no_evidence_prevalence: 0, abstention_rate: 0, precision: 0, recall: 0, f1: 0 },
    });
    render(<RagEvaluationPage />);

    expect(await screen.findByLabelText("当前评测制品")).toHaveValue(candidate.artifact_sha256);
    expect(screen.getByText("历史 / 已淘汰候选")).toBeInTheDocument();
  });

  it("sorts candidates with null values last", async () => {
    const user = userEvent.setup();
    const candidates = [
      { ...inspection.candidates[0], candidate_id: "missing", title: "Missing score", selected: false, semantic_score: null },
      { ...inspection.candidates[0], candidate_id: "high", title: "High score", selected: false, semantic_score: 0.9 },
      { ...inspection.candidates[0], candidate_id: "low", title: "Low score", selected: false, semantic_score: 0.2 },
    ];
    render(<CandidateTable candidates={candidates} />);
    await user.selectOptions(screen.getByLabelText("候选排序"), "semantic_score");
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("High score"),
      expect.stringContaining("Low score"),
      expect.stringContaining("Missing score"),
    ]);
  });

  it("reserves green for formal pass states and copies diagnostic identities", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });

    render(
      <>
        <StatusPill value="available">可用</StatusPill>
        <StatusPill value="passed">通过</StatusPill>
        <StatusPill value="blocked">已阻断</StatusPill>
        <dl>
          <IdentityValue label="制品 SHA" value={"a".repeat(64)} />
        </dl>
      </>,
    );

    expect(screen.getByText("可用").closest(".rag-pill")).toHaveAttribute(
      "data-tone",
      "neutral",
    );
    expect(screen.getByText("通过").closest(".rag-pill")).toHaveAttribute(
      "data-tone",
      "success",
    );
    expect(screen.getByText("已阻断").closest(".rag-pill")).toHaveAttribute(
      "data-tone",
      "danger",
    );
    const passedPill = screen.getByText("通过").closest(".rag-pill");
    expect(passedPill.querySelector('[aria-hidden="true"]')).toHaveTextContent(
      "✓",
    );
    await user.click(screen.getByRole("button", { name: "复制制品 SHA" }));
    expect(writeText).toHaveBeenCalledWith("a".repeat(64));
    expect(
      screen.getByRole("button", { name: "复制制品 SHA" }),
    ).toHaveTextContent("已复制");
  });

  it("copies the complete candidate ID and reports clipboard failure", async () => {
    const user = userEvent.setup();
    const writeText = vi
      .fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("clipboard unavailable"));
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
    const candidateId = "candidate-" + "x".repeat(80);

    render(
      <CandidateTable
        candidates={[{
          ...inspection.candidates[0],
          candidate_id: candidateId,
        }]}
      />,
    );

    const candidateCopy = screen.getByRole("button", {
      name: `复制候选 ID ${candidateId}`,
    });
    await user.click(candidateCopy);
    expect(writeText).toHaveBeenNthCalledWith(1, candidateId);
    expect(candidateCopy).toHaveTextContent("已复制");

    await user.click(candidateCopy);
    expect(writeText).toHaveBeenNthCalledWith(2, candidateId);
    expect(candidateCopy).toHaveTextContent("复制失败");
  });

  it("keeps corpus governance read-only and reports unknown lifecycle honestly", async () => {
    routeFetch({ "/api/rag/corpus": { schema_version: "rag-corpus-v1", corpus_version: "memory-p1-zh-v4", manifest_sha256: "a".repeat(64), chunk_count: 1, embedding: { provider: "siliconflow", model: "BAAI/bge-m3", revision: "v1", dimension: 1024 }, activation_status: "not_recorded", retired_versions: [], units: [{ unit_id: "redis-lock", title: "Redis lock", domain: "redis", topic: "locking", source_type: "theory", tags: ["redis"], aliases: ["分布式锁"], source_authority: "not_recorded", review_status: "not_recorded", version: "memory-p1-zh-v4", retirement_status: "not_recorded", embedding_status: "not_recorded", content_sha256: "b".repeat(64) }] } });
    render(<RagCorpusPage />);
    expect(await screen.findByText("知识语料")).toBeInTheDocument();
    expect(screen.getAllByText("未记录").length).toBeGreaterThan(1);
    expect(screen.queryByRole("button", { name: /publish|delete|re-embed|activate|retire|rollback/i })).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("source_url");
  });

  it("validates and creates a new corpus version without persisting its body", async () => {
    const user = userEvent.setup();
    const privateBody = "中".repeat(320);
    const corpus = {
      schema_version: "rag-corpus-v1",
      corpus_version: "memory-p1-zh-v4",
      manifest_sha256: "a".repeat(64),
      chunk_count: 31,
      embedding: { provider: "siliconflow", model: "BAAI/bge-m3", revision: "v1", dimension: 1024 },
      activation_status: "active",
      retired_versions: [],
      write_enabled: true,
      units: [],
    };
    fetch.mockImplementation((url, options = {}) => {
      if (url === "/api/rag/corpus") return response(corpus);
      if (url === "/api/rag/corpus/drafts/validate") {
        const payload = JSON.parse(options.body);
        expect(payload.entry.content).toBe(privateBody);
        expect(payload.corpus_version).toMatch(/console/);
        return response({
          schema_version: "rag-corpus-validation-v2",
          valid: true,
          validation_sha256: "b".repeat(64),
          current_corpus_version: corpus.corpus_version,
          current_manifest_sha256: corpus.manifest_sha256,
          current_chunk_count: 31,
          target_corpus_version: payload.corpus_version,
          target_manifest_sha256: "d".repeat(64),
          target_chunk_count: 32,
          added_chunk_count: 1,
          reused_embedding_count: 31,
          content_sha256: "c".repeat(64),
          chinese_character_count: 320,
          provider_call_required: true,
          estimated_embedding_count: 1,
          provider_name: "siliconflow",
          model_name: "BAAI/bge-m3",
          model_revision: "v1",
          issues: [],
        });
      }
      if (url === "/api/rag/corpus/versions") {
        const payload = JSON.parse(options.body);
        expect(payload.entry.content).toBe(privateBody);
        expect(payload.confirm_create_version).toBe(true);
        expect(payload.expected_target_manifest_sha256).toBe("d".repeat(64));
        return response({
          schema_version: "rag-corpus-version-v1",
          corpus_version: payload.corpus_version,
          manifest_sha256: "d".repeat(64),
          discovered: 32,
          reused: 31,
          embedded: 1,
          activated: 32,
          provider_name: "siliconflow",
          model_name: "BAAI/bge-m3",
          model_revision: "v1",
          dimension: 1024,
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<RagCorpusPage />);
    await user.click(await screen.findByRole("button", { name: "新增资料" }));
    fireEvent.change(screen.getByLabelText(/^知识单元 ID/), { target: { value: "rocketmq_delay_queue" } });
    fireEvent.change(screen.getByLabelText("中文标题"), { target: { value: "RocketMQ 延迟消息实践" } });
    fireEvent.change(screen.getByLabelText("稳定主题标识"), { target: { value: "delay-message" } });
    fireEvent.change(screen.getByLabelText(/^别名/), { target: { value: "延迟队列" } });
    fireEvent.change(screen.getByLabelText("问题 1"), { target: { value: "如何实现延迟消息？" } });
    fireEvent.change(screen.getByLabelText("问题 2"), { target: { value: "延迟消息失败时如何处理？" } });
    fireEvent.change(screen.getByLabelText("来源 1 标题"), { target: { value: "RocketMQ 中文文档" } });
    fireEvent.change(screen.getByLabelText("发布方"), { target: { value: "Apache RocketMQ" } });
    fireEvent.change(screen.getByLabelText("HTTPS 地址"), { target: { value: "https://rocketmq.apache.org/zh/docs/featureBehavior/02delaymessage" } });
    fireEvent.change(screen.getByLabelText(/^正文/), { target: { value: privateBody } });
    await user.click(screen.getByRole("button", { name: /校验并预览/ }));

    expect(await screen.findByText("校验通过，Re-index 预览已生成")).toBeInTheDocument();
    expect(window.location.href).not.toContain(privateBody);
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    await user.click(screen.getByLabelText(/确认创建并启用该语料版本/));
    await user.click(screen.getByRole("button", { name: "创建新版本" }));

    expect(await screen.findByText("新语料版本已创建")).toBeInTheDocument();
    expect(screen.getByText(/复用 31 条向量/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(privateBody);
  });

  it("positions the overview as a technical showcase with honest experiment findings", async () => {
    routeFetch({ "/api/rag/overview": { schema_version: "rag-overview-v2", generated_at: "2026-08-14T00:00:00Z", project_scope: "learning_project_technical_showcase", current_engine: "legacy", comparison_engines: ["legacy", "hybrid-v2"], remote_reranker_enabled: false, evidence_gate_enabled: true, corpus: { version: "v4", manifest_sha256: "a".repeat(64), chunk_count: 31 }, embedding: { provider: "test", model: "test", revision: "v1", dimension: 3 }, profiles: [], component_versions: { fusion: "weighted-rrf-v1" }, capabilities: { console_read: true, live_execution: true, corpus_write: false, access_mode: "loopback" }, technologies: ["Semantic Retrieval", "Lexical Retrieval", "Weighted RRF Fusion"], diagnostic_dataset: { label: "Demo Diagnostic Dataset", curation: "Curated / Machine-assisted", tuning_case_count: 75, diagnostic_case_count: 25, human_annotator_count: 0, production_claim: false }, experiment_findings: ["现有机器辅助诊断制品不能证明 Hybrid 已整体优于 Legacy。", "No-evidence 仍是当前最明确的算法缺口。"], demo_boundaries: ["仅用于本地学习项目与技术展示。", "不包含生产 Shadow、Canary、Promotion 或 Legacy 退役流程。"] } });
    render(<RagOverviewPage />);
    expect(await screen.findByText("学习项目 / 技术展示")).toBeInTheDocument();
    expect(screen.getByText("Demo Diagnostic Dataset")).toBeInTheDocument();
    expect(screen.getByText("Curated / Machine-assisted")).toBeInTheDocument();
    expect(screen.getByText(/不能证明 Hybrid 已整体优于 Legacy/)).toBeInTheDocument();
    expect(screen.getByText(/不包含生产 Shadow、Canary、Promotion/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "打开检索诊断" })).toHaveAttribute("href", "/rag/retrieval");
    expect(screen.queryByText(/允许发布|暂不允许发布/)).not.toBeInTheDocument();
  });
});
