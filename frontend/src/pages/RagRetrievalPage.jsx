import { useEffect, useRef, useState } from "react";
import {
  CandidateTable,
  IdentityValue,
  SectionHead,
  StatusPill,
} from "../components/rag/RagPrimitives";
import { RagConsoleShell } from "../components/rag/RagConsoleShell";
import { usePageMeta } from "../hooks/usePageMeta";
import {
  getRagReplay,
  runRagComparison,
  runRagInspection,
} from "../rag/ragApi";
import { displayFieldLabel, displayStatus } from "../rag/ragDisplay";
import "../styles/pages/rag-console.css";

const evidenceDimensions = [
  "availability",
  "sufficiency",
  "consistency",
  "evaluation_confidence",
];
const profileFields = [
  "semantic_enabled",
  "lexical_enabled",
  "fusion_strategy",
  "semantic_weight",
  "lexical_weight",
  "semantic_candidate_limit",
  "lexical_candidate_limit",
  "fusion_candidate_limit",
  "rerank_candidate_limit",
  "evidence_limit",
  "semantic_timeout_ms",
  "lexical_timeout_ms",
  "rerank_timeout_ms",
  "total_timeout_ms",
];

const sideStatusLabel = {
  success: "完成",
  failed: "执行失败",
  timeout: "执行超时",
};

function stageText(candidate) {
  if (!candidate) return "未进入候选集";
  const value = (rank, score) => `${rank ?? "—"} / ${score ?? "—"}`;
  return [
    `语义 ${value(candidate.semantic_rank, candidate.semantic_score)}`,
    `词法 ${value(candidate.lexical_rank, candidate.lexical_score)}`,
    `融合 ${value(candidate.fusion_rank, candidate.fusion_score)}`,
    `重排 ${value(candidate.rerank_rank, candidate.rerank_score)}`,
  ];
}

function CompareResult({ comparison, onInspect }) {
  const legacy = comparison.legacy?.inspection;
  const hybrid = comparison.hybrid?.inspection;
  const legacyCandidates = new Map(
    (legacy?.candidates || []).map((candidate) => [candidate.candidate_id, candidate]),
  );
  const hybridCandidates = new Map(
    (hybrid?.candidates || []).map((candidate) => [candidate.candidate_id, candidate]),
  );
  const selectedIds = (inspection) => (inspection?.candidates || [])
    .filter((candidate) => candidate.selected)
    .map((candidate) => candidate.candidate_id);
  const decisionText = (inspection) => inspection?.evidence_decision?.reason_codes
    ?.join(" · ") || "未记录原因";
  const changedLabel = (value) => value == null
    ? "无法比较"
    : value
      ? "发生变化"
      : "保持一致";

  return (
    <section className="rag-panel rag-compare-result">
      <SectionHead
        eyebrow="00 · 双引擎现场对比"
        title="同一问题的服务端差异"
        aside={<StatusPill value="diagnostic_only" />}
      />
      <div className="rag-compare-metrics">
        <div>
          <small>Top-5 重合</small>
          <strong>
            {comparison.top_k_overlap
              ? `${comparison.top_k_overlap.overlap_count} / ${comparison.top_k_overlap.k}`
              : "无法比较"}
          </strong>
        </div>
        <div>
          <small>最终证据</small>
          <strong>{changedLabel(comparison.selected_evidence_changed)}</strong>
        </div>
        <div>
          <small>证据门禁</small>
          <strong>{changedLabel(comparison.evidence_decision_changed)}</strong>
        </div>
        <div>
          <small>Hybrid 延迟差</small>
          <strong>
            {comparison.latency_delta_ms == null
              ? "无法比较"
              : `${comparison.latency_delta_ms > 0 ? "+" : ""}${comparison.latency_delta_ms} ms`}
          </strong>
        </div>
      </div>
      <div className="rag-compare-sides">
        {[
          ["Legacy", comparison.legacy, legacy],
          ["Hybrid V2", comparison.hybrid, hybrid],
        ].map(([label, side, inspection]) => (
          <article key={label} data-status={side?.status}>
            <header>
              <strong>{label}</strong>
              <span>{sideStatusLabel[side?.status] || "未运行"}</span>
            </header>
            {inspection ? (
              <>
                <p>
                  最终证据：{selectedIds(inspection).join(" · ") || "未选择证据"}
                </p>
                <small>门禁原因：{decisionText(inspection)}</small>
              </>
            ) : (
              <p>该侧没有可比较结果；另一侧结果仍然保留。</p>
            )}
          </article>
        ))}
      </div>
      {comparison.rank_changes?.length > 0 && (
        <div className="rag-table-wrap">
          <table className="rag-table rag-compare-table">
            <thead>
              <tr>
                <th>候选资料</th>
                <th>Legacy：语义 / 词法 / 融合 / 重排</th>
                <th>Hybrid：语义 / 词法 / 融合 / 重排</th>
                <th>最终排名变化</th>
              </tr>
            </thead>
            <tbody>
              {comparison.rank_changes.map((change) => {
                const legacyCandidate = legacyCandidates.get(change.candidate_id);
                const hybridCandidate = hybridCandidates.get(change.candidate_id);
                const candidate = hybridCandidate || legacyCandidate;
                return (
                  <tr key={change.candidate_id}>
                    <td>
                      <strong>{candidate?.title || change.candidate_id}</strong>
                      <code>{change.candidate_id}</code>
                      <button
                        type="button"
                        className="rag-link-button"
                        onClick={(event) => onInspect(candidate, event.currentTarget)}
                      >
                        查看候选解释
                      </button>
                    </td>
                    <td>
                      {stageText(legacyCandidate).map((line) => <small key={line}>{line}</small>)}
                      <em>{change.legacy_selected ? "已选为证据" : "未选为证据"}</em>
                    </td>
                    <td>
                      {stageText(hybridCandidate).map((line) => <small key={line}>{line}</small>)}
                      <em>{change.hybrid_selected ? "已选为证据" : "未选为证据"}</em>
                    </td>
                    <td>
                      <strong>
                        {change.rank_delta == null
                          ? "仅一侧出现"
                          : change.rank_delta === 0
                            ? "排名不变"
                            : change.rank_delta > 0
                              ? `Hybrid 上升 ${change.rank_delta} 位`
                              : `Hybrid 下降 ${Math.abs(change.rank_delta)} 位`}
                      </strong>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="rag-footnote">
        两侧共享同一条问题、约束和当前语料身份；比较结果不写入面试会话，
        也不会生成生产 Shadow 记录。问题原文不包含在响应中。
      </p>
    </section>
  );
}

export function RagRetrievalPage() {
  usePageMeta({
    title: "检索诊断",
    description: "逐阶段检查 RAG 候选排序与证据决策。",
    bodyClass: "start-page-body",
  });
  const [query, setQuery] = useState("");
  const [runMode, setRunMode] = useState("compare");
  const [engine, setEngine] = useState("hybrid-v2");
  const [state, setState] = useState({
    status: "idle",
    data: null,
    error: null,
  });
  const [active, setActive] = useState(null);
  const requestRef = useRef(null);
  const closeButtonRef = useRef(null);
  const explainTriggerRef = useRef(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sha = params.get("artifact");
    const caseId = params.get("case");
    if (!sha || !caseId) return undefined;
    const controller = new AbortController();
    requestRef.current = controller;
    setState({ status: "loading", data: null, error: null });
    getRagReplay(sha, caseId, { signal: controller.signal })
      .then((data) => setState({ status: "success", data, error: null }))
      .catch((error) => {
        if (error?.code !== "REQUEST_ABORTED")
          setState({ status: "error", data: null, error });
      });
    return () => controller.abort();
  }, []);

  useEffect(() => () => requestRef.current?.abort(), []);

  const submit = async (event) => {
    event.preventDefault();
    const controller = new AbortController();
    requestRef.current?.abort();
    requestRef.current = controller;
    setState({ status: "loading", data: null, error: null });
    try {
      const payload = {
        query_text: query,
        profile_id: "question-review",
        intent: "question_review",
      };
      const data = runMode === "compare"
        ? await runRagComparison(payload, {
          timeoutMs: 12000,
          signal: controller.signal,
        })
        : await runRagInspection(
          { ...payload, engine },
          { timeoutMs: 10000, signal: controller.signal },
        );
      setState({ status: "success", data, error: null });
    } catch (error) {
      if (error?.code !== "REQUEST_ABORTED")
        setState({ status: "error", data: null, error });
      else setState({ status: "idle", data: null, error: null });
    }
  };

  const cancel = () => requestRef.current?.abort();
  const clear = () => {
    requestRef.current?.abort();
    setQuery("");
    setState({ status: "idle", data: null, error: null });
  };
  const comparison = state.data?.schema_version === "rag-retrieval-compare-v1"
    ? state.data
    : null;
  const data = comparison
    ? comparison.hybrid?.inspection || comparison.legacy?.inspection
    : state.data;
  const replay = data?.mode === "artifact_replay";

  useEffect(() => {
    if (!active) return undefined;
    closeButtonRef.current?.focus();
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        setActive(null);
        explainTriggerRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [active]);

  const openCandidate = (candidate, trigger) => {
    explainTriggerRef.current = trigger;
    setActive(candidate);
  };
  const closeCandidate = () => {
    setActive(null);
    explainTriggerRef.current?.focus();
  };

  return (
    <RagConsoleShell
      statusLabel={
        comparison
          ? "Legacy / Hybrid · 对比完成"
          : data
            ? `${data.engine} · ${displayStatus(data.diagnostic_fidelity)}`
            : "检索器待命"
      }
      statusTone={state.status === "error" ? "error" : "ready"}
    >
      <header className="rag-page-head rag-page-head-compact">
        <div>
          <p>查询 → 双通道 → 融合 → 重排 → 证据</p>
          <h1>检索诊断</h1>
          <span>
            Live query 只存在于当前页面内存和 POST body；响应不会回显原文。
          </span>
        </div>
        {data && (
          <StatusPill value={data.diagnostic_fidelity} />
        )}
      </header>
      <div className="rag-mode-notice" data-mode={replay ? "replay" : "live"}>
        <strong>{replay ? "冻结制品回放" : "实时诊断"}</strong>
        <span>
          {replay
            ? "不调用模型服务，结果直接读取自不可变的评测制品与快照。"
            : "可能调用当前向量服务并产生费用。"}
        </span>
      </div>
      {!replay && (
        <form className="rag-query-bar" onSubmit={submit}>
          <label>
            <span>诊断问题</span>
            <textarea
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="输入需要检查的技术问题…"
              required
            />
          </label>
          <label>
            <span>诊断方式</span>
            <select
              value={runMode}
              onChange={(event) => setRunMode(event.target.value)}
            >
              <option value="compare">Legacy / Hybrid 对比</option>
              <option value="single">单引擎诊断</option>
            </select>
          </label>
          {runMode === "single" && (
            <label>
              <span>检索引擎</span>
              <select
                value={engine}
                onChange={(event) => setEngine(event.target.value)}
              >
                <option value="hybrid-v2">Hybrid V2</option>
                <option value="legacy">Legacy</option>
              </select>
            </label>
          )}
          <button type="submit" disabled={state.status === "loading"}>
            {state.status === "loading"
              ? "正在运行…"
              : runMode === "compare"
                ? "比较两种引擎"
                : "开始诊断"}
          </button>
          {state.status === "loading" && (
            <button type="button" className="rag-secondary" onClick={cancel}>
              取消
            </button>
          )}
          <button type="button" className="rag-secondary" onClick={clear}>
            清空
          </button>
        </form>
      )}
      {state.error && (
        <div className="rag-inline-error" role="alert">
          <strong>检索诊断不可用</strong>
          <span>{state.error.message}</span>
          {state.error.requestId && <code>{state.error.requestId}</code>}
        </div>
      )}
      {!data && state.status === "idle" && (
        <div className="rag-inspector-empty">
          <span>5</span>
          <div>
            <strong>五阶段证据路径</strong>
            <p>运行实时诊断，或从评测看板打开冻结回放。</p>
          </div>
        </div>
      )}
      {data && (
        <>
          {comparison && (
            <CompareResult comparison={comparison} onInspect={openCandidate} />
          )}
          <section className="rag-identity-strip">
            <IdentityValue label="模式" value={displayStatus(data.mode)} />
            <IdentityValue label="检索引擎" value={data.engine} />
            <IdentityValue
              label="检索配置"
              value={`${data.profile_id}@${data.profile_version}`}
            />
            <IdentityValue
              label="Trace 结构版本"
              value={data.trace_schema_version}
            />
            <IdentityValue
              label="问题摘要"
              value={data.query_facts.query_sha256 || "未记录"}
              hash
            />
            <IdentityValue
              label="总耗时"
              value={
                data.latency_ms.total == null
                  ? "未记录"
                  : `${data.latency_ms.total} ms`
              }
            />
          </section>
          {replay && (
            <section className="rag-panel">
              <SectionHead
                eyebrow="00 · 冻结身份"
                title="评测制品身份"
              />
              <dl className="rag-detail-list">
                {Object.entries(data.artifact_identity || {}).map(
                  ([key, value]) => (
                    <IdentityValue key={key} label={displayFieldLabel(key)} value={value} />
                  ),
                )}
              </dl>
              {data.diagnostic_fidelity === "partial_historical" && (
                <p className="rag-footnote">
                  历史回放只包含原始评测制品已经冻结的字段；语义、词法、融合、
                  证据门禁和解释字段可能不可用，界面不会自行重建。
                </p>
              )}
            </section>
          )}
          <div className="rag-grid">
            <section className="rag-panel">
              <SectionHead
                eyebrow="01 · 当前信号"
                title="安全请求事实"
              />
              <dl className="rag-detail-list">
                {Object.entries(data.inspection_inputs || {}).map(
                  ([key, value]) => (
                    <IdentityValue
                      key={key}
                      label={displayFieldLabel(key)}
                      value={
                        Array.isArray(value)
                          ? value.join(" · ") || "无"
                          : value
                      }
                    />
                  ),
                )}
                {Object.entries(data.routing_summary || {}).map(
                  ([key, value]) => (
                    <IdentityValue
                      key={`routing-${key}`}
                      label={`路由：${displayFieldLabel(key)}`}
                      value={value}
                    />
                  ),
                )}
              </dl>
              <p className="rag-footnote">
                当前接口未提供问题信号分类、动态权重、分类器置信度与动态路由原因；
                界面不会推断或补造这些信息。
              </p>
            </section>
            <section className="rag-panel">
              <SectionHead
                eyebrow="02 · 生效配置"
                title="检索策略"
              />
              <dl className="rag-detail-list">
                {profileFields.map((key) => (
                  <IdentityValue
                    key={key}
                    label={displayFieldLabel(key)}
                    value={data.resolved_profile?.[key] ?? "未记录"}
                  />
                ))}
              </dl>
            </section>
          </div>
          <section className="rag-panel">
            <SectionHead
              eyebrow="03 · 检索流水线"
              title="候选排名"
              aside={
                <span className="rag-count">
                  {data.candidates.length} 个候选项
                </span>
              }
            />
            <CandidateTable
              candidates={data.candidates}
              onInspect={openCandidate}
              activeCandidateId={active?.candidate_id}
            />
          </section>
          <div className="rag-grid">
            <section className="rag-panel">
              <SectionHead eyebrow="04 · 证据门禁" title="证据决策" />
              <div className="rag-decision-grid">
                {evidenceDimensions.map((key) => (
                  <div key={key}>
                    <small>{displayFieldLabel(key)}</small>
                    <StatusPill value={data.evidence_decision?.[key]} />
                  </div>
                ))}
              </div>
              <dl className="rag-detail-list">
                <IdentityValue
                  label="已覆盖信号"
                  value={
                    data.evidence_decision?.covered_signals?.join(" · ") ||
                    "未记录"
                  }
                />
                <IdentityValue
                  label="缺失信号"
                  value={
                    data.evidence_decision?.missing_signals?.join(" · ") ||
                    "未记录"
                  }
                />
                <IdentityValue
                  label="原因代码"
                  value={
                    data.evidence_decision?.reason_codes?.join(" · ") ||
                    "未记录"
                  }
                />
                <IdentityValue
                  label="门禁版本"
                  value={data.evidence_decision?.gate_version || "未记录"}
                />
              </dl>
              <div className="rag-consumer">
                <small>
                  消费端动作 · {displayStatus(data.consumer_action.recording_status)}
                </small>
                <strong>{data.consumer_action.public_message}</strong>
              </div>
            </section>
            <section className="rag-panel">
              <SectionHead eyebrow="05 · 耗时" title="各阶段实际耗时" />
              <div className="rag-latency">
                {Object.entries(data.latency_ms).map(([key, value]) => (
                  <div key={key}>
                    <span>{displayFieldLabel(key)}</span>
                    <strong>
                      {value == null
                        ? "未记录"
                        : `${Number(value).toFixed(2)} ms`}
                    </strong>
                  </div>
                ))}
              </div>
              <p className="rag-footnote">
                Semantic 与 Lexical 可能并行；阶段值不能相加推导
                Total，也不会通过相减补造缺失阶段。
              </p>
            </section>
          </div>
          {active && (
            <div className="rag-drawer-backdrop" onClick={closeCandidate}>
              <aside
                className="rag-drawer"
                role="dialog"
                aria-modal="true"
                aria-labelledby="rag-candidate-title"
                onClick={(event) => event.stopPropagation()}
              >
                <button
                  ref={closeButtonRef}
                  type="button"
                  onClick={closeCandidate}
                >
                  关闭
                </button>
                <p>候选排序解释</p>
                <h2 id="rag-candidate-title">{active.title}</h2>
                <code>{active.candidate_id}</code>
                <dl className="rag-detail-list">
                  <IdentityValue
                    label="语义排名 / 分数"
                    value={`${active.semantic_rank ?? "—"} / ${active.semantic_score ?? "未记录"}`}
                  />
                  <IdentityValue
                    label="词法排名 / 分数"
                    value={`${active.lexical_rank ?? "—"} / ${active.lexical_score ?? "未记录"}`}
                  />
                  <IdentityValue
                    label="融合排名 / 分数"
                    value={`${active.fusion_rank ?? "—"} / ${active.fusion_score ?? "未记录"}`}
                  />
                  <IdentityValue
                    label="重排排名 / 分数"
                    value={`${active.rerank_rank ?? "—"} / ${active.rerank_score ?? "未记录"}`}
                  />
                  <IdentityValue
                    label="选用状态"
                    value={active.selected ? "已选用" : "未选用"}
                  />
                  {Object.entries(active.ranking_explanation || {}).map(
                    ([key, value]) => (
                      <IdentityValue
                        key={key}
                        label={displayFieldLabel(key)}
                        value={
                          Array.isArray(value)
                            ? value.join(", ")
                            : (value ?? "未记录")
                        }
                      />
                    ),
                  )}
                </dl>
                {!active.ranking_explanation && (
                  <p>当前评测制品结构未记录排序解释。</p>
                )}
              </aside>
            </div>
          )}
        </>
      )}
    </RagConsoleShell>
  );
}
