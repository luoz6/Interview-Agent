import { useEffect, useMemo, useState } from "react";
import { downloadFile, getJson } from "../api/client";
import { AppShell, PageHeading } from "../components/AppShell";
import { Badge, Button, EmptyState, Notice, SectionHeading, Skeleton } from "../components/UI";
import { usePageMeta } from "../hooks/usePageMeta";
import { useSessionId } from "../hooks/useSessionId";

const dimensionLabels = {
  breadth: "知识广度",
  depth: "技术深度",
  architecture: "系统设计",
  engineering: "工程实践",
  communication: "表达沟通",
};

function scoreTone(score) {
  if (score >= 85) return "success";
  if (score >= 70) return "blue";
  if (score >= 60) return "coral";
  return "danger";
}

function DimensionBars({ values = {} }) {
  return <div className="dimension-list">{Object.entries(dimensionLabels).map(([key, label]) => {
    const value = Number(values[key]) || 0;
    return <div key={key} className="dimension-row"><div><span>{label}</span><strong>{value}</strong></div><progress max="100" value={value} aria-label={`${label} ${value} 分`} /></div>;
  })}</div>;
}

function FeedbackItem({ feedback, index }) {
  const references = feedback.references || [];
  const dimensionEvidence = feedback.dimension_evidence || [];
  return (
    <details className="feedback-item" open={index === 0}>
      <summary>
        <span className="feedback-number">Q{String(index + 1).padStart(2, "0")}</span>
        <div><strong>{feedback.question_text || feedback.question_id}</strong><small>{feedback.answer_state === "skipped" ? "本题已跳过" : `${(feedback.applicable_dimensions || []).map((value) => dimensionLabels[value] || value).join(" · ") || "综合能力"}`}</small></div>
        <Badge tone={scoreTone(feedback.score)}>{feedback.score}/100</Badge>
      </summary>
      <div className="feedback-body">
        <section><h4>评分依据</h4><p>{feedback.rationale || "暂无评分依据。"}</p></section>
        <section className="feedback-warning"><h4>主要不足</h4><p>{feedback.critique || "暂无重点不足。"}</p></section>
        <section className="feedback-better"><h4>更好的回答</h4><p>{feedback.better_answer || "暂无改进答案。"}</p></section>
        <section><h4>证据引用</h4>{references.length ? <ul>{references.map((reference) => <li key={reference.chunk_id}><strong>{reference.title}</strong><p>{reference.excerpt}</p><code>{reference.chunk_id}</code></li>)}</ul> : <p>本题没有可公开的知识引用。</p>}</section>
        <section className="scoring-evidence"><h4>评分证据</h4>{dimensionEvidence.length ? <div className="scoring-evidence-list">{dimensionEvidence.map((item, evidenceIndex) => <article key={`${item.dimension || "dimension"}-${evidenceIndex}`}><div><Badge tone="blue">{dimensionLabels[item.dimension] || item.dimension || "综合能力"}</Badge><span>{(item.quality_signals || []).join(" · ") || "暂无质量信号"}</span></div><dl><div><dt>命中证据</dt><dd>{(item.observed || []).join("；") || "暂无明确命中项"}</dd></div><div><dt>缺失项</dt><dd>{(item.missing || []).join("；") || "未记录缺失项"}</dd></div></dl></article>)}</div> : <p>当前报告没有返回独立的维度证据记录。</p>}</section>
      </div>
    </details>
  );
}

function RuntimeList({ items, type }) {
  if (!items.length) return <EmptyState title="暂无公开记录" description="当前运行环境没有返回可公开的稳定轨迹。" />;
  return <div className="runtime-list">{items.map((item, index) => {
    const recordId = item.run_id || item.event_id;
    const agentTitle = [item.agent, item.operation].filter(Boolean).join(" · ");
    const eventTitle = item.event_type || item.code;
    return (
      <article key={recordId || index}>
        <div>
          <Badge tone={item.status === "failed" ? "danger" : item.status === "completed" ? "success" : "blue"}>{item.status || "未提供状态"}</Badge>
          {recordId ? <code>{recordId}</code> : <span className="runtime-unavailable">未提供记录 ID</span>}
        </div>
        <strong>{type === "agent" ? agentTitle || "未提供 Agent 操作" : eventTitle || "未提供事件类型"}</strong>
        <p>{item.message || item.error_code || item.started_at || item.created_at || "未提供公开说明"}</p>
      </article>
    );
  })}</div>;
}

export function ReportDetailPage() {
  usePageMeta({ title: "结构化面评报告", description: "查看五维评分、逐题反馈、知识证据和 Agent 运行轨迹。", theme: "report" });
  const sessionId = useSessionId();
  const [report, setReport] = useState(null);
  const [evaluations, setEvaluations] = useState([]);
  const [agentRuns, setAgentRuns] = useState([]);
  const [runtimeEvents, setRuntimeEvents] = useState([]);
  const [state, setState] = useState("loading");
  const [notice, setNotice] = useState(null);
  const [currentSection, setCurrentSection] = useState("overview");

  useEffect(() => {
    if (!sessionId) {
      setState("error");
      setNotice({ tone: "danger", text: "缺少 session_id，无法加载报告。" });
      return;
    }
    Promise.all([
      getJson(`/api/interviews/${encodeURIComponent(sessionId)}/report`),
      getJson(`/api/interviews/${encodeURIComponent(sessionId)}/question-evaluations`).catch(() => ({ items: [] })),
      getJson(`/api/interviews/${encodeURIComponent(sessionId)}/agent-runs?limit=100`).catch(() => ({ items: [] })),
      getJson(`/api/interviews/${encodeURIComponent(sessionId)}/runtime-events?limit=100`).catch(() => ({ items: [] })),
    ]).then(([reportPayload, evaluationPayload, runPayload, eventPayload]) => {
      if (reportPayload.status === "processing") {
        window.location.replace(`/report-processing?session_id=${encodeURIComponent(sessionId)}`);
        return;
      }
      setReport(reportPayload);
      setEvaluations(evaluationPayload.items || []);
      setAgentRuns(runPayload.items || []);
      setRuntimeEvents(eventPayload.items || []);
      setState(reportPayload.is_fallback ? "fallback" : "completed");
    }).catch((error) => {
      setState("error");
      setNotice({ tone: "danger", text: error.message });
    });
  }, [sessionId]);

  useEffect(() => { document.body.dataset.reportState = state; }, [state]);

  useEffect(() => {
    if (!report) return undefined;
    const sections = ["overview", "questions", "actions", "evidence", "trace"]
      .map((id) => document.getElementById(id))
      .filter(Boolean);
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible[0]) setCurrentSection(visible[0].target.id);
    }, { rootMargin: "-20% 0px -65% 0px", threshold: [0, 0.1] });
    sections.forEach((section) => observer.observe(section));
    return () => observer.disconnect();
  }, [report]);

  const feedbacks = report?.feedbacks || [];
  const dimensions = report?.overall_dimension_scores || {};
  const highScoreCount = feedbacks.filter((item) => item.score >= 80).length;
  const improvements = feedbacks.map((item) => item.critique).filter(Boolean);
  const rankedDimensions = Object.entries(dimensionLabels)
    .map(([key, label]) => ({ key, label, value: Number(dimensions[key]) || 0 }))
    .sort((left, right) => right.value - left.value);
  const strongestDimension = rankedDimensions[0];
  const weakestDimension = rankedDimensions[rankedDimensions.length - 1];
  const evidence = useMemo(() => {
    const map = new Map();
    feedbacks.flatMap((item) => item.references || []).forEach((item) => map.set(item.chunk_id, item));
    return [...map.values()];
  }, [feedbacks]);

  async function download() {
    try {
      await downloadFile(`/api/interviews/${encodeURIComponent(sessionId)}/report.pdf`, `interview-report-${sessionId}.pdf`);
    } catch (error) {
      setNotice({ tone: "danger", text: error.message });
    }
  }

  return (
    <AppShell statusLabel="Research Canvas · Review" skipLabel="跳到报告内容">
      <div className="report-layout">
        <aside className="report-rail" aria-label="报告章节">
          <h2>本次报告</h2>
          <nav><a href="#overview" aria-current={currentSection === "overview" ? "location" : undefined}>总体表现</a><a href="#questions" aria-current={currentSection === "questions" ? "location" : undefined}>逐题反馈</a><a href="#actions" aria-current={currentSection === "actions" ? "location" : undefined}>优势与改进</a><a href="#evidence" aria-current={currentSection === "evidence" ? "location" : undefined}>知识证据</a><a href="#trace" aria-current={currentSection === "trace" ? "location" : undefined}>运行轨迹</a></nav>
          <div className="report-rail-note"><strong>证据所有权</strong><p>AI 负责提取和组织证据，后端规则负责计算并确认分数。</p></div>
        </aside>

        <main id="main-content" className="report-main" tabIndex="-1">
          <section id="overview" className="report-section">
            <PageHeading title="结构化面评报告" description={report?.summary || "正在加载本次面试的真实评分和反馈。"} aside={<Badge tone={state === "fallback" ? "coral" : state === "error" ? "danger" : "success"}>{state}</Badge>} />
            <Notice tone={notice?.tone}>{notice?.text}</Notice>
            {state === "loading" ? <Skeleton lines={7} /> : state === "error" ? <EmptyState title="报告暂时无法读取" description="确认报告任务已经完成，或返回报告中心查看状态。" action={<Button onClick={() => window.location.assign("/reports")}>返回报告中心</Button>} /> : (
              <>
                {state === "fallback" && <Notice tone="warning">这份报告使用全会话降级路径完成。分数和反馈仍来自真实会话，但逐题复用链路未完全可用。</Notice>}
                <div className="score-overview">
                  <article className="overall-score"><span className="score-label">综合评分</span><strong>{report.overall_score}</strong><small>/ 100</small><Badge tone={scoreTone(report.overall_score)}>{report.overall_score >= 80 ? "表现稳健" : report.overall_score >= 60 ? "仍有提升空间" : "建议重点练习"}</Badge></article>
                  <article className="dimension-field"><SectionHeading title="能力维度" meta="数值始终可见" /><DimensionBars values={dimensions} /></article>
                  <article className="highlight-field"><SectionHeading title="结论摘要" meta="基于真实评分" /><dl className="overview-insights"><div><dt>最强能力</dt><dd>{strongestDimension ? `${strongestDimension.label} · ${strongestDimension.value}` : "暂无维度数据"}</dd></div><div><dt>优先补强</dt><dd>{weakestDimension ? `${weakestDimension.label} · ${weakestDimension.value}` : "暂无维度数据"}</dd></div><div><dt>下一步</dt><dd>{improvements[0] || "当前报告未返回明确改进项。"}</dd></div></dl><ul>{(report.highlights || []).slice(0, 3).map((item) => <li key={item}>{item}</li>)}</ul></article>
                </div>
              </>
            )}
          </section>

          {report && <>
            <section id="questions" className="report-section"><SectionHeading title="逐题反馈" meta={`${feedbacks.length} 道题`} /><p className="section-intro">先阅读评分依据和主要不足，再展开更好的回答与知识证据。</p><div className="feedback-list">{feedbacks.map((feedback, index) => <FeedbackItem key={feedback.question_id || index} feedback={feedback} index={index} />)}</div><article className="evaluation-ledger"><SectionHeading title="逐题评审链路" meta={`${evaluations.length} 条`} />{evaluations.length ? <div>{evaluations.map((item) => { const degraded = item.retrieval_path === "degraded" || Boolean(item.degraded_reason); return <article key={item.question_id}><Badge tone={item.status === "failed" ? "danger" : degraded ? "coral" : "success"}>{item.status}</Badge><strong>{item.question_id}</strong><div><p>{item.feedback?.rationale || "评审记录已保存。"}</p><small>{item.retrieval_path || "未提供检索路径"}{item.degraded_reason ? ` · 降级原因：${item.degraded_reason}` : ""}</small></div></article>; })}</div> : <EmptyState title="暂无逐题评审记录" description="报告可能由全会话路径生成，或当前运行存储未提供评审账本。" />}</article></section>

            <section id="actions" className="report-section"><SectionHeading title="优势与改进" meta="下一轮练习输入" /><div className="action-review-grid"><article><strong>{highScoreCount}</strong><h3>高分回答</h3><ul>{(report.highlights || []).map((item) => <li key={item}>{item}</li>)}</ul></article><article className="improvement-field"><strong>{improvements.length}</strong><h3>优先改进项</h3><ul>{improvements.slice(0, 6).map((item) => <li key={item}>{item}</li>)}</ul></article></div></section>

            <section id="evidence" className="report-section"><SectionHeading title="知识证据" meta={`${evidence.length} 个来源`} />{evidence.length ? <div className="evidence-grid">{evidence.map((item) => <article key={item.chunk_id} data-evidence-id={item.chunk_id}><div><Badge tone="green">{item.source_type}</Badge><code>{item.chunk_id}</code></div><h3>{item.title}</h3><p>{item.excerpt}</p></article>)}</div> : <EmptyState title="没有可公开的知识引用" description="这不等于报告失败；部分回答可以只根据候选人原始内容完成评审。" />}</section>

            <section id="trace" className="report-section"><SectionHeading title="运行轨迹" meta="稳定公开字段" /><p className="section-intro">不展示提示词、密钥、绝对路径、候选人完整原文或 Provider 原始错误。</p><div className="trace-grid"><article><h3>Agent 执行</h3><RuntimeList items={agentRuns} type="agent" /></article><article><h3>运行事件</h3><RuntimeList items={runtimeEvents} type="event" /></article></div></section>
          </>}
        </main>
      </div>

      {report && <div className="report-actions" aria-label="报告操作"><Button onClick={download}>下载 PDF</Button><Button onClick={() => window.location.assign("/prep")}>再次模拟</Button><Button variant="primary" onClick={() => window.location.assign("/reports")}>返回报告中心</Button></div>}
    </AppShell>
  );
}
