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
import { displayFieldLabel } from "../rag/ragDisplay";
import "../styles/pages/rag-console.css";

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
        {(data) => (
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
              <div className="rag-release-stamp" data-state="ready">
                <small>项目定位</small>
                <strong>学习项目 / 技术展示</strong>
                <span>本地诊断，不代表生产发布</span>
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
                  <a className="rag-link-button" href="/rag/retrieval">
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
                      value={typeof value === "boolean" ? (value ? "是" : "否") : value}
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
        )}
      </RagState>
    </RagConsoleShell>
  );
}
