import { useEffect, useRef, useState } from "react";
import { RagConsoleShell } from "../components/rag/RagConsoleShell";
import {
  IdentityValue,
  SectionHead,
  StatusPill,
} from "../components/rag/RagPrimitives";
import { usePageMeta } from "../hooks/usePageMeta";
import { getRagEvidenceTrace } from "../rag/ragApi";
import { displayStatus } from "../rag/ragDisplay";
import "../styles/pages/rag-console.css";

const labels = {
  base_evidence_bundle: "BaseEvidenceBundle（基础证据包）",
  question_evidence_binding: "QuestionEvidenceBinding（问题证据绑定）",
  review_evidence_binding: "ReviewEvidenceBinding（评审证据绑定）",
  reviewer_decision: "Reviewer Decision（评审结论）",
  followup_decision: "Follow-up Decision（追问结论）",
};
const boundaryLabels = {
  raw_query_excluded: "不返回原始问题",
  answer_excluded: "不返回原始答案",
  resume_excluded: "不返回简历原文",
  jd_excluded: "不返回 JD 原文",
  provider_payload_excluded: "不返回模型服务载荷",
  embedding_excluded: "不返回向量数据",
  chain_of_thought_excluded: "不返回模型思维过程",
};

function traceNote(stage) {
  if (stage.recording_status === "not_recorded") {
    return "没有可用的持久化记录，界面不会推断缺失内容。";
  }
  return stage.note;
}

export function RagEvidenceTracePage() {
  usePageMeta({
    title: "证据链路",
    description: "安全的证据传递与决策链路视图。",
    bodyClass: "start-page-body",
  });
  const [traceId, setTraceId] = useState("");
  const [state, setState] = useState({
    status: "idle",
    data: null,
    error: null,
  });
  const requestRef = useRef(null);
  useEffect(() => () => requestRef.current?.abort(), []);
  const submit = async (event) => {
    event.preventDefault();
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setState({ status: "loading", data: null, error: null });
    try {
      const data = await getRagEvidenceTrace(traceId, {
        signal: controller.signal,
      });
      setState({ status: "success", data, error: null });
    } catch (error) {
      if (error?.code !== "REQUEST_ABORTED")
        setState({ status: "error", data: null, error });
    }
  };
  const data = state.data;
  return (
    <RagConsoleShell
      statusLabel={data ? "已持久化证据链" : "等待查询"}
      statusTone={state.status === "error" ? "error" : "ready"}
    >
      <header className="rag-page-head">
        <div>
          <p>安全证据链 · 不展示思维过程</p>
          <h1>证据链路</h1>
          <span>
            展示已持久化的证据传递关系与策略决策，不暴露或重建模型思维过程。
          </span>
        </div>
        <StatusPill value={data ? "available" : "not_evaluated"}>
          {data ? "安全数据" : "等待 Trace"}
        </StatusPill>
      </header>
      <form className="rag-trace-lookup" onSubmit={submit}>
        <label>
          <span>不透明会话 / Trace ID</span>
          <input
            value={traceId}
            onChange={(event) => setTraceId(event.target.value)}
            required
            maxLength="160"
            autoComplete="off"
          />
        </label>
        <button type="submit" disabled={state.status === "loading"}>
          {state.status === "loading" ? "正在读取…" : "查询证据链"}
        </button>
      </form>
      {state.error && (
        <div className="rag-inline-error" role="alert">
          <strong>证据链不可用</strong>
          <span>{state.error.message}</span>
        </div>
      )}
      <section className="rag-panel">
        <SectionHead eyebrow="01 · 证据传递" title="证据如何传递" />
        <div className="rag-timeline">
          {(data?.stages || []).map((stage, index) => (
            <article key={`${stage.stage}:${stage.record_id || index}`}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <div>
                <strong>{labels[stage.stage] || stage.stage}</strong>
                <p>{traceNote(stage)}</p>
                <dl className="rag-detail-list">
                  <IdentityValue
                    label="记录 ID"
                    value={stage.record_id || "未记录"}
                  />
                  <IdentityValue
                    label="父记录"
                    value={stage.parent_record_id || "未记录"}
                  />
                  <IdentityValue
                    label="时间"
                    value={stage.created_at || "未记录"}
                  />
                  <IdentityValue
                    label="证据 ID"
                    value={stage.evidence_ids.join(" · ") || "无"}
                  />
                  <IdentityValue
                    label="可用性"
                    value={displayStatus(stage.decision?.availability || "not_recorded")}
                  />
                  <IdentityValue
                    label="充分性"
                    value={displayStatus(stage.decision?.sufficiency || "not_recorded")}
                  />
                  <IdentityValue
                    label="一致性"
                    value={displayStatus(stage.decision?.consistency || "not_recorded")}
                  />
                  <IdentityValue
                    label="评估置信度"
                    value={
                      displayStatus(stage.decision?.evaluation_confidence || "not_recorded")
                    }
                  />
                  <IdentityValue
                    label="原因代码"
                    value={
                      stage.decision?.reason_codes?.join(" · ") ||
                      "未记录"
                    }
                  />
                  <IdentityValue
                    label="门禁版本"
                    value={stage.decision?.gate_version || "未记录"}
                  />
                </dl>
                {stage.evidence_refs?.length > 0 && (
                  <details>
                    <summary>安全证据引用</summary>
                    {stage.evidence_refs.map((reference) => (
                      <dl
                        key={reference.evidence_id}
                        className="rag-detail-list"
                      >
                        <IdentityValue
                          label="证据 ID"
                          value={reference.evidence_id}
                        />
                        <IdentityValue label="标题" value={reference.title} />
                        <IdentityValue
                          label="领域 / 主题"
                          value={`${reference.domain} / ${reference.topic}`}
                        />
                        <IdentityValue
                          label="来源类型"
                          value={reference.source_type}
                        />
                        <IdentityValue
                          label="内容 SHA"
                          value={reference.content_sha256}
                        />
                        <IdentityValue
                          label="语料清单"
                          value={reference.corpus_manifest_sha256}
                        />
                      </dl>
                    ))}
                  </details>
                )}
              </div>
              <StatusPill value={stage.recording_status} />
            </article>
          ))}
        </div>
        {!data && (
          <p className="rag-empty-row">
            输入不透明 Trace ID，读取已持久化的证据链路。
          </p>
        )}
        <div className="rag-boundary-note">
          <strong>安全边界</strong>
          <p>
            {data
              ? data.safe_boundary
                  .map((item) => boundaryLabels[item] || item)
                  .join(" · ")
              : "原始问题、答案、简历、JD、模型服务载荷、向量与思维过程均不会返回。"}
          </p>
        </div>
      </section>
    </RagConsoleShell>
  );
}
