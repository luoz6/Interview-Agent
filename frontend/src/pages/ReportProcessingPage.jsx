import { useEffect, useMemo, useState } from "react";
import { getJson } from "../api/client";
import { AppShell, PageHeading } from "../components/AppShell";
import { WorkflowRail } from "../components/WorkflowRail";
import { Badge, Button, EmptyState, Notice, SectionHeading, Skeleton } from "../components/UI";
import { usePageMeta } from "../hooks/usePageMeta";
import { useSessionId } from "../hooks/useSessionId";

const stages = [
  ["queued", "任务排队", "等待报告 Worker 领取任务"],
  ["retrieving", "知识检索", "复用本轮绑定证据并补齐相关知识"],
  ["analyzing", "回答分析", "整理逐题回答、缺失项与质量信号"],
  ["evaluating", "逐题评审", "验证回答证据并形成五维评分"],
  ["aggregating", "报告聚合", "生成总结、亮点和行动建议"],
  ["coaching", "改进建议", "生成下一轮练习重点与更好回答"],
  ["completed", "报告完成", "结构化报告可供阅读和下载"],
];

const stageIndex = Object.fromEntries(stages.map(([name], index) => [name, index]));

export function ReportProcessingPage() {
  usePageMeta({ title: "报告生成中", description: "查看知识检索、逐题评审和报告聚合的真实进度。", theme: "pipeline" });
  const sessionId = useSessionId();
  const [progress, setProgress] = useState(null);
  const [notice, setNotice] = useState(null);
  const [polling, setPolling] = useState(true);

  async function loadProgress() {
    if (!sessionId) {
      setNotice({ tone: "danger", text: "缺少 session_id，无法读取报告任务。" });
      setPolling(false);
      return;
    }
    try {
      const payload = await getJson(`/api/interviews/${encodeURIComponent(sessionId)}/report/progress`);
      setProgress(payload);
      document.body.dataset.reportState = payload.status;
      if (["completed", "failed"].includes(payload.status)) setPolling(false);
      setNotice(payload.status === "failed" ? { tone: "danger", text: payload.message || "报告生成失败。" } : null);
    } catch (error) {
      const retryable = !error.status || error.status === 429 || error.status >= 500;
      if (retryable && polling) {
        setNotice({ tone: "warning", text: `同步暂时失败，3 秒后自动重试：${error.message}` });
        document.body.dataset.reportState = "retrying";
      } else {
        setNotice({ tone: "danger", text: error.message });
        setPolling(false);
        document.body.dataset.reportState = "error";
      }
    }
  }

  useEffect(() => {
    loadProgress();
  }, [sessionId]);

  useEffect(() => {
    if (!polling) return undefined;
    const timer = window.setInterval(loadProgress, 3000);
    return () => window.clearInterval(timer);
  }, [polling, sessionId]);

  useEffect(() => {
    if (progress?.status !== "completed" || !sessionId) return undefined;
    const timer = window.setTimeout(() => {
      window.location.assign(`/report-detail?session_id=${encodeURIComponent(sessionId)}`);
    }, 900);
    return () => window.clearTimeout(timer);
  }, [progress?.status, sessionId]);

  const currentIndex = stageIndex[progress?.stage] ?? 0;
  const percent = Math.max(0, Math.min(100, Number(progress?.percent) || 0));
  const events = progress?.events || [];
  const metadata = progress?.metadata || {};
  const rag = progress?.rag || {};
  const metrics = useMemo(() => [
    ["任务状态", progress?.status || "loading"],
    ["当前题目", progress?.current_question_id || "--"],
    ["已完成评审", progress?.completed_question_count ?? "--"],
    ["评审总数", progress?.total_question_count ?? "--"],
  ], [progress]);

  return (
    <AppShell statusLabel="Pipeline Field · Running" statusTone="pipeline" skipLabel="跳到报告进度">
      <div className="workflow-layout pipeline-layout">
        <WorkflowRail current={3} note={<><strong>后台任务</strong><p>离开页面不会终止生成。真实状态会保留在报告中心。</p></>} />
        <main id="main-content" className="page-main processing-main" tabIndex="-1">
          <PageHeading title="把等待变成一条透明流水线" description="进度、阶段、事件和降级路径都来自后端。界面不会用无限动画代替真实任务状态。" aside={<Badge tone={progress?.status === "failed" ? "danger" : progress?.status === "completed" ? "success" : "coral"}>{progress?.status || "读取状态"}</Badge>} />

          <section className="pipeline-hero" aria-live="polite">
            <div className="pipeline-hero-top"><div><span className="progress-caption">报告进度</span><strong>{percent}%</strong></div><p>{progress?.message || "正在读取报告任务状态…"}</p></div>
            <div className="pipeline-progress" role="progressbar" aria-label="报告生成进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow={percent}><span style={{ "--progress-scale": percent / 100 }} /></div>
            <div className="pipeline-signal"><i /><span>{polling ? "每 3 秒同步一次任务状态" : progress?.status === "completed" ? "报告生成已完成" : "自动轮询已停止"}</span></div>
          </section>

          <div className="processing-layout">
            <section className="processing-primary">
              <article className="open-section">
                <SectionHeading title="生成阶段" meta={progress?.stage || "queued"} />
                <ol className="stage-list">
                  {stages.map(([name, title, description], index) => {
                    const completed = progress?.status === "completed" && index <= currentIndex;
                    const state = progress?.status === "failed" && index === currentIndex ? "failed" : completed || index < currentIndex ? "done" : index === currentIndex ? "current" : "pending";
                    const stageMessage = state === "current" && progress?.message ? progress.message : description;
                    return <li key={name} data-state={state}><span className="stage-node" /><span className="stage-code">{String(index + 1).padStart(2, "0")}</span><div><strong>{title}</strong><p>{stageMessage}</p></div><Badge tone={state === "failed" ? "danger" : state === "done" ? "success" : state === "current" ? "coral" : "neutral"}>{state}</Badge></li>;
                  })}
                </ol>
              </article>

              <article className="open-section">
                <SectionHeading title="运行事件" meta={`${events.length} 条`} />
                {!progress ? <Skeleton lines={4} /> : events.length ? <div className="event-ledger">{events.map((event, index) => <div key={`${event.stage}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><Badge tone={event.stage === "failed" ? "danger" : "blue"}>{event.stage}</Badge><p>{event.message}</p></div>)}</div> : <EmptyState title="等待第一条运行事件" description="Worker 开始处理后，这里会展示稳定阶段和公开消息。" />}
              </article>
            </section>

            <aside className="processing-context">
              <section className="context-panel pipeline-facts">
                <h2>任务信息</h2>
                <dl><div><dt>任务 ID</dt><dd><code>{progress?.report_job_id || "未提供"}</code></dd></div><div><dt>生成路径</dt><dd>{metadata.report_path || "未提供"}</dd></div><div><dt>工作流</dt><dd>{progress?.workflow_engine || "未提供"}</dd></div></dl>
              </section>
              <section className="context-panel"><h2>生成指标</h2><dl className="metric-list">{metrics.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></section>
              <section className="context-panel knowledge-context"><h2>知识检索</h2><p>匹配片段：{rag.matched_chunks ?? "处理中"}</p><div className="tag-row">{(rag.source_types || []).map((type) => <Badge key={type} tone="green">{type}</Badge>)}</div></section>
              {metadata.full_session_fallback && <Notice tone="warning">本次报告使用全会话降级路径生成；报告仍然有效，但逐题证据复用链路未完全可用。</Notice>}
              <Notice tone={notice?.tone}>{notice?.text}</Notice>
              <div className="stack-actions"><Button onClick={() => window.location.assign("/reports")}>返回报告中心</Button><Button variant={progress?.status === "completed" ? "primary" : "secondary"} disabled={progress?.status !== "completed"} onClick={() => window.location.assign(`/report-detail?session_id=${encodeURIComponent(sessionId)}`)}>查看完整报告</Button></div>
            </aside>
          </div>
        </main>
      </div>
    </AppShell>
  );
}
