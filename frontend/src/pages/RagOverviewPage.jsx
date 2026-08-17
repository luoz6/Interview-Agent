import { useCallback } from "react";
import { RagConsoleShell } from "../components/rag/RagConsoleShell";
import {
  IdentityValue,
  RagState,
  SectionHead,
  StatusPill,
} from "../components/rag/RagPrimitives";
import { usePageMeta } from "../hooks/usePageMeta";
import { getRagOverview } from "../rag/ragApi";
import { useRagResource } from "../rag/useRagResource";
import { displayFieldLabel, displayStatus } from "../rag/ragDisplay";
import { RAG_LAB_ROUTES } from "../rag/ragRoutes";
import "../styles/pages/rag-console.css";

const blockerFindings = {
  HUMAN_TUNING_GT_MISSING: "当前结论来自机器预标注，尚未形成独立人工标注结论。",
  NO_EVIDENCE_GATE_FAILED: "无证据判断仍未达到发布要求，是当前最明确的算法缺口。",
  HYBRID_NOT_BETTER_THAN_LEGACY: "现有诊断制品不能证明 Hybrid 已整体优于 Legacy。",
  SEALED_HOLDOUT_MISSING: "当前只有历史诊断结果，不包含正式封存测试集结论。",
  BUSINESS_BLIND_AB_PENDING: "Reviewer 与 Follow-up 的盲测尚未完成。",
  SHADOW_NOT_AUTHORIZED: "Shadow 当前未启用，也不属于本地学习演示范围。",
};

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function displayOverviewValue(value) {
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string") return displayStatus(value);
  return value;
}

function normalizeRagOverview(payload = {}) {
  const componentVersions = payload.component_versions || {};
  const profiles = asArray(payload.profiles);
  const releaseEvidence = payload.release_evidence || {};
  const blockers = asArray(payload.promotion?.blockers);
  const comparisonEngines = asArray(payload.comparison_engines).length
    ? payload.comparison_engines
    : [payload.formal_engine, payload.candidate_engine].filter(Boolean);
  const technologies = asArray(payload.technologies).length
    ? payload.technologies
    : [
        profiles.some((profile) => profile.semantic_enabled) ? "语义检索" : null,
        profiles.some((profile) => profile.lexical_enabled) ? "词法检索" : null,
        componentVersions.fusion ? `融合：${componentVersions.fusion}` : null,
        componentVersions.reranker ? `重排：${componentVersions.reranker}` : null,
      ].filter(Boolean);
  const diagnosticDataset = payload.diagnostic_dataset || {
    annotation_status: releaseEvidence.annotation_status || "not_recorded",
    human_annotator_count: releaseEvidence.human_annotator_count ?? 0,
    independent_evidence_eligible:
      releaseEvidence.independent_evidence_eligible === true,
    holdout_status: releaseEvidence.holdout_status || "not_recorded",
    formal_sealed_holdout_available:
      releaseEvidence.formal_sealed_holdout_available === true,
  };
  const experimentFindings = asArray(payload.experiment_findings).length
    ? payload.experiment_findings
    : blockers.map(
        (blocker) =>
          blockerFindings[blocker.code]
          || blocker.observed_evidence
          || `仍需处理：${blocker.code}`,
      );

  return {
    ...payload,
    current_engine: payload.current_engine || payload.formal_engine || "未记录",
    comparison_engines: comparisonEngines,
    corpus: payload.corpus || {},
    embedding: payload.embedding || {},
    profiles,
    component_versions: componentVersions,
    technologies,
    diagnostic_dataset: diagnosticDataset,
    experiment_findings: experimentFindings,
    demo_boundaries: asArray(payload.demo_boundaries).length
      ? payload.demo_boundaries
      : [
          "仅用于本地学习、检索诊断与资料管理。",
          "当前结果不代表生产发布、Shadow、Canary 或 Legacy 退役结论。",
        ],
  };
}

export function RagOverviewPage() {
  usePageMeta({
    title: "RAG 工程概览",
    description: "当前检索技术、实验事实与本地演示入口。",
    bodyClass: "start-page-body",
  });
  const loader = useCallback((options) => getRagOverview(options), []);
  const resource = useRagResource(loader);
  return (
    <RagConsoleShell statusLabel="本地技术演示">
      <RagState resource={resource}>
        {(payload) => {
          const data = normalizeRagOverview(payload);
          return (
          <>
            <header className="rag-page-head">
              <div>
                <p>技术组成 · 运行状态 · 实验事实</p>
                <h1>RAG 工程概览</h1>
                <span>
                  这里说明当前用了什么、怎样工作，以及已有实验真正证明了什么。
                  Legacy 与 Hybrid 的差异请直接进入检索诊断现场比较。
                </span>
              </div>
              <div className="rag-runtime-stamp" data-state="ready">
                <small>项目定位</small>
                <strong>学习项目 / 技术展示</strong>
                <span>本地诊断，不代表线上结论</span>
              </div>
            </header>
            <section className="rag-identity-strip">
              <IdentityValue label="当前业务引擎" value={data.current_engine} />
              <IdentityValue
                label="可比较引擎"
                value={data.comparison_engines.join(" / ")}
              />
              <IdentityValue label="语料版本" value={data.corpus.version} />
              <IdentityValue label="资料数量" value={`${data.corpus.chunk_count} 条`} />
              <IdentityValue
                label="清单摘要"
                value={data.corpus.manifest_sha256}
                hash
              />
              <IdentityValue
                label="向量模型"
                value={`${data.embedding.provider} / ${data.embedding.model}`}
              />
              <IdentityValue
                label="远程重排器"
                value={data.remote_reranker_enabled ? "已启用" : "未启用"}
              />
              <IdentityValue
                label="证据门禁"
                value={data.evidence_gate_enabled ? "已启用" : "未启用"}
              />
            </section>
            <div className="rag-grid rag-grid-overview">
              <section className="rag-panel">
                <SectionHead eyebrow="01 · 当前实现" title="检索引擎组成" />
                <dl className="rag-detail-list">
                  {Object.entries(data.component_versions).map(([key, value]) => (
                    <IdentityValue
                      key={key}
                      label={displayFieldLabel(key)}
                      value={value}
                    />
                  ))}
                  <IdentityValue
                    label="Embedding 身份"
                    value={`${data.embedding.revision} / ${data.embedding.dimension} 维`}
                  />
                </dl>
                <div className="rag-capability-row">
                  {data.technologies.map((technology) => (
                    <StatusPill key={technology} value="available">
                      {technology}
                    </StatusPill>
                  ))}
                </div>
                <div className="rag-overview-action">
                  <div>
                    <strong>从一个真实问题开始</strong>
                    <span>在同一请求里比较 Legacy 与 Hybrid 的全阶段排名。</span>
                  </div>
                  <a className="rag-link-button" href={RAG_LAB_ROUTES.retrieval}>
                    打开检索诊断
                  </a>
                </div>
              </section>
              <section className="rag-panel">
                <SectionHead eyebrow="02 · 实验事实" title="目前能得出的结论" />
                <dl className="rag-detail-list">
                  {Object.entries(data.diagnostic_dataset).map(([key, value]) => (
                    <IdentityValue
                      key={key}
                      label={displayFieldLabel(key)}
                      value={displayOverviewValue(value)}
                    />
                  ))}
                </dl>
                <ol className="rag-finding-list">
                  {data.experiment_findings.map((finding, index) => (
                    <li key={finding}>
                      <span>{String(index + 1).padStart(2, "0")}</span>
                      <p>{finding}</p>
                    </li>
                  ))}
                </ol>
              </section>
            </div>
            <section className="rag-panel">
              <SectionHead eyebrow="03 · 演示边界" title="这个控制台不代表什么" />
              <div className="rag-boundary-grid">
                {data.demo_boundaries.map((boundary) => (
                  <p key={boundary}>{boundary}</p>
                ))}
              </div>
            </section>
            <section className="rag-panel">
              <SectionHead eyebrow="04 · 检索配置" title="当前生效的检索配置" />
              <div className="rag-profile-grid">
                {data.profiles.map((profile) => (
                  <article key={`${profile.profile_id}-${profile.profile_version}`}>
                    <div>
                      <strong>{profile.profile_id}</strong>
                      <StatusPill value="available">{profile.profile_version}</StatusPill>
                    </div>
                    <dl>
                      <IdentityValue label="融合方式" value={profile.fusion_strategy} />
                      <IdentityValue
                        label="语义 / 词法权重"
                        value={`${profile.semantic_weight} : ${profile.lexical_weight}`}
                      />
                      <IdentityValue label="融合候选上限" value={profile.fusion_candidate_limit} />
                      <IdentityValue label="最终证据上限" value={profile.evidence_limit} />
                      <IdentityValue label="总超时" value={`${profile.total_timeout_ms} ms`} />
                    </dl>
                  </article>
                ))}
              </div>
            </section>
          </>
          );
        }}
      </RagState>
    </RagConsoleShell>
  );
}
