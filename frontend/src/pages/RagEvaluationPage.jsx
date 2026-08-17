import { useCallback, useEffect, useMemo, useState } from "react";
import { RagConsoleShell } from "../components/rag/RagConsoleShell";
import {
  IdentityValue,
  RagState,
  SectionHead,
  StatusPill,
} from "../components/rag/RagPrimitives";
import { usePageMeta } from "../hooks/usePageMeta";
import {
  displayStatus,
  displayCaseType,
  formatMetric,
  metricLabels,
  shortHash,
} from "../rag/ragDisplay";
import {
  getRagEvaluationCases,
  getRagEvaluations,
  getRagNoEvidence,
  getRagPairedEvaluations,
} from "../rag/ragApi";
import { RAG_LAB_ROUTES } from "../rag/ragRoutes";
import { useRagResource } from "../rag/useRagResource";
import "../styles/pages/rag-console.css";

const metricOrder = [
  "recall_at_5",
  "mrr_at_5",
  "ndcg_at_5",
  "hit_at_1",
  "filter_correctness_rate",
  "no_evidence_precision",
  "no_evidence_recall",
  "no_evidence_f1",
  "evidence_replay_stability_rate",
  "p95_latency_ms",
];
const caseMetricOrder = [
  "recall_at_5",
  "mrr_at_5",
  "ndcg_at_5",
  "hit_at_1",
  "evidence_precision_at_5",
  "domain_routing_accuracy",
  "topic_routing_accuracy",
  "no_evidence_precision",
  "no_evidence_recall",
];
const confusionLabels = {
  correct_evidence: "正确返回证据",
  false_abstention: "错误拒答",
  false_evidence: "错误返回证据",
  correct_abstention: "正确拒答",
};

export function RagEvaluationPage() {
  usePageMeta({
    title: "RAG 评测看板",
    description: "对比 Legacy 与候选检索引擎的冻结评测事实。",
    bodyClass: "start-page-body",
  });
  const loader = useCallback((options) => getRagEvaluations(options), []);
  const resource = useRagResource(loader);
  return (
    <RagConsoleShell statusLabel="冻结评测">
      <RagState resource={resource}>
        {(data) => <EvaluationContent artifacts={data.artifacts} />}
      </RagState>
    </RagConsoleShell>
  );
}

function EvaluationContent({ artifacts }) {
  const tuning = artifacts.filter((item) => item.split === "tuning");
  const primaryTuning = tuning.find(
    (item) => !item.engine_version.includes("rank-normalized"),
  );
  const [selected, setSelected] = useState(
    primaryTuning?.artifact_sha256 ||
      tuning[0]?.artifact_sha256 ||
      artifacts[0]?.artifact_sha256 ||
      "",
  );
  const [cases, setCases] = useState({
    status: "idle",
    data: null,
    error: null,
  });
  const [noEvidence, setNoEvidence] = useState(null);
  const [paired, setPaired] = useState([]);
  const [caseTypeFilter, setCaseTypeFilter] = useState("all");

  useEffect(() => {
    const controller = new AbortController();
    getRagPairedEvaluations({ signal: controller.signal })
      .then((data) => setPaired(data.comparisons))
      .catch((error) => {
        if (error?.code !== "REQUEST_ABORTED") setPaired([]);
      });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!selected) return undefined;
    const controller = new AbortController();
    setCases({ status: "loading", data: null, error: null });
    setNoEvidence(null);
    Promise.all([
      getRagEvaluationCases(selected, { signal: controller.signal }),
      getRagNoEvidence(selected, { signal: controller.signal }),
    ])
      .then(([data, summary]) => {
        setCases({ status: "success", data, error: null });
        setNoEvidence(summary);
      })
      .catch((error) => {
        if (error?.code !== "REQUEST_ABORTED")
          setCases({ status: "error", data: null, error });
      });
    return () => controller.abort();
  }, [selected]);

  const active = artifacts.find((item) => item.artifact_sha256 === selected);
  const engines = useMemo(
    () =>
      artifacts.filter(
        (item) =>
          item.dataset_version === active?.dataset_version &&
          item.split === active?.split,
      ),
    [artifacts, active],
  );
  const activePairs = paired.filter(
    (item) =>
      item.baseline_artifact_sha256 === selected ||
      item.candidate_artifact_sha256 === selected,
  );
  const caseTypes = Object.keys(active?.metrics.case_type_breakdown || {});
  const visibleCases = (cases.data?.cases || []).filter(
    (item) => caseTypeFilter === "all" || item.case_type === caseTypeFilter,
  );
  const historical = artifacts.filter(
    (item) => item.diagnostic_status === "historical_compatible",
  );
  const mainEngines = engines.filter(
    (item) => !item.engine_version.includes("rank-normalized"),
  );
  const rejectedEngines = engines.filter((item) =>
    item.engine_version.includes("rank-normalized"),
  );

  return (
    <>
      <header className="rag-page-head">
        <div>
          <p>Demo Diagnostic Dataset · Curated / Machine-assisted</p>
          <h1>RAG 诊断评测</h1>
          <span>
            用冻结制品比较不同检索引擎，观察指标、案例类型与无证据表现。
          </span>
        </div>
        <StatusPill value="diagnostic">工程对比</StatusPill>
      </header>
      {active && (
        <section className="rag-artifact-hero">
          <div>
            <small>数据集 / 分区</small>
            <strong>{active.dataset_version}</strong>
            <span>
              {displayStatus(active.split)} · {displayStatus(active.diagnostic_status)}
            </span>
          </div>
          <div>
            <small>Benchmark 类型</small>
            <strong>Demo Diagnostic Dataset</strong>
            <span>{displayStatus(active.label_source)}</span>
          </div>
          <div>
            <small>用途</small>
            <strong>工程对比</strong>
            <span>本地学习与技术展示</span>
          </div>
          <label>
            <span>当前评测制品</span>
            <select
              value={selected}
              onChange={(event) => setSelected(event.target.value)}
            >
              {artifacts.map((item) => (
                <option key={item.artifact_sha256} value={item.artifact_sha256}>
                  {item.engine_version} · {displayStatus(item.split)}
                </option>
              ))}
            </select>
          </label>
        </section>
      )}
      {active && (
        <section className="rag-panel">
          <SectionHead
            eyebrow="00 · 指标解释前先确认身份"
            title="冻结执行身份"
          />
          <dl className="rag-detail-list">
            <IdentityValue
              label="制品 SHA"
              value={active.artifact_sha256}
              copy
            />
            <IdentityValue
              label="语料清单"
              value={active.corpus_manifest_sha256}
              copy
            />
            <IdentityValue
              label="向量模型"
              value={`${active.embedding_provider} / ${active.embedding_model} / ${active.embedding_revision} / ${active.embedding_dimension}`}
            />
            <IdentityValue label="检索引擎" value={active.engine_version} />
            <IdentityValue
              label="检索配置"
              value={`${active.profile_id}@${active.profile_version}`}
            />
            <IdentityValue
              label="配置 SHA"
              value={active.profile_sha256}
              copy
            />
            <IdentityValue
              label="代码版本"
              value={active.code_revision}
              copy
            />
            <IdentityValue
              label="代码树 SHA"
              value={active.code_tree_sha256}
              copy
            />
            <IdentityValue label="冻结时间" value={active.created_at} />
            <IdentityValue
              label="诊断完整度"
              value={displayStatus(active.diagnostic_fidelity)}
            />
          </dl>
        </section>
      )}
      {historical.length > 0 && (
        <div className="rag-mode-notice" data-mode="replay">
          <strong>历史兼容的最终诊断集</strong>
          <span>
            {historical[0].case_count} 个案例保留用于最终诊断与历史回放，不参与反复调参。
          </span>
        </div>
      )}
      <section className="rag-panel">
        <SectionHead
          eyebrow="01 · 引擎矩阵"
          title="Legacy 与检索引擎对比"
          aside={
            <span className="rag-count">{paired.length} 组配对评测</span>
          }
        />
        <MetricTable engines={mainEngines} />
        {activePairs.length > 0 && (
          <div className="rag-paired-deltas">
            <h3>冻结诊断差值</h3>
            {activePairs.map((pair) => (
              <article key={pair.artifact_sha256}>
                <header>
                  <strong>
                    {pair.baseline_engine_version} →{" "}
                    {pair.candidate_engine_version}
                  </strong>
                  <div>
                    <StatusPill value={pair.comparison_status}>
                      诊断比较
                    </StatusPill>
                    <code>{shortHash(pair.artifact_sha256)}</code>
                  </div>
                </header>
                <dl>
                  {pair.metrics
                    .filter((item) => metricOrder.includes(item.metric))
                    .map((item) => (
                      <div key={item.metric}>
                        <dt>{metricLabels[item.metric] || item.metric}</dt>
                        <dd
                          data-direction={
                            item.delta > 0
                              ? "up"
                              : item.delta < 0
                                ? "down"
                                : "flat"
                          }
                        >
                          {item.delta > 0 ? "+" : ""}
                          {formatMetric(item.metric, item.delta)}
                        </dd>
                      </div>
                    ))}
                </dl>
              </article>
            ))}
          </div>
        )}
        {rejectedEngines.length > 0 && (
          <details className="rag-historical">
            <summary>历史 / 已淘汰候选</summary>
            <MetricTable engines={rejectedEngines} />
          </details>
        )}
      </section>
      <div className="rag-grid">
        <section className="rag-panel">
          <SectionHead
            eyebrow="02 · 无证据"
            title="无证据混淆矩阵"
            aside={<StatusPill value="available">服务端计算</StatusPill>}
          />
          {noEvidence ? (
            <>
              <div className="rag-confusion">
                {[
                  "correct_evidence",
                  "false_abstention",
                  "false_evidence",
                  "correct_abstention",
                ].map((key) => (
                  <div key={key}>
                    <small>{confusionLabels[key]}</small>
                    <strong>{noEvidence[key]}</strong>
                  </div>
                ))}
              </div>
              <dl className="rag-detail-list">
                <IdentityValue
                  label="无证据占比"
                  value={formatMetric(
                    "rate",
                    noEvidence.no_evidence_prevalence,
                  )}
                />
                <IdentityValue
                  label="拒答率"
                  value={formatMetric("rate", noEvidence.abstention_rate)}
                />
                <IdentityValue
                  label="精确率"
                  value={formatMetric("rate", noEvidence.precision)}
                />
                <IdentityValue
                  label="召回率"
                  value={formatMetric("rate", noEvidence.recall)}
                />
                <IdentityValue
                  label="F1"
                  value={formatMetric("rate", noEvidence.f1)}
                />
                <IdentityValue
                  label="错误拒答案例"
                  value={
                    noEvidence.false_abstention_case_ids?.join(" · ") || "无"
                  }
                />
                <IdentityValue
                  label="错误取证案例"
                  value={
                    noEvidence.false_evidence_case_ids?.join(" · ") || "无"
                  }
                />
                <IdentityValue
                  label="正确拒答案例"
                  value={
                    noEvidence.correct_abstention_case_ids?.join(" · ") || "无"
                  }
                />
                <IdentityValue
                  label="原因代码分布"
                  value={
                    Object.entries(noEvidence.reason_code_breakdown || {})
                      .map(([code, count]) => `${code} × ${count}`)
                      .join(" · ") || "未记录"
                  }
                />
              </dl>
            </>
          ) : (
            <p>暂无数据</p>
          )}
          <p className="rag-footnote">
            所有计数与派生率由服务端使用 Eval Ground Truth 和冻结
            declared_no_evidence 计算。
          </p>
        </section>
        <section className="rag-panel">
          <SectionHead
            eyebrow="03 · 案例类型"
            title="哪些类型发生变化"
          />
          <div className="rag-case-types">
            {Object.entries(active?.metrics.case_type_breakdown || {}).map(
              ([type, metrics]) => (
                <article key={type}>
                  <header>
                    <strong>{displayCaseType(type)}</strong>
                    <span>{metrics.case_count} 个案例</span>
                  </header>
                  <dl>
                    {caseMetricOrder.map((metric) => (
                      <div key={metric}>
                        <dt>{metricLabels[metric] || metric}</dt>
                        <dd>{formatMetric(metric, metrics[metric])}</dd>
                      </div>
                    ))}
                  </dl>
                </article>
              ),
            )}
          </div>
        </section>
      </div>
      <section className="rag-panel">
        <SectionHead
          eyebrow="04 · 案例明细"
          title="冻结案例结果"
          aside={
            <label className="rag-search">
              <span>案例类型</span>
              <select
                value={caseTypeFilter}
                onChange={(event) => setCaseTypeFilter(event.target.value)}
              >
                <option value="all">全部类型</option>
                {caseTypes.map((type) => (
                  <option key={type} value={type}>
                    {displayCaseType(type)}
                  </option>
                ))}
              </select>
            </label>
          }
        />
        {cases.error && (
          <div className="rag-inline-error" role="alert">
            {cases.error.message}
          </div>
        )}
        {cases.status === "loading" ? (
          <p className="rag-loading-line">正在读取案例…</p>
        ) : (
          <div className="rag-table-wrap">
            <table className="rag-table">
              <thead>
                <tr>
                  <th>案例 / 类型</th>
                  <th>结果</th>
                  <th>相关证据 ID</th>
                  <th>排除证据 ID</th>
                  <th>失败分类</th>
                  <th>诊断完整度</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {visibleCases.map((item) => (
                  <tr key={item.case_id}>
                    <td>
                      <code>{item.case_id}</code>
                      <small>
                        {displayCaseType(item.case_type)} · {item.evaluation_group}
                      </small>
                    </td>
                    <td>
                      <StatusPill value={item.availability} />
                      <small>
                        {item.selected_evidence_ids.length} 条证据 ·{" "}
                        {item.latency_ms.toFixed(1)} ms
                      </small>
                    </td>
                    <td>
                      <small>
                        主要相关：{" "}
                        {item.primary_relevant_chunk_ids.join(", ") || "无"}
                      </small>
                      <small>
                        可接受相关：{" "}
                        {item.accepted_related_chunk_ids.join(", ") || "无"}
                      </small>
                    </td>
                    <td>
                      <small>
                        {item.excluded_chunk_ids.join(", ") || "无"}
                      </small>
                    </td>
                    <td>
                      <small>
                        {item.reason_codes.join(" · ") ||
                          (item.declared_no_evidence
                            ? "标注为无证据"
                            : "未记录失败原因")}
                      </small>
                    </td>
                    <td>
                      <StatusPill value={item.diagnostic_fidelity} />
                      {item.diagnostic_fidelity === "partial_historical" && (
                        <small>缺失阶段不会被重建。</small>
                      )}
                    </td>
                    <td>
                      <a
                        className="rag-link-button"
                        href={`${RAG_LAB_ROUTES.retrieval}?artifact=${encodeURIComponent(selected)}&case=${encodeURIComponent(item.case_id)}&mode=artifact_replay`}
                      >
                        在检索诊断中打开冻结回放
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function MetricTable({ engines }) {
  return (
    <div className="rag-table-wrap">
      <table className="rag-table rag-metric-table">
        <thead>
          <tr>
            <th>指标</th>
            {engines.map((item) => (
              <th key={item.artifact_sha256}>
                {item.engine_version}
                <small>{shortHash(item.artifact_sha256)}</small>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {metricOrder.map((metric) => (
            <tr key={metric}>
              <td>
                <strong>{metricLabels[metric]}</strong>
              </td>
              {engines.map((item) => (
                <td key={item.artifact_sha256}>
                  {formatMetric(metric, item.metrics[metric])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
