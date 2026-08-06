import {
  BookOpenText,
  Briefcase,
  CheckCircle,
  Circle,
  Clock,
  CloudCheck,
  Eraser,
  FileText,
  FloppyDisk,
  IdentificationCard,
  Info,
  ListChecks,
  ShieldCheck,
  SpinnerGap,
  Trash,
  WarningCircle,
} from "@phosphor-icons/react";

const TABS = [
  { id: "readiness", label: "准备状态" },
  { id: "plan", label: "计划" },
  { id: "evidence", label: "证据" },
];

const KNOWLEDGE_COPY = {
  keyword: {
    label: "基于资料关键词准备",
    text: "当前计划依据岗位 JD 与候选人经历中的关键词生成，未附加公开知识引用。",
    tone: "neutral",
  },
  completed: {
    label: "知识证据可用",
    text: "知识检索已完成。下方仅展示服务端公开返回、可供核对的证据摘要。",
    tone: "success",
  },
  empty: {
    label: "未找到公开证据",
    text: "知识检索已完成，但没有可展示的公开证据；计划仍以岗位与经历资料为准。",
    tone: "neutral",
  },
  degraded: {
    label: "知识检索已降级",
    text: "知识检索路径未能完整工作。当前公开数据不包含具体原因，计划仍可依据岗位与经历资料继续使用。",
    tone: "warning",
  },
};

function EmptyPanel({ Icon, title, children }) {
  return (
    <div className="start-inspector-empty">
      <div className="start-inspector-empty-head">
        <Icon size={18} weight="duotone" aria-hidden="true" />
        <strong>{title}</strong>
      </div>
      <p>{children}</p>
    </div>
  );
}

function ReadinessPanel({ jobDescription, resumeText, plan, draftMeta }) {
  const checks = [
    { label: "岗位 JD", ready: Boolean(jobDescription.trim()), Icon: Briefcase },
    { label: "候选人经历", ready: Boolean(resumeText.trim()), Icon: IdentificationCard },
    { label: "面试蓝图", ready: Boolean(plan), Icon: ListChecks },
    { label: "匿名草稿", ready: Boolean(draftMeta), Icon: CloudCheck },
  ];
  return (
    <section id="prep-inspector-readiness" className="start-readiness-panel" role="tabpanel" aria-labelledby="prep-inspector-tab-readiness">
      <div className="start-readiness-list">
        {checks.map(({ label, ready, Icon }) => (
          <div key={label} data-ready={ready}>
            <span className="start-readiness-label"><Icon size={17} weight="bold" aria-hidden="true" />{label}</span>
            <strong>{ready ? "已就绪" : "待完成"}</strong>
          </div>
        ))}
      </div>
      <div className="start-privacy-note">
        <ShieldCheck size={18} weight="duotone" aria-hidden="true" />
        <p><strong>本地只保留恢复标识</strong><span>岗位与经历正文通过服务端草稿保存；浏览器不复制持久化正文。</span></p>
      </div>
    </section>
  );
}

function PlanPanel({ plan, enabledQuestions, estimatedMinutes }) {
  if (!plan) {
    return (
      <section id="prep-inspector-plan" className="start-plan-panel" role="tabpanel" aria-labelledby="prep-inspector-tab-plan">
        <EmptyPanel Icon={ListChecks} title="尚未生成面试计划">填写两份资料后，生成蓝图即可在这里核对版本、题量和考察范围。</EmptyPanel>
      </section>
    );
  }
  return (
    <section id="prep-inspector-plan" className="start-plan-panel" role="tabpanel" aria-labelledby="prep-inspector-tab-plan">
      <div className="start-plan-summary">
        <div><span>当前蓝图</span><h3>计划摘要</h3><p className="start-prep-plan-title">{plan.title}</p></div>
        <div className="start-job-tags" aria-label="岗位标签">
          {(plan.job_tags || []).length ? plan.job_tags.map((tag) => <span key={tag}>{tag}</span>) : <span data-empty="true">暂无岗位标签</span>}
        </div>
      </div>
      <dl className="start-plan-metrics">
        <div><dt><FileText size={14} aria-hidden="true" />版本</dt><dd>v{plan.plan_version}</dd></div>
        <div><dt><ListChecks size={14} aria-hidden="true" />题目</dt><dd>{enabledQuestions.length} 道</dd></div>
        <div><dt><Clock size={14} aria-hidden="true" />预计</dt><dd>{estimatedMinutes}</dd></div>
      </dl>
      <ol className="start-plan-list">
        {enabledQuestions.map((question, index) => (
          <li key={question.question_id} className="start-prep-plan-summary-question">
            <span className="start-plan-index">{String(index + 1).padStart(2, "0")}</span>
            <div className="start-plan-copy"><div className="start-plan-meta"><span>{question.kind}</span>{question.required && <span>必考</span>}</div><strong>{question.prompt}</strong></div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function PrepEvidenceContent({ plan, compact = false }) {
  const prepContext = plan?.prep_context;
  const knowledgeStatus = prepContext?.knowledge_status || "keyword";
  const evidence = prepContext?.evidence_refs || [];
  const knowledge = KNOWLEDGE_COPY[knowledgeStatus] || KNOWLEDGE_COPY.keyword;

  if (!plan) {
    return <EmptyPanel Icon={BookOpenText} title="尚无证据上下文">生成面试蓝图后，这里会如实显示公开证据、关键词准备或检索降级状态。</EmptyPanel>;
  }

  return (
    <div className={compact ? "start-prep-evidence-compact" : undefined}>
      <div className="start-evidence-head">
        <div><span>Knowledge</span><h3>计划依据与公开证据</h3></div>
        <span className="start-knowledge-state" data-state={knowledgeStatus}>
          {knowledgeStatus === "degraded" ? <WarningCircle size={14} weight="fill" aria-hidden="true" /> : knowledgeStatus === "completed" ? <CheckCircle size={14} weight="fill" aria-hidden="true" /> : <Info size={14} weight="bold" aria-hidden="true" />}
          <strong>{knowledge.label}</strong>
        </span>
      </div>
      <div className="start-evidence-state" data-tone={knowledge.tone === "warning" ? "warning" : undefined}>
        <span aria-hidden="true">{knowledgeStatus === "degraded" ? <WarningCircle size={18} weight="duotone" /> : <BookOpenText size={18} weight="duotone" />}</span>
        <div><strong>{knowledge.label}</strong><p>{knowledge.text}</p></div>
      </div>
      {evidence.length > 0 ? (
        <div className="start-evidence-list" aria-label="公开证据">
          {evidence.map((item, index) => (
            <article key={item.evidence_id || `${item.title}-${index}`}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <div><strong>{item.title || "未命名证据"}</strong><p>{item.candidate_summary || `${item.domain || "公开来源"} · ${item.source_type || "知识条目"}`}</p>{item.evidence_id && <code>{item.evidence_id}</code>}</div>
            </article>
          ))}
        </div>
      ) : <p className="start-evidence-summary">当前响应没有可展示的公开 evidence refs。</p>}
    </div>
  );
}

function EvidencePanel({ plan }) {
  return (
    <section id="prep-inspector-evidence" className="start-evidence-panel" role="tabpanel" aria-labelledby="prep-inspector-tab-evidence">
      <PrepEvidenceContent plan={plan} compact />
    </section>
  );
}

export function PrepInspector({
  activeTab,
  onTabChange,
  status,
  statusLabel,
  showSpinner,
  jobDescription,
  resumeText,
  plan,
  enabledQuestions,
  estimatedMinutes,
  draftMeta,
  busy,
  draftId,
  clearArmed,
  onSave,
  onRestore,
  onDelete,
  onClear,
}) {
  const loading = showSpinner && ["generating", "saving", "restoring", "starting", "updating", "regenerating"].includes(status);
  function moveTab(event, index) {
    const keyOffsets = { ArrowLeft: -1, ArrowRight: 1 };
    let nextIndex;
    if (event.key in keyOffsets) nextIndex = (index + keyOffsets[event.key] + TABS.length) % TABS.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = TABS.length - 1;
    else return;
    event.preventDefault();
    onTabChange(TABS[nextIndex].id);
    event.currentTarget.parentElement?.querySelectorAll('[role="tab"]')[nextIndex]?.focus();
  }
  return (
    <aside className="start-inspector start-prep-inspector" aria-labelledby="prep-inspector-title">
      <header className="start-inspector-head">
        <div><span>INSPECTOR</span><h2 id="prep-inspector-title">准备检查器</h2></div>
        <span className="start-inspector-state" data-state={status}>{loading ? <SpinnerGap className="start-spinner" size={14} weight="bold" aria-hidden="true" /> : status === "error" ? <WarningCircle size={14} weight="fill" aria-hidden="true" /> : <Circle size={14} weight="fill" aria-hidden="true" />}{statusLabel}</span>
      </header>
      <div className="start-inspector-tabs" role="tablist" aria-label="检查器视图">
        {TABS.map((tab, index) => (
          <button
            key={tab.id}
            id={`prep-inspector-tab-${tab.id}`}
            type="button"
            role="tab"
            aria-controls={`prep-inspector-${tab.id}`}
            aria-selected={activeTab === tab.id}
            tabIndex={activeTab === tab.id ? 0 : -1}
            onClick={() => onTabChange(tab.id)}
            onKeyDown={(event) => moveTab(event, index)}
          >{tab.label}</button>
        ))}
      </div>
      <div className="start-inspector-content">
        {activeTab === "readiness" && <ReadinessPanel jobDescription={jobDescription} resumeText={resumeText} plan={plan} draftMeta={draftMeta} />}
        {activeTab === "plan" && <PlanPanel plan={plan} enabledQuestions={enabledQuestions} estimatedMinutes={estimatedMinutes} />}
        {activeTab === "evidence" && <EvidencePanel plan={plan} />}
      </div>
      <div className="start-inspector-actions start-prep-data-actions" aria-label="数据操作">
        <button className="button start-tool-button" type="button" onClick={onSave} disabled={busy}>
          <FloppyDisk size={16} weight="bold" aria-hidden="true" />保存草稿
        </button>
        <button className="button start-tool-button" type="button" onClick={onRestore} disabled={busy}>
          <CloudCheck size={16} weight="bold" aria-hidden="true" />恢复草稿
        </button>
        <button className="button start-tool-button" type="button" onClick={onDelete} disabled={busy || !draftId}>
          <Trash size={16} weight="bold" aria-hidden="true" />删除已保存草稿
        </button>
        <button className="button start-tool-button start-tool-danger" type="button" onClick={onClear} disabled={busy || (!jobDescription && !resumeText)} data-state={clearArmed ? "confirm" : undefined}>
          {clearArmed ? <WarningCircle size={16} weight="fill" aria-hidden="true" /> : <Eraser size={16} weight="bold" aria-hidden="true" />}
          {clearArmed ? "确认清空画布" : "清空当前画布"}
        </button>
      </div>
    </aside>
  );
}
