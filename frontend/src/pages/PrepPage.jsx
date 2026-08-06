import { useEffect, useMemo, useState } from "react";
import {
  clearStableRequestId,
  getJson,
  postJson,
  stableRequestId,
} from "../api/client";
import { AppShell, PageHeading } from "../components/AppShell";
import { WorkflowRail } from "../components/WorkflowRail";
import { Badge, Button, EmptyState, Notice, SectionHeading } from "../components/UI";
import { usePageMeta } from "../hooks/usePageMeta";

const DRAFT_KEY = "interview-agent:draft-id";
const MAX_FILE_BYTES = 1024 * 1024;

function PlanQuestion({ question, index }) {
  return (
    <li className="plan-question">
      <span className="plan-index">Q{String(index + 1).padStart(2, "0")}</span>
      <div>
        <div className="plan-question-meta">
          <Badge tone="blue">{question.kind || "technical"}</Badge>
          <span>{question.focus || "综合考察"}</span>
        </div>
        <strong>{question.prompt}</strong>
      </div>
    </li>
  );
}

export function PrepPage() {
  usePageMeta({
    title: "开始一次模拟面试",
    description: "根据岗位 JD、候选人简历与知识证据生成个性化技术面试计划。",
    theme: "research",
  });

  const [jobDescription, setJobDescription] = useState("");
  const [resumeText, setResumeText] = useState("");
  const [plan, setPlan] = useState(null);
  const [state, setState] = useState("idle");
  const [notice, setNotice] = useState(null);
  const [fileMeta, setFileMeta] = useState({ jd: "未选择文件", resume: "未选择文件" });
  const [validation, setValidation] = useState({ jd: false, resume: false });

  useEffect(() => {
    document.body.dataset.prepState = state;
    return () => delete document.body.dataset.prepState;
  }, [state]);

  const questions = plan?.questions || [];
  const estimatedMinutes = useMemo(
    () => questions.length ? `${questions.length * 4}–${questions.length * 6} 分钟` : "--",
    [questions.length],
  );

  function validate() {
    const next = { jd: !jobDescription.trim(), resume: !resumeText.trim() };
    setValidation(next);
    if (next.jd || next.resume) {
      setNotice({ tone: "warning", text: "请先填写岗位 JD 和简历内容。" });
      return false;
    }
    return true;
  }

  async function importTextFile(file, type) {
    if (!file) return;
    if (file.size > MAX_FILE_BYTES) {
      setNotice({ tone: "warning", text: "文件不能超过 1 MiB。" });
      return;
    }
    if (!/\.(txt|md)$/i.test(file.name)) {
      setNotice({ tone: "warning", text: "仅支持 .txt 或 .md 文本文件。" });
      return;
    }
    const text = await file.text();
    if (type === "jd") setJobDescription(text);
    else setResumeText(text);
    setFileMeta((current) => ({ ...current, [type]: `${file.name} · ${Math.ceil(file.size / 1024)} KB` }));
    setNotice({ tone: "success", text: "文本文件已导入，可以继续编辑。" });
  }

  async function generatePlan() {
    if (!validate()) return;
    setState("generating");
    setNotice(null);
    try {
      const payload = await postJson("/api/prep", {
        job_description: jobDescription,
        resume_text: resumeText,
      });
      setPlan(payload);
      setState("ready");
      setNotice({ tone: "success", text: `已生成 ${payload.questions?.length || 0} 道真实面试题。` });
    } catch (error) {
      setState("error");
      setNotice({ tone: "danger", text: error.message });
    }
  }

  async function saveDraft() {
    if (!validate()) return;
    setState("saving");
    try {
      const payload = await postJson("/api/interview-drafts", {
        draft_id: localStorage.getItem(DRAFT_KEY) || undefined,
        job_description: jobDescription,
        resume_text: resumeText,
        title: plan?.title || undefined,
        job_tags: plan?.job_tags || undefined,
      });
      localStorage.setItem(DRAFT_KEY, payload.draft_id);
      setState(plan ? "ready" : "idle");
      setNotice({ tone: "success", text: "草稿已保存在本机运行时。" });
    } catch (error) {
      setState("error");
      setNotice({ tone: "danger", text: error.message });
    }
  }

  async function restoreDraft() {
    const draftId = localStorage.getItem(DRAFT_KEY);
    if (!draftId) {
      setNotice({ tone: "info", text: "当前浏览器没有可恢复的草稿。" });
      return;
    }
    setState("restoring");
    try {
      const payload = await getJson(`/api/interview-drafts/${encodeURIComponent(draftId)}`);
      setJobDescription(payload.job_description || "");
      setResumeText(payload.resume_text || "");
      setState("idle");
      setNotice({ tone: "success", text: "草稿已恢复，请重新生成计划以刷新知识证据。" });
    } catch (error) {
      if (error.status === 404) localStorage.removeItem(DRAFT_KEY);
      setState("error");
      setNotice({ tone: "danger", text: error.message });
    }
  }

  async function startInterview() {
    if (!validate()) return;
    if (!plan) {
      setNotice({ tone: "warning", text: "请先生成并确认面试计划。" });
      return;
    }
    setState("starting");
    try {
      const requestScope = `session-start:${plan.plan_revision_id}`;
      const turn = await postJson("/api/interviews", {
        plan_revision_id: plan.plan_revision_id,
        expected_revision: plan.revision,
        plan_sha256: plan.plan_sha256,
        request_id: stableRequestId(requestScope),
      });
      clearStableRequestId(requestScope);
      window.location.assign(`/interview?session_id=${encodeURIComponent(turn.session_id)}`);
    } catch (error) {
      setState("error");
      setNotice({ tone: "danger", text: error.message });
    }
  }

  const busy = ["generating", "saving", "restoring", "starting"].includes(state);
  const prepContext = plan?.prep_context || {};

  return (
    <AppShell statusLabel="研究画布 · 准备" skipLabel="跳到面试准备">
      <div className="workflow-layout">
        <WorkflowRail current={1} note={<><strong>证据优先</strong><p>计划只使用当前输入和后端返回的知识预热结果。</p></>} />
        <main id="main-content" className="page-main prep-main" tabIndex="-1">
          <PageHeading
            title="建立一次有证据的技术面试"
            description="输入岗位要求和候选人经历。Knowledge Agent 会先整理考察边界，再生成可解释的题目计划。"
            aside={<Badge tone={plan ? "success" : "neutral"}>{plan ? "计划已就绪" : "等待资料"}</Badge>}
          />

          <div className="prep-layout">
            <section className="source-column" aria-label="面试资料">
              <article className="field-panel">
                <div className="field-panel-heading">
                  <div><h2>目标岗位</h2><p>岗位职责、技术栈、业务规模和能力要求。</p></div>
                  <label className="file-button">导入文本<input type="file" accept=".txt,.md,text/plain,text/markdown" onChange={(event) => importTextFile(event.target.files?.[0], "jd")} /></label>
                </div>
                <textarea value={jobDescription} onChange={(event) => { setJobDescription(event.target.value); setValidation((current) => ({ ...current, jd: false })); }} maxLength="50000" aria-label="岗位 JD" aria-invalid={validation.jd || undefined} aria-describedby="prepInputNotice" placeholder="粘贴目标岗位 JD。建议保留职责、技术要求、业务规模和协作方式。" />
                <div className="field-foot"><span>{fileMeta.jd}</span><span>{jobDescription.length.toLocaleString()} / 50,000</span></div>
              </article>

              <article className="field-panel">
                <div className="field-panel-heading">
                  <div><h2>候选人经历</h2><p>项目经历、技术决策、量化结果和个人职责。</p></div>
                  <label className="file-button">导入文本<input type="file" accept=".txt,.md,text/plain,text/markdown" onChange={(event) => importTextFile(event.target.files?.[0], "resume")} /></label>
                </div>
                <textarea value={resumeText} onChange={(event) => { setResumeText(event.target.value); setValidation((current) => ({ ...current, resume: false })); }} maxLength="50000" aria-label="简历内容" aria-invalid={validation.resume || undefined} aria-describedby="prepInputNotice" placeholder="粘贴候选人简历。重点保留项目背景、个人贡献、技术方案和结果。" />
                <div className="field-foot"><span>{fileMeta.resume}</span><span>{resumeText.length.toLocaleString()} / 50,000</span></div>
              </article>

              <Notice id="prepInputNotice" tone={notice?.tone}>{notice?.text}</Notice>
              <div className="action-row">
                <Button onClick={restoreDraft} disabled={busy}>恢复草稿</Button>
                <Button onClick={saveDraft} disabled={busy} busy={state === "saving"}>保存草稿</Button>
                <Button variant={plan ? "secondary" : "primary"} onClick={generatePlan} disabled={busy} busy={state === "generating"}>生成面试计划</Button>
              </div>
            </section>

            <aside className="plan-column" aria-label="面试计划">
              <section className="plan-field">
                <div className="plan-field-head">
                  <div><h2>{plan?.title || "面试计划待生成"}</h2></div>
                  <Badge tone={plan ? "coral" : "neutral"}>{questions.length || "--"} 题</Badge>
                </div>
                <dl className="plan-metrics">
                  <div><dt>题目数量</dt><dd>{questions.length || "--"}</dd></div>
                  <div><dt>预计时长</dt><dd>{estimatedMinutes}</dd></div>
                  <div><dt>岗位标签</dt><dd>{plan?.job_tags?.length || "--"}</dd></div>
                </dl>

                {questions.length ? (
                  <ol className="plan-list">{questions.map((question, index) => <PlanQuestion key={question.id || index} question={question} index={index} />)}</ol>
                ) : (
                  <EmptyState title="等待形成考察路径" description="生成后，这里会按顺序展示问题、类型与考察重点。" />
                )}

                <Button variant={plan ? "primary" : "secondary"} className="full-width" onClick={startInterview} disabled={!plan || busy} busy={state === "starting"}>开始本次面试</Button>
              </section>

              <section className="knowledge-section">
                <SectionHeading kicker="Knowledge Agent" title="知识预热" meta={prepContext.knowledge_status || "等待计划"} />
                <p>{prepContext.summary || "系统会根据岗位输入提取考点，并说明正常检索、关键词降级或不可用路径。"}</p>
                <div className="tag-row">{(prepContext.topics || []).map((topic) => <Badge key={topic.id || topic.label} tone="green">{topic.label || topic.id}</Badge>)}</div>
                {(prepContext.evidence_refs || []).length ? (
                  <div className="prep-evidence-list">
                    {(prepContext.evidence_refs || []).map((item) => (
                      <article key={item.evidence_id} data-evidence-id={item.evidence_id}>
                        <div><strong>{item.title}</strong><Badge tone="blue">{item.source_type}</Badge></div>
                        <p>{item.candidate_summary || "该证据已绑定到本次准备上下文。"}</p>
                        <code>{item.evidence_id}</code>
                      </article>
                    ))}
                  </div>
                ) : plan ? <p className="knowledge-degraded">本次计划未附加可信知识依据；界面不会伪造引用。</p> : null}
              </section>
            </aside>
          </div>
        </main>
      </div>
    </AppShell>
  );
}
