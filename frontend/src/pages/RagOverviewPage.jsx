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
import {
  displayFieldLabel,
  displayPromotionBlocker,
  displayStatus,
} from "../rag/ragDisplay";
import "../styles/pages/rag-console.css";

export function RagOverviewPage() {
  usePageMeta({
    title: "RAG 运行概览",
    description: "检索引擎运行状态与发布阻断项。",
    bodyClass: "start-page-body",
  });
  const loader = useCallback((options) => getRagOverview(options), []);
  const resource = useRagResource(loader);
  return (
    <RagConsoleShell statusLabel="运行状态真实值">
      <RagState resource={resource}>
        {(data) => (
          <>
            <header className="rag-page-head">
              <div>
                <p>运行状态与发布门禁</p>
                <h1>RAG 运行概览</h1>
                <span>
                  代码已合并，不代表候选引擎已晋级；Shadow 获得授权，不代表
                  Canary 已启动；Legacy 只有在全部门禁通过后才能退出。
                </span>
              </div>
              <div
                className="rag-release-stamp"
                data-state={data.promotion.allowed ? "ready" : "blocked"}
              >
                <small>候选引擎发布状态</small>
                <strong>
                  {data.promotion.allowed ? "允许发布" : "暂不允许发布"}
                </strong>
                <span>{data.promotion.blockers.length} 个有效阻断项</span>
              </div>
            </header>
            <section className="rag-identity-strip">
              <IdentityValue label="正式引擎" value={data.formal_engine} />
              <IdentityValue label="候选引擎" value={data.candidate_engine} />
              <IdentityValue
                label="灰度比例"
                value={`${data.hybrid_rollout_percent}%`}
              />
              <IdentityValue
                label="Shadow 影子流量"
                value={data.shadow_enabled ? "已启用" : "未启用"}
              />
              <IdentityValue
                label="远程重排器"
                value={data.remote_reranker_enabled ? "已启用" : "未启用"}
              />
              <IdentityValue
                label="证据门禁"
                value={data.evidence_gate_enabled ? "已启用" : "未启用"}
              />
              <IdentityValue label="语料版本" value={data.corpus.version} />
              <IdentityValue
                label="清单摘要"
                value={data.corpus.manifest_sha256}
                hash
              />
            </section>
            <div className="rag-grid rag-grid-overview">
              <section className="rag-panel">
                <SectionHead
                  eyebrow="01 · 组件身份"
                  title="当前引擎组成"
                />
                <dl className="rag-detail-list">
                  {Object.entries(data.component_versions).map(
                    ([key, value]) => (
                      <IdentityValue key={key} label={displayFieldLabel(key)} value={value} />
                    ),
                  )}
                  <IdentityValue
                    label="向量模型"
                    value={`${data.embedding.provider} / ${data.embedding.model} / ${data.embedding.revision} / ${data.embedding.dimension}`}
                  />
                </dl>
                <div className="rag-capability-row">
                  {Object.entries(data.capabilities)
                    .filter(([key]) => key !== "access_mode")
                    .map(([key, value]) => (
                      <StatusPill
                        key={key}
                        value={value ? "available" : "not_evaluated"}
                      >
                        {displayFieldLabel(key)}：{value ? "开启" : "关闭"}
                      </StatusPill>
                    ))}
                </div>
                <dl className="rag-detail-list">
                  {Object.entries(data.release_evidence).map(([key, value]) => (
                    <IdentityValue
                      key={key}
                      label={displayFieldLabel(key)}
                      value={
                        typeof value === "boolean"
                          ? value
                            ? "是"
                            : "否"
                          : typeof value === "string"
                            ? displayStatus(value)
                            : String(value)
                      }
                    />
                  ))}
                </dl>
              </section>
              <section className="rag-panel rag-blockers">
                <SectionHead
                  eyebrow="02 · 发布门禁"
                  title="为什么暂不能发布"
                  aside={<StatusPill value="blocked">硬性阻断</StatusPill>}
                />
                <ol>
                  {data.promotion.blockers.map((blocker) => {
                    const translated = displayPromotionBlocker(blocker);
                    return <li key={blocker.code}>
                      <div>
                        <code>{blocker.code}</code>
                        <StatusPill value="blocked">
                          硬性阻断
                        </StatusPill>
                      </div>
                      <p>{translated.observed}</p>
                      <strong>{translated.action}</strong>
                      <small>阻断范围 · {translated.targets.join(" / ")}</small>
                      <time dateTime={blocker.last_evaluated_at}>
                        评估时间 {blocker.last_evaluated_at}
                      </time>
                    </li>;
                  })}
                </ol>
              </section>
            </div>
            <section className="rag-panel">
              <SectionHead
                eyebrow="03 · 检索配置"
                title="当前生效的检索配置"
              />
              <div className="rag-profile-grid">
                {data.profiles.map((profile) => (
                  <article
                    key={`${profile.profile_id}-${profile.profile_version}`}
                  >
                    <div>
                      <strong>{profile.profile_id}</strong>
                      <StatusPill value="available">
                        {profile.profile_version}
                      </StatusPill>
                    </div>
                    <dl>
                      <IdentityValue
                        label="融合方式"
                        value={profile.fusion_strategy}
                      />
                      <IdentityValue
                        label="语义 / 词法权重"
                        value={`${profile.semantic_weight} : ${profile.lexical_weight}`}
                      />
                      <IdentityValue
                        label="融合候选上限"
                        value={profile.fusion_candidate_limit}
                      />
                      <IdentityValue
                        label="最终证据上限"
                        value={profile.evidence_limit}
                      />
                      <IdentityValue
                        label="总超时"
                        value={`${profile.total_timeout_ms} ms`}
                      />
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
