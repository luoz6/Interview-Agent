import { useEffect, useMemo, useState } from "react";
import {
  ArrowCounterClockwise,
  ArrowRight,
  Books,
  Briefcase,
  CheckCircle,
  Circle,
  Clock,
  ClipboardText,
  Columns,
  FileText,
  Files,
  FloppyDisk,
  IdentificationCard,
  Info,
  ListChecks,
  ShieldCheck,
  SpinnerGap,
  Trash,
  UploadSimple,
  WarningCircle,
} from "@phosphor-icons/react";

const DRAFT_KEYS = ["interview-agent:draft-id", "interviewDraftId"];
const MAX_FILE_BYTES = 1024 * 1024;
const MAX_TEXT_LENGTH = 50000;

const KNOWLEDGE_STATUS_LABELS = {
  completed: "检索完成",
  degraded: "检索降级",
  empty: "无公开证据",
  pending: "等待计划",
  unknown: "状态未提供",
};

const QUESTION_KIND_LABELS = {
  project: "项目经历",
  technical: "技术能力",
  "system-design": "系统设计",
  system_design: "系统设计",
  behavioral: "行为问题",
  follow_up: "追问",
  "follow-up": "追问",
};

function getStoredDraftId() {
  return DRAFT_KEYS.map((key) => window.localStorage.getItem(key)).find(Boolean) || "";
}

function storeDraftId(value) {
  DRAFT_KEYS.forEach((key) => window.localStorage.setItem(key, value));
}

function clearStoredDraftId() {
  DRAFT_KEYS.forEach((key) => window.localStorage.removeItem(key));
}

function errorMessage(payload, fallback) {
  if (typeof payload?.detail === "string") return payload.detail;
  if (Array.isArray(payload?.detail)) {
    return payload.detail.map((item) => item?.msg).filter(Boolean).join("；") || fallback;
  }
  return fallback;
}

async function requestJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      ...options,
      headers: options.body
        ? { "Content-Type": "application/json", ...(options.headers || {}) }
        : options.headers,
    });
  } catch {
    throw new Error("无法连接后端服务。请确认服务已启动并稍后重试。");
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorMessage(payload, `请求失败（${response.status}）`));
  return payload;
}

function SourceEditor({
  code,
  title,
  description,
  label,
  value,
  onChange,
  onFile,
  fileName,
  invalid,
  placeholder,
  disabled,
  compact = false,
  DocumentIcon,
}) {
  const errorId = `${code}-error`;
  const ready = Boolean(value.trim());
  const feedbackState = invalid ? "error" : ready ? "ready" : "hint";
  const FeedbackIcon = invalid ? WarningCircle : ready ? CheckCircle : Info;
  const importLabel = code === "JD" ? "导入当前岗位文档" : "导入当前经历文档";
  const feedbackText = invalid
    ? `请先填写${label}，系统不会根据空白资料生成计划。`
    : ready
      ? "内容已就绪；系统会根据当前文本建立考察边界。"
      : "支持粘贴或导入 .txt / .md，最多 50,000 字。";
  return (
    <section className={`start-source start-document-editor ${compact ? "is-compact" : ""}`} data-ready={ready}>
      <header className="start-document-head">
        <div className="start-document-identity">
          <span className="start-document-code" aria-hidden="true">
            <DocumentIcon size={18} weight="bold" focusable="false" />
          </span>
          <div>
            <h2>{title}</h2>
            <p>{description}</p>
          </div>
        </div>
        <label className="start-file-button" aria-label={importLabel}>
          <UploadSimple size={16} weight="bold" aria-hidden="true" focusable="false" />
          <span>导入文本</span>
          <input
            type="file"
            accept=".txt,.md,text/plain,text/markdown"
            onChange={(event) => onFile(event.target.files?.[0], event.target)}
            disabled={disabled}
          />
        </label>
      </header>
      <label className="start-editor-label" htmlFor={`${code}-input`}>{label}</label>
      <textarea
        id={`${code}-input`}
        aria-label={label}
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? errorId : undefined}
        value={value}
        maxLength={MAX_TEXT_LENGTH}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      />
      <footer className="start-document-foot">
        <span className="start-document-file">{fileName}</span>
        <span className="start-document-count">{value.length.toLocaleString()} / {MAX_TEXT_LENGTH.toLocaleString()} 字</span>
      </footer>
      <p
        id={errorId}
        className="start-field-error"
        data-visible={invalid}
        data-state={feedbackState}
      >
        <FeedbackIcon size={16} weight={invalid || ready ? "fill" : "bold"} aria-hidden="true" focusable="false" />
        <span>{feedbackText}</span>
      </p>
    </section>
  );
}

function PlanQuestion({ question, index }) {
  const kind = QUESTION_KIND_LABELS[question.kind] || question.kind || "综合考察";
  return (
    <li className="start-plan-question">
      <span className="start-plan-index">{String(index + 1).padStart(2, "0")}</span>
      <div className="start-plan-copy">
        <div className="start-plan-meta">
          <span>{kind}</span>
          <span>{question.focus || "综合考察"}</span>
        </div>
        <strong>{question.prompt}</strong>
      </div>
    </li>
  );
}

function StatusNotice({ notice }) {
  if (!notice) return null;
  const NoticeIcon = notice.tone === "error" || notice.tone === "warning" ? WarningCircle : notice.tone === "success" ? CheckCircle : Info;
  return (
    <div
      className={`start-notice start-notice-${notice.tone}`}
      role={notice.tone === "error" ? "alert" : "status"}
      aria-live={notice.tone === "error" ? "assertive" : "polite"}
      aria-atomic="true"
    >
      <span className="start-notice-icon" aria-hidden="true">
        <NoticeIcon size={18} weight={notice.tone === "info" ? "bold" : "fill"} focusable="false" />
      </span>
      <p>{notice.text}</p>
    </div>
  );
}

function RuntimeStatus({ status, label }) {
  const loading = ["generating", "saving", "restoring", "starting"].includes(status);
  const StateIcon = status === "ready" ? CheckCircle : status === "error" ? WarningCircle : Circle;
  return (
    <div className="start-runtime" data-state={status} role="status" aria-live="polite">
      <span className="start-runtime-icon" aria-hidden="true">
        {loading
          ? <SpinnerGap className="start-spinner" size={15} weight="bold" focusable="false" />
          : <StateIcon size={15} weight={status === "idle" ? "fill" : "bold"} focusable="false" />}
      </span>
      <span>当前任务</span><strong>{label}</strong>
    </div>
  );
}

function InspectorStatus({ status, label }) {
  const loading = ["generating", "saving", "restoring", "starting"].includes(status);
  const StateIcon = status === "ready" ? CheckCircle : status === "error" ? WarningCircle : Circle;
  return (
    <span className="start-inspector-state" data-state={status}>
      {loading
        ? <SpinnerGap className="start-spinner" size={13} weight="bold" aria-hidden="true" focusable="false" />
        : <StateIcon size={13} weight={status === "idle" ? "fill" : "bold"} aria-hidden="true" focusable="false" />}
      <span>{label}</span>
    </span>
  );
}

function InspectorEmpty({ icon: EmptyIcon, title, children }) {
  return (
    <div className="start-inspector-empty">
      <div className="start-inspector-empty-head">
        <EmptyIcon size={18} weight="bold" aria-hidden="true" focusable="false" />
        <strong>{title}</strong>
      </div>
      <p>{children}</p>
    </div>
  );
}

function ReadinessItem({ ready, label, value }) {
  const StateIcon = ready ? CheckCircle : Circle;
  return (
    <div data-ready={ready}>
      <span className="start-readiness-label">
        <StateIcon size={16} weight={ready ? "fill" : "regular"} aria-hidden="true" focusable="false" />
        <span>{label}</span>
      </span>
      <strong>{value}</strong>
    </div>
  );
}

function KnowledgeStatus({ status }) {
  const normalized = status || "pending";
  const label = KNOWLEDGE_STATUS_LABELS[normalized] || normalized.replaceAll("_", " ");
  const StateIcon = normalized === "completed" ? CheckCircle : normalized === "degraded" ? WarningCircle : normalized === "empty" ? Info : Circle;
  return (
    <span className="start-knowledge-state" data-state={normalized}>
      <StateIcon size={14} weight={normalized === "completed" || normalized === "degraded" ? "fill" : "bold"} aria-hidden="true" focusable="false" />
      <strong>{label}</strong>
    </span>
  );
}

function StatusBarItem({ ready = false, state = "idle", label, value, current = false }) {
  const loading = ["generating", "saving", "restoring", "starting"].includes(state);
  const StateIcon = state === "error" || state === "warning" ? WarningCircle : state === "info" ? Info : ready || state === "ready" ? CheckCircle : Circle;
  return (
    <span className={current ? "start-status-current" : undefined} data-ready={ready} data-state={state}>
      {loading
        ? <SpinnerGap className="start-spinner" size={12} weight="bold" aria-hidden="true" focusable="false" />
        : <StateIcon size={12} weight={ready || state === "ready" || state === "warning" ? "fill" : "regular"} aria-hidden="true" focusable="false" />}
      <strong>{label}</strong>{value}
    </span>
  );
}

export function StartPage() {
  const [jobDescription, setJobDescription] = useState("");
  const [resumeText, setResumeText] = useState("");
  const [plan, setPlan] = useState(null);
  const [status, setStatus] = useState("idle");
  const [notice, setNotice] = useState(null);
  const [draftId, setDraftId] = useState(() => getStoredDraftId());
  const [fileNames, setFileNames] = useState({ jd: "未导入文件", resume: "未导入文件" });
  const [invalid, setInvalid] = useState({ jd: false, resume: false });
  const [activeDocument, setActiveDocument] = useState("jd");
  const [inspectorView, setInspectorView] = useState("plan");
  const [focusTarget, setFocusTarget] = useState("");
  const [clearArmed, setClearArmed] = useState(false);

  const questions = plan?.questions || [];
  const prepContext = plan?.prep_context || {};
  const topics = prepContext.topics || [];
  const evidence = prepContext.evidence_refs || [];
  const jobTags = plan?.job_tags || [];
  const busy = ["generating", "saving", "restoring", "starting"].includes(status);
  const sourcesReady = Number(Boolean(jobDescription.trim())) + Number(Boolean(resumeText.trim()));
  const estimatedMinutes = useMemo(
    () => questions.length ? `${questions.length * 4}–${questions.length * 6} 分钟` : "待生成",
    [questions.length],
  );

  useEffect(() => {
    const previousTitle = document.title;
    document.title = "开始一次技术面试 - 面试智能体";
    document.body.className = "start-page-body";
    const descriptionMeta = document.querySelector('meta[name="description"]');
    const themeMeta = document.querySelector('meta[name="theme-color"]');
    descriptionMeta?.setAttribute("content", "输入岗位 JD 与候选人经历，生成有证据约束的技术面试蓝图。");
    const themeColor = getComputedStyle(document.documentElement).getPropertyValue("--start-color-ink").trim();
    if (themeColor) themeMeta?.setAttribute("content", themeColor);
    return () => {
      document.title = previousTitle;
      document.body.className = "";
      delete document.body.dataset.prepState;
    };
  }, []);

  useEffect(() => {
    document.body.dataset.prepState = status;
  }, [status]);

  useEffect(() => {
    if (!clearArmed) return undefined;
    const timeout = window.setTimeout(() => setClearArmed(false), 5000);
    return () => window.clearTimeout(timeout);
  }, [clearArmed]);

  useEffect(() => {
    if (!focusTarget) return undefined;
    const frame = window.requestAnimationFrame(() => {
      const target = document.getElementById(focusTarget === "jd" ? "JD-input" : "CV-input");
      if (target) {
        target.focus();
        setFocusTarget("");
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeDocument, focusTarget]);

  function validateSources() {
    const next = {
      jd: !jobDescription.trim(),
      resume: !resumeText.trim(),
    };
    setInvalid(next);
    if (next.jd || next.resume) {
      const missingDocument = next.jd ? "jd" : "resume";
      setActiveDocument(missingDocument);
      setFocusTarget(missingDocument);
      setInspectorView("readiness");
      setNotice({ tone: "error", text: `${next.jd ? "岗位 JD" : "候选人经历"}尚未填写。需要同时提供两份资料，才能建立可信的考察边界。` });
      return false;
    }
    return true;
  }

  async function importFile(file, target, input) {
    if (!file) return;
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (!extension || !["txt", "md"].includes(extension)) {
      setNotice({ tone: "error", text: "仅支持 .txt 或 .md 文件；PDF、Word 和图片不会被静默解析。" });
      input.value = "";
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      setNotice({ tone: "error", text: "文件不能超过 1 MB。请保留与岗位和项目经历直接相关的内容后重新导入。" });
      input.value = "";
      return;
    }
    let fileText;
    try {
      fileText = await file.text();
    } catch {
      setNotice({ tone: "error", text: `${file.name} 读取失败。请确认文件未损坏后重新导入。` });
      input.value = "";
      return;
    }
    const truncated = fileText.length > MAX_TEXT_LENGTH;
    const text = fileText.slice(0, MAX_TEXT_LENGTH);
    if (target === "jd") {
      setJobDescription(text);
      setInvalid((value) => ({ ...value, jd: false }));
    } else {
      setResumeText(text);
      setInvalid((value) => ({ ...value, resume: false }));
    }
    setFileNames((value) => ({ ...value, [target]: file.name }));
    setPlan(null);
    setNotice({ tone: truncated ? "info" : "success", text: truncated ? `${file.name} 已导入，并按上限保留前 50,000 字。` : `${file.name} 已导入。生成计划前仍可继续编辑。` });
    input.value = "";
  }

  async function generatePlan() {
    if (!validateSources()) return;
    setStatus("generating");
    setNotice({ tone: "info", text: "正在提取岗位约束、候选人经历与可用知识证据。" });
    try {
      const nextPlan = await requestJson("/api/prep", {
        method: "POST",
        body: JSON.stringify({ job_description: jobDescription, resume_text: resumeText }),
      });
      setPlan(nextPlan);
      setStatus("ready");
      setInspectorView("plan");
      setNotice({ tone: "success", text: "面试蓝图已生成。请先检查题目与证据路径，再开始面试。" });
    } catch (error) {
      setStatus("error");
      setNotice({ tone: "error", text: error.message });
    }
  }

  async function saveDraft() {
    if (!validateSources()) return;
    setStatus("saving");
    setNotice({ tone: "info", text: "正在保存到当前浏览器对应的匿名草稿。" });
    try {
      const draft = await requestJson("/api/interview-drafts", {
        method: "POST",
        body: JSON.stringify({
          draft_id: draftId || null,
          job_description: jobDescription,
          resume_text: resumeText,
          title: plan?.title || null,
          job_tags: jobTags.length ? jobTags : null,
        }),
      });
      setDraftId(draft.draft_id);
      storeDraftId(draft.draft_id);
      setStatus(plan ? "ready" : "idle");
      setNotice({ tone: "success", text: "草稿已保存在本机浏览器中。它不会跨浏览器或跨设备同步。" });
    } catch (error) {
      setStatus("error");
      setNotice({ tone: "error", text: error.message });
    }
  }

  async function restoreDraft() {
    const storedId = draftId || getStoredDraftId();
    if (!storedId) {
      setNotice({ tone: "info", text: "当前浏览器没有可恢复的匿名草稿。" });
      return;
    }
    setStatus("restoring");
    setNotice({ tone: "info", text: "正在恢复当前浏览器保存的资料。" });
    try {
      const draft = await requestJson(`/api/interview-drafts/${encodeURIComponent(storedId)}`);
      setDraftId(draft.draft_id);
      setJobDescription(draft.job_description || "");
      setResumeText(draft.resume_text || "");
      setPlan(null);
      setInvalid({ jd: false, resume: false });
      setFileNames({ jd: "来自匿名草稿", resume: "来自匿名草稿" });
      setStatus("idle");
      setActiveDocument("jd");
      setInspectorView("readiness");
      setNotice({ tone: "success", text: "草稿已恢复。为保证计划与内容一致，请重新生成面试蓝图。" });
    } catch (error) {
      clearStoredDraftId();
      setDraftId("");
      setStatus("error");
      setNotice({ tone: "error", text: `草稿恢复失败：${error.message}` });
    }
  }

  function clearWorkspace() {
    if (!clearArmed) {
      setClearArmed(true);
      setNotice({ tone: "warning", text: "再次点击“确认清空”将移除当前画布中的未保存内容；已保存的匿名草稿仍可恢复。" });
      return;
    }
    setJobDescription("");
    setResumeText("");
    setPlan(null);
    setInvalid({ jd: false, resume: false });
    setFileNames({ jd: "未导入文件", resume: "未导入文件" });
    setStatus("idle");
    setActiveDocument("jd");
    setInspectorView("readiness");
    setClearArmed(false);
    setNotice({ tone: "info", text: "当前画布已清空；此前保存的匿名草稿仍可恢复。" });
  }

  async function startInterview() {
    if (!plan || !validateSources()) return;
    setStatus("starting");
    setNotice({ tone: "info", text: "正在创建可恢复的面试会话。" });
    try {
      const session = await requestJson("/api/interviews", {
        method: "POST",
        body: JSON.stringify({ job_description: jobDescription, resume_text: resumeText }),
      });
      window.location.assign(`/interview?session_id=${encodeURIComponent(session.session_id)}`);
    } catch (error) {
      setStatus("error");
      setNotice({ tone: "error", text: error.message });
    }
  }

  const statusLabel = {
    idle: sourcesReady === 2 ? "可以生成计划" : "等待资料",
    generating: "正在建模",
    ready: "蓝图就绪",
    saving: "保存草稿",
    restoring: "恢复草稿",
    starting: "创建会话",
    error: "需要处理",
  }[status];
  const knowledgeStatus = prepContext.knowledge_status || (plan ? "unknown" : "pending");
  const knowledgeStatusLabel = KNOWLEDGE_STATUS_LABELS[knowledgeStatus] || knowledgeStatus.replaceAll("_", " ");

  const documentConfig = {
    jd: {
      code: "JD",
      title: "岗位任务书",
      description: "职责、技术约束与需要验证的能力边界。",
      label: "岗位 JD",
      value: jobDescription,
      fileName: fileNames.jd,
      invalid: invalid.jd,
      placeholder: "粘贴岗位 JD。优先保留职责、技术栈、业务规模、性能或稳定性要求……",
      onChange: (value) => {
        setJobDescription(value);
        if (plan) setNotice({ tone: "info", text: "岗位 JD 已修改。原面试计划已失效，请重新生成。" });
        else if (invalid.jd) setNotice(invalid.resume ? { tone: "error", text: "岗位 JD 已补充；候选人经历仍未填写。" } : null);
        setPlan(null);
        setInvalid((state) => ({ ...state, jd: false }));
      },
      onFile: (file, input) => importFile(file, "jd", input),
      DocumentIcon: Briefcase,
    },
    resume: {
      code: "CV",
      title: "候选人经历",
      description: "项目事实、技术选择、结果与个人职责。",
      label: "简历内容",
      value: resumeText,
      fileName: fileNames.resume,
      invalid: invalid.resume,
      placeholder: "粘贴候选人经历。优先保留项目背景、个人贡献、方案取舍和可验证结果……",
      onChange: (value) => {
        setResumeText(value);
        if (plan) setNotice({ tone: "info", text: "候选人经历已修改。原面试计划已失效，请重新生成。" });
        else if (invalid.resume) setNotice(invalid.jd ? { tone: "error", text: "候选人经历已补充；岗位 JD 仍未填写。" } : null);
        setPlan(null);
        setInvalid((state) => ({ ...state, resume: false }));
      },
      onFile: (file, input) => importFile(file, "resume", input),
      DocumentIcon: IdentificationCard,
    },
  };

  function renderDocument(type, compact = false) {
    const document = documentConfig[type];
    return <SourceEditor key={type} {...document} disabled={busy} compact={compact} />;
  }

  return (
    <div className="start-app-root">
      <a className="start-skip-link" href="#main-content">跳到主要内容</a>
      <header className="app-topbar start-app-topbar">
        <a className="start-brand" href="/prep" aria-label="面试智能体开始页">
          <span className="start-brand-mark" aria-hidden="true">IA</span>
          <span className="start-brand-copy"><strong>面试智能体</strong><small>面试配置工作台</small></span>
        </a>
        <nav className="app-nav start-nav" aria-label="主导航">
          <a href="/prep" aria-current="page">准备</a>
          <a href="/reports">报告</a>
          <a href="/help">帮助</a>
        </nav>
        <RuntimeStatus status={status} label={statusLabel} />
      </header>

      <main id="main-content" className="start-app-shell" tabIndex="-1">
        <nav className="start-activity-rail" aria-label="准备工作区">
          <button type="button" aria-pressed={inspectorView === "readiness"} aria-controls="inspector-panel" onClick={() => setInspectorView("readiness")}><span aria-hidden="true"><FileText size={20} weight="bold" focusable="false" /></span><strong>资料</strong></button>
          <button type="button" aria-pressed={inspectorView === "plan"} aria-controls="inspector-panel" onClick={() => setInspectorView("plan")}><span aria-hidden="true"><ClipboardText size={20} weight="bold" focusable="false" /></span><strong>蓝图</strong></button>
          <button type="button" aria-pressed={inspectorView === "evidence"} aria-controls="inspector-panel" onClick={() => setInspectorView("evidence")}><span aria-hidden="true"><Books size={20} weight="bold" focusable="false" /></span><strong>证据</strong></button>
        </nav>

        <section className="start-editor-workspace" aria-labelledby="workspace-title">
          <header className="start-workspace-head">
            <div className="start-workspace-title">
              <span className="start-workspace-mark" aria-hidden="true"><Files size={18} weight="bold" focusable="false" /></span>
              <div><h1 id="workspace-title">面试输入</h1><p>编辑两份源文档，生成有证据约束的技术面试计划。</p></div>
            </div>
            <div className="start-readiness" data-ready={sourcesReady === 2} aria-label={`资料完成度 ${sourcesReady}/2，${sourcesReady === 2 ? "资料完整" : "等待资料"}`}><span>{sourcesReady}/2</span><strong>{sourcesReady === 2 ? "资料完整" : "等待资料"}</strong></div>
          </header>

          <div className="start-editor-commandbar">
            <div className="start-document-tabs" role="tablist" aria-label="源文档">
              <button id="document-tab-jd" type="button" role="tab" aria-selected={activeDocument === "jd"} aria-controls="document-workspace" onClick={() => setActiveDocument("jd")}>
                <span className="start-tab-label"><Briefcase size={16} weight="bold" aria-hidden="true" focusable="false" />岗位 JD</span>
                <span className="start-tab-state" data-ready={Boolean(jobDescription.trim())}>{jobDescription.trim() ? <CheckCircle size={13} weight="fill" aria-hidden="true" focusable="false" /> : <Circle size={13} weight="regular" aria-hidden="true" focusable="false" />}{jobDescription.trim() ? "已填写" : "待填写"}</span>
              </button>
              <button id="document-tab-resume" type="button" role="tab" aria-selected={activeDocument === "resume"} aria-controls="document-workspace" onClick={() => setActiveDocument("resume")}>
                <span className="start-tab-label"><IdentificationCard size={16} weight="bold" aria-hidden="true" focusable="false" />候选人经历</span>
                <span className="start-tab-state" data-ready={Boolean(resumeText.trim())}>{resumeText.trim() ? <CheckCircle size={13} weight="fill" aria-hidden="true" focusable="false" /> : <Circle size={13} weight="regular" aria-hidden="true" focusable="false" />}{resumeText.trim() ? "已填写" : "待填写"}</span>
              </button>
              <button className="start-split-tab" id="document-tab-split" type="button" role="tab" aria-selected={activeDocument === "split"} aria-controls="document-workspace" onClick={() => setActiveDocument("split")}><span className="start-tab-label"><Columns size={16} weight="bold" aria-hidden="true" focusable="false" />并排查看</span></button>
            </div>
            <div className="start-editor-tools" aria-label="文档工具">
              <button className="button start-tool-button" type="button" onClick={saveDraft} disabled={busy} aria-busy={status === "saving" || undefined} data-state={status === "saving" ? "loading" : undefined}>{status === "saving" ? <SpinnerGap className="start-spinner" size={16} weight="bold" aria-hidden="true" focusable="false" /> : <FloppyDisk size={16} weight="bold" aria-hidden="true" focusable="false" />}<span>{status === "saving" ? "正在保存" : "保存草稿"}</span></button>
              <button className="button start-tool-button" type="button" onClick={restoreDraft} disabled={busy} aria-busy={status === "restoring" || undefined} data-state={status === "restoring" ? "loading" : undefined}>{status === "restoring" ? <SpinnerGap className="start-spinner" size={16} weight="bold" aria-hidden="true" focusable="false" /> : <ArrowCounterClockwise size={16} weight="bold" aria-hidden="true" focusable="false" />}<span>{status === "restoring" ? "正在恢复" : "恢复草稿"}</span></button>
              <button className="button start-tool-button start-tool-danger" type="button" onClick={clearWorkspace} disabled={busy || (!jobDescription && !resumeText)} data-state={clearArmed ? "confirm" : undefined} aria-label={clearArmed ? "确认清空当前画布" : "清空当前画布"}>{clearArmed ? <WarningCircle size={16} weight="fill" aria-hidden="true" focusable="false" /> : <Trash size={16} weight="bold" aria-hidden="true" focusable="false" />}<span>{clearArmed ? "确认清空" : "清空"}</span></button>
            </div>
          </div>

          <StatusNotice key={notice ? `${notice.tone}-${notice.text}` : "empty"} notice={notice} />

          <div
            id="document-workspace"
            className={activeDocument === "split" ? "start-document-canvas start-document-split" : "start-document-canvas"}
            role="tabpanel"
            aria-label="源文档编辑区"
          >
            {activeDocument === "split" ? <>{renderDocument("jd", true)}{renderDocument("resume", true)}</> : renderDocument(activeDocument)}
          </div>
        </section>

        <aside className="start-inspector" aria-labelledby="inspector-title">
          <header className="start-inspector-head">
            <div><span>工作面板</span><h2 id="inspector-title">{inspectorView === "evidence" ? "知识证据" : inspectorView === "readiness" ? "准备状态" : "面试计划"}</h2></div>
            <InspectorStatus status={status} label={statusLabel} />
          </header>
          <div className="start-inspector-tabs" role="tablist" aria-label="工作面板视图">
            <button id="inspector-tab-plan" type="button" role="tab" aria-selected={inspectorView === "plan"} aria-controls="inspector-panel" onClick={() => setInspectorView("plan")}><ListChecks size={16} weight="bold" aria-hidden="true" focusable="false" />计划</button>
            <button id="inspector-tab-evidence" type="button" role="tab" aria-selected={inspectorView === "evidence"} aria-controls="inspector-panel" onClick={() => setInspectorView("evidence")}><Books size={16} weight="bold" aria-hidden="true" focusable="false" />证据</button>
            <button id="inspector-tab-readiness" type="button" role="tab" aria-selected={inspectorView === "readiness"} aria-controls="inspector-panel" onClick={() => setInspectorView("readiness")}><ShieldCheck size={16} weight="bold" aria-hidden="true" focusable="false" />就绪</button>
          </div>

          <div id="inspector-panel" className="start-inspector-content" role="tabpanel" aria-labelledby={`inspector-tab-${inspectorView}`}>
            {inspectorView === "plan" ? (
              <section className="start-plan-panel" aria-label="面试计划">
                {plan ? (
                  <>
                    <header className="start-plan-summary">
                      <div><span>计划已生成</span><h3>{plan.title}</h3></div>
                      {jobTags.length ? <div className="start-job-tags" aria-label="岗位标签">{jobTags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}
                    </header>
                    <dl className="start-plan-metrics">
                      <div><dt><ListChecks size={14} weight="bold" aria-hidden="true" focusable="false" />问题</dt><dd>{questions.length || "暂无"}</dd></div>
                      <div><dt><Clock size={14} weight="bold" aria-hidden="true" focusable="false" />时长</dt><dd>{estimatedMinutes}</dd></div>
                      <div><dt><Books size={14} weight="bold" aria-hidden="true" focusable="false" />证据</dt><dd>{evidence.length}</dd></div>
                    </dl>
                    {questions.length ? <ol className="start-plan-list">{questions.map((question, index) => <PlanQuestion key={question.id || index} question={question} index={index} />)}</ol> : <InspectorEmpty icon={ClipboardText} title="计划没有返回可用题目。">请重新生成计划；系统不会使用示例题填充空列表。</InspectorEmpty>}
                  </>
                ) : (
                  <InspectorEmpty icon={ClipboardText} title="这里不会预填示例题。">补齐岗位 JD 与候选人经历后生成计划，真实题目会按顺序出现在这里。</InspectorEmpty>
                )}
              </section>
            ) : null}

            {inspectorView === "evidence" ? (
              <section className="start-evidence-panel" aria-label="知识证据">
                {plan ? (
                  <>
                    <header className="start-evidence-head">
                      <div><span>知识检索</span><h3>检索路径与证据</h3></div>
                      <KnowledgeStatus status={knowledgeStatus} />
                    </header>
                    <p className="start-evidence-summary">{prepContext.summary || "当前计划没有返回知识检索摘要。"}</p>
                    {topics.length ? <div className="start-topic-list" aria-label="考察主题">{topics.map((topic) => <span key={topic.id || topic.label}>{topic.label || topic.id}</span>)}</div> : null}
                    {evidence.length ? (
                      <div className="start-evidence-list">
                        {evidence.map((item, index) => (
                          <article key={item.evidence_id} data-evidence-id={item.evidence_id}>
                            <span>{String(index + 1).padStart(2, "0")}</span>
                            <div><strong>{item.title}</strong><p>{item.candidate_summary || "该证据已绑定到本次准备上下文。"}</p><code>{item.source_type === "theory" ? "理论资料" : item.source_type === "expert_benchmark" ? "专家基准" : item.source_type} / {item.evidence_id}</code></div>
                          </article>
                        ))}
                      </div>
                    ) : (
                      <div className="start-evidence-state" data-tone={prepContext.knowledge_status === "degraded" ? "warning" : "info"} role="status">
                        <span aria-hidden="true">{prepContext.knowledge_status === "degraded" ? <WarningCircle size={18} weight="fill" focusable="false" /> : <Info size={18} weight="bold" focusable="false" />}</span>
                        <div><strong>{prepContext.knowledge_status === "degraded" ? "知识检索已降级" : "暂无公开知识证据"}</strong><p>{prepContext.knowledge_status === "degraded" ? "面试仍可继续；系统不会展示不存在的引用。" : "计划仍可使用；证据列表保持为空，不会填充示例引用。"}</p></div>
                      </div>
                    )}
                  </>
                ) : (
                  <InspectorEmpty icon={Books} title="证据面板正在等待计划。">系统只展示真实可用的知识检索结果。</InspectorEmpty>
                )}
              </section>
            ) : null}

            {inspectorView === "readiness" ? (
              <section className="start-readiness-panel" aria-label="准备状态">
                <div className="start-readiness-list">
                  <ReadinessItem ready={Boolean(jobDescription.trim())} label="岗位 JD" value={jobDescription.trim() ? jobDescription.length.toLocaleString() + " 字" : "尚未填写"} />
                  <ReadinessItem ready={Boolean(resumeText.trim())} label="候选人经历" value={resumeText.trim() ? resumeText.length.toLocaleString() + " 字" : "尚未填写"} />
                  <ReadinessItem ready={Boolean(draftId)} label="匿名草稿" value={draftId ? "当前浏览器已关联" : "尚未保存"} />
                  <ReadinessItem ready={Boolean(plan)} label="面试计划" value={plan ? "已生成" : "尚未生成"} />
                </div>
                <div className="start-privacy-note"><ShieldCheck size={17} weight="bold" aria-hidden="true" focusable="false" /><p><strong>仅与当前浏览器关联</strong><span>资料用于当前面试流程；匿名草稿不会跨设备同步。</span></p></div>
              </section>
            ) : null}
          </div>

          <footer className="start-inspector-actions">
            <button className={plan ? "button start-button start-inspector-secondary" : "button start-button button-primary"} type="button" onClick={generatePlan} disabled={busy} aria-busy={status === "generating" || undefined} data-state={status === "generating" ? "loading" : undefined}>
              {status === "generating" ? <SpinnerGap className="start-spinner" size={18} weight="bold" aria-hidden="true" focusable="false" /> : <ListChecks size={18} weight="bold" aria-hidden="true" focusable="false" />}
              <span>{status === "generating" ? "正在生成面试计划" : plan ? "重新生成计划" : "生成面试计划"}</span>
            </button>
            <button className={plan ? "button start-button button-primary" : "button start-button start-inspector-secondary"} type="button" disabled={!plan || busy} onClick={startInterview} aria-busy={status === "starting" || undefined} data-state={status === "starting" ? "loading" : undefined}>
              {status === "starting" ? <SpinnerGap className="start-spinner" size={18} weight="bold" aria-hidden="true" focusable="false" /> : <ArrowRight size={18} weight="bold" aria-hidden="true" focusable="false" />}
              <span>{status === "starting" ? "正在创建面试" : "开始本次面试"}</span>
            </button>
          </footer>
        </aside>
      </main>

      <footer className="start-status-bar" aria-label="工作区状态">
        <StatusBarItem ready={Boolean(draftId)} label="草稿" value={draftId ? "已关联" : "未保存"} />
        <StatusBarItem ready={Boolean(jobDescription.trim())} label="JD" value={jobDescription.trim() ? "已填写" : "待填写"} />
        <StatusBarItem ready={Boolean(resumeText.trim())} label="经历" value={resumeText.trim() ? "已填写" : "待填写"} />
        <StatusBarItem ready={knowledgeStatus === "completed"} state={knowledgeStatus === "degraded" ? "warning" : knowledgeStatus === "empty" ? "info" : "idle"} label="知识" value={knowledgeStatusLabel} />
        <StatusBarItem ready={status === "ready"} state={status} label="请求" value={statusLabel} current />
      </footer>
    </div>
  );
}
