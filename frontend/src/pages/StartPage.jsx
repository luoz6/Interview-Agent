import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowCounterClockwise,
  ArrowRight,
  ArrowUp,
  ArrowsClockwise,
  Books,
  Briefcase,
  CheckCircle,
  Circle,
  Clock,
  ClipboardText,
  Columns,
  Copy,
  Eye,
  FileText,
  Files,
  FloppyDisk,
  IdentificationCard,
  Info,
  ListChecks,
  PencilSimple,
  Plus,
  ShieldCheck,
  SpinnerGap,
  Trash,
  UploadSimple,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import {
  copyableLocalDraft,
  createPlanEditorState,
  editableQuestions,
  hasLocalChanges,
  interviewPlanReducer,
  isLatestValidPlan,
  normalizePlanResponse,
  planEditorStatus,
  questionDraft,
} from "../interviewPlanState";
import {
  configurationMatchesSnapshot,
  createPlanConfiguration,
  describeConfigurationChanges,
  PLAN_DIFFICULTIES,
  PLAN_DURATIONS,
  PLAN_FOCUS_PRESETS,
  planConfigurationEstimate,
  planConfigurationPayload,
  QUESTION_MIX_PRESETS,
  updatePlanConfiguration,
} from "../interviewPlanConfiguration";
import {
  ConfirmationDialog,
} from "../components/ConfirmationDialog";
import { AppShell } from "../components/AppShell";
import { useConfirmationDialog } from "../components/useConfirmationDialog";
import { useDelayedPendingOperation } from "../hooks/useDelayedPending";
import {
  clearStableRequestId,
  deleteJson,
  getJson,
  patchJson,
  postJson,
  stableRequestId,
} from "../api/client";
import "../styles/pages/prep.css";

const DRAFT_KEYS = ["interview-agent:draft-id", "interviewDraftId"];
const CONFIGURATION_KEY = "interview-agent:plan-configuration-v1";
const MAX_FILE_BYTES = 1024 * 1024;
const MAX_TEXT_LENGTH = 50000;
const PENDING_STATES = ["generating", "saving", "restoring", "starting"];

const KNOWLEDGE_STATUS_LABELS = {
  keyword: "关键词准备",
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

function readLocalStorage(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeLocalStorage(key, value) {
  try {
    window.localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function removeLocalStorage(key) {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // Browser privacy settings may make storage unavailable.
  }
}

function getStoredDraftId() {
  return DRAFT_KEYS.map((key) => readLocalStorage(key)).find(Boolean) || "";
}

function storeDraftId(value) {
  DRAFT_KEYS.forEach((key) => writeLocalStorage(key, value));
}

function clearStoredDraftId() {
  DRAFT_KEYS.forEach((key) => removeLocalStorage(key));
}

function getStoredConfiguration() {
  try {
    const value = readLocalStorage(CONFIGURATION_KEY);
    return createPlanConfiguration(value ? JSON.parse(value) : null);
  } catch {
    return createPlanConfiguration();
  }
}

function normalizeApiError(error) {
  const payload = error?.payload || error?.body || {};
  const detail = payload?.detail;
  const message =
    (typeof detail === "string" && detail.trim())
    || detail?.message
    || payload?.message
    || error?.message
    || "请求失败";
  const normalized = new Error(message, error ? { cause: error } : undefined);
  normalized.name = error?.name || "Error";
  normalized.status = error?.status || 0;
  normalized.code = error?.code || payload?.code || detail?.code || "REQUEST_FAILED";
  normalized.payload = payload;
  normalized.body = payload;
  normalized.retryable = error?.retryable;
  return normalized;
}

async function requestJson(url, options = {}) {
  const { method = "GET", body, ...requestOptions } = options;
  const payload = body ? JSON.parse(body) : undefined;
  try {
    if (method === "POST") return await postJson(url, payload || {}, requestOptions);
    if (method === "PATCH") return await patchJson(url, payload || {}, requestOptions);
    if (method === "DELETE") return await deleteJson(url, requestOptions);
    return await getJson(url, requestOptions);
  } catch (error) {
    throw normalizeApiError(error);
  }
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

function PlanQuestion({
  question,
  index,
  total,
  draft,
  dirty,
  busy,
  onDraft,
  onSave,
  onDiscard,
  onMove,
  onRegenerate,
  onDelete,
}) {
  const kind =
    QUESTION_KIND_LABELS[question.question_type] ||
    question.question_type ||
    "综合考察";
  const bindingStatus = question.knowledge_binding?.status || "unbound";
  const bindingLabel = {
    valid: "证据有效",
    invalidated: "证据待重建",
    unbound: "未绑定证据",
  }[bindingStatus] || bindingStatus;
  const textId = "plan-question-" + question.question_id;
  const focusId = textId + "-focus";
  const saveDisabled =
    busy || !dirty || !draft.question_text.trim() || !draft.focus.trim();
  return (
    <li className="start-plan-question" data-dirty={dirty || undefined}>
      <form
        className="start-plan-question-form"
        onSubmit={(event) => {
          event.preventDefault();
          onSave(question.question_id);
        }}
      >
        <header className="start-plan-question-head">
          <span className="start-plan-index">
            {String(index + 1).padStart(2, "0")}
          </span>
          <div className="start-plan-question-identity">
            <strong>{kind}</strong>
            <span data-binding={bindingStatus}>{bindingLabel}</span>
          </div>
          <div className="start-plan-order-actions" aria-label={"调整第 " + (index + 1) + " 题顺序"}>
            <button
              type="button"
              onClick={() => onMove(question.question_id, index)}
              disabled={busy || index === 0}
              aria-label={"将第 " + (index + 1) + " 题上移"}
            >
              <ArrowUp size={15} weight="bold" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => onMove(question.question_id, index + 2)}
              disabled={busy || index === total - 1}
              aria-label={"将第 " + (index + 1) + " 题下移"}
            >
              <ArrowDown size={15} weight="bold" aria-hidden="true" />
            </button>
          </div>
        </header>
        <label className="start-plan-edit-label" htmlFor={textId}>
          问题内容
        </label>
        <textarea
          id={textId}
          value={draft.question_text}
          onChange={(event) =>
            onDraft(question.question_id, "question_text", event.target.value)
          }
          disabled={busy}
          rows={4}
          aria-describedby={textId + "-hint"}
        />
        <div className="start-plan-question-hint" id={textId + "-hint"}>
          <span>
            {draft.question_text.length.toLocaleString()} 字 · 建议包含一个清晰任务和可追问边界
          </span>
          {question.origin === "custom" ? <strong>自定义题</strong> : null}
          {question.origin === "regenerated" ? <strong>已换题</strong> : null}
        </div>
        <label className="start-plan-edit-label" htmlFor={focusId}>
          考察重点
        </label>
        <input
          id={focusId}
          type="text"
          value={draft.focus}
          onChange={(event) =>
            onDraft(question.question_id, "focus", event.target.value)
          }
          disabled={busy}
        />
        <footer className="start-plan-question-actions">
          <div>
            <button
              className="start-plan-action"
              type="button"
              onClick={(event) => onRegenerate(question.question_id, event.currentTarget)}
              disabled={busy}
            >
              <ArrowsClockwise size={15} weight="bold" aria-hidden="true" />
              换题
            </button>
            <button
              className="start-plan-action start-plan-action-danger"
              type="button"
              onClick={(event) => onDelete(question.question_id, index, event.currentTarget)}
              disabled={busy || total <= 1}
            >
              <Trash size={15} weight="bold" aria-hidden="true" />
              删除
            </button>
          </div>
          <div>
            {dirty ? (
              <button
                className="start-plan-action"
                type="button"
                onClick={() => onDiscard(question.question_id)}
                disabled={busy}
              >
                <X size={15} weight="bold" aria-hidden="true" />
                撤销
              </button>
            ) : null}
            <button
              className="start-plan-action start-plan-action-primary"
              type="submit"
              disabled={saveDisabled}
            >
              <FloppyDisk size={15} weight="bold" aria-hidden="true" />
              保存修改
            </button>
          </div>
        </footer>
      </form>
    </li>
  );
}

function RevisionState({ editor }) {
  const state = planEditorStatus(editor);
  const label = {
    empty: "等待计划",
    saved: "已保存",
    draft: "有本地修改",
    saving: "保存中",
    conflict: "版本冲突",
    failed: "保存失败",
  }[state];
  const Icon =
    state === "saved"
      ? CheckCircle
      : state === "conflict" || state === "failed"
        ? WarningCircle
        : state === "saving"
          ? SpinnerGap
          : PencilSimple;
  return (
    <span className="start-revision-state" data-state={state} role="status" aria-live="polite">
      <Icon
        className={state === "saving" ? "start-spinner" : undefined}
        size={14}
        weight={state === "saved" || state === "conflict" || state === "failed" ? "fill" : "bold"}
        aria-hidden="true"
      />
      <strong>{label}</strong>
      {editor.serverPlan ? <span>R{editor.serverPlan.revision}</span> : null}
    </span>
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

function ConfigurationChoice({ legend, name, options, value, onChange, disabled }) {
  return (
    <fieldset className="start-configuration-field" disabled={disabled}>
      <legend>{legend}</legend>
      <div className="start-configuration-options">
        {options.map((option) => {
          const optionValue = option.value ?? option;
          const label = option.label ?? `${option} 分钟`;
          return (
            <label key={optionValue} data-selected={value === optionValue || undefined}>
              <input
                type="radio"
                name={name}
                value={optionValue}
                checked={value === optionValue}
                onChange={() => onChange(optionValue)}
              />
              <span>
                <strong>{label}</strong>
                {option.description ? <small>{option.description}</small> : null}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

function PlanConfigurationPanel({ configuration, stale, disabled, onChange }) {
  const estimate = planConfigurationEstimate(configuration);
  const questionMixOptions = configuration.question_mix_preset === "saved"
    ? [
        {
          value: "saved",
          label: "当前 revision",
          description: "保留历史版本的精确计数；选择其他项后使用安全预设",
        },
        ...QUESTION_MIX_PRESETS,
      ]
    : QUESTION_MIX_PRESETS;
  const typeLabels = {
    project: "项目",
    technical: "技术",
    "system-design": "系统设计",
    behavioral: "行为",
  };
  return (
    <section className="start-configuration-panel" aria-labelledby="plan-configuration-title">
      <header>
        <div>
          <span>生成设置</span>
          <h3 id="plan-configuration-title">面试范围与节奏</h3>
        </div>
        <span className="start-configuration-state" data-stale={stale || undefined} role="status">
          {stale ? "待重新生成" : "配置已同步"}
        </span>
      </header>
      <ConfigurationChoice
        legend="难度"
        name="plan-difficulty"
        options={PLAN_DIFFICULTIES}
        value={configuration.difficulty}
        onChange={(value) => onChange("difficulty", value)}
        disabled={disabled}
      />
      <ConfigurationChoice
        legend="目标时长"
        name="plan-duration"
        options={PLAN_DURATIONS}
        value={configuration.target_duration_minutes}
        onChange={(value) => onChange("target_duration_minutes", value)}
        disabled={disabled}
      />
      <ConfigurationChoice
        legend="考察重点"
        name="plan-focus"
        options={PLAN_FOCUS_PRESETS}
        value={configuration.focus_preset}
        onChange={(value) => onChange("focus_preset", value)}
        disabled={disabled}
      />
      <ConfigurationChoice
        legend="题型安全预设"
        name="plan-question-mix"
        options={questionMixOptions}
        value={configuration.question_mix_preset}
        onChange={(value) => onChange("question_mix_preset", value)}
        disabled={disabled}
      />
      <div className="start-configuration-budget" aria-label="计划预算估算">
        <div><span>预计主问题</span><strong>{estimate.questionCount} 道</strong></div>
        <div><span>目标时长</span><strong>约 {estimate.targetMinutes} 分钟</strong></div>
        <div><span>预计追问</span><strong>{estimate.expectedFollowups} 次</strong></div>
        <div><span>单题硬上限</span><strong>最多 2 次</strong></div>
      </div>
      <div className="start-configuration-mix" aria-label="题型数量">
        {Object.entries(configuration.question_type_budget).map(([type, count]) => (
          <span key={type}><strong>{typeLabels[type]}</strong>{count}</span>
        ))}
      </div>
      <p className="start-configuration-disclaimer">
        预计时长不是精确结束承诺；实际进度取决于回答长度、追问和操作节奏。配置只影响出题，
        不改变评分 rubric。
      </p>
      {stale ? (
        <p className="start-configuration-stale" role="status">
          当前 revision 仍保留旧配置。开始按钮已禁用，请明确确认重新生成后采用新配置。
        </p>
      ) : null}
    </section>
  );
}

function RuntimeStatus({ status, label, showSpinner }) {
  const StateIcon = status === "ready" ? CheckCircle : status === "error" ? WarningCircle : Circle;
  return (
    <div className="start-runtime" data-state={status} role="status" aria-live="polite">
      <span className="start-runtime-icon" aria-hidden="true">
        {showSpinner
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
  const [editor, dispatchEditor] = useReducer(
    interviewPlanReducer,
    undefined,
    () => createPlanEditorState(),
  );
  const [status, setStatus] = useState("idle");
  const [notice, setNotice] = useState(null);
  const [draftId, setDraftId] = useState(() => getStoredDraftId());
  const [draftDurability, setDraftDurability] = useState("");
  const [fileNames, setFileNames] = useState({ jd: "未导入文件", resume: "未导入文件" });
  const [invalid, setInvalid] = useState({ jd: false, resume: false });
  const [activeDocument, setActiveDocument] = useState("jd");
  const [inspectorView, setInspectorView] = useState("plan");
  const [focusTarget, setFocusTarget] = useState("");
  const { confirmation, openConfirmation, closeConfirmation } = useConfirmationDialog();
  const [historyOpen, setHistoryOpen] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  const [customQuestion, setCustomQuestion] = useState({
    question_text: "",
    focus: "",
  });
  const [configuration, setConfiguration] = useState(getStoredConfiguration);
  const automaticRestoreStarted = useRef(false);

  const plan = editor.serverPlan;
  const questions = editableQuestions(plan);
  const configurationSnapshot = useMemo(
    () => plan?.plan?.configuration_snapshot || null,
    [plan?.plan?.configuration_snapshot],
  );
  const defaultConfigurationSnapshot = useMemo(
    () => planConfigurationPayload(createPlanConfiguration()),
    [],
  );
  const configurationIsDefault = configurationMatchesSnapshot(
    configuration,
    defaultConfigurationSnapshot,
  );
  const configurationStale = Boolean(
    plan && !configurationMatchesSnapshot(configuration, configurationSnapshot),
  );
  const prepContext = plan?.prep_context || {};
  const topics = prepContext.topics || [];
  const evidence = prepContext.evidence_refs || [];
  const jobTags = plan?.job_tags || [];
  const draftDurabilityLabel = draftDurability === "postgres"
    ? "持久保存"
    : draftDurability
      ? "进程内临时保存"
      : draftId
        ? "读取中"
        : "未保存";
  const busy =
    PENDING_STATES.includes(status) ||
    Boolean(editor.pendingOperation);
  const { showSpinner: showRuntimeSpinner } = useDelayedPendingOperation(
    editor.pendingOperation ? "saving" : status,
    {
      pendingStates: PENDING_STATES,
      delay: 150,
      minimumVisible: 300,
    },
  );
  const sourcesReady = Number(Boolean(jobDescription.trim())) + Number(Boolean(resumeText.trim()));
  const estimatedMinutes = useMemo(
    () => {
      if (!questions.length) return "待生成";
      const total = questions.reduce(
        (sum, question) => sum + (question.expected_minutes || 0),
        0,
      );
      return total ? total + " 分钟" : questions.length * 5 + " 分钟";
    },
    [questions],
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
    if (!configurationSnapshot) return;
    setConfiguration(createPlanConfiguration(configurationSnapshot));
  }, [configurationSnapshot]);

  useEffect(() => {
    writeLocalStorage(
      CONFIGURATION_KEY,
      JSON.stringify(planConfigurationPayload(configuration)),
    );
  }, [configuration]);

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
      setNotice({ tone: "error", text: "仅支持 .txt 或 .md 文件；PDF、Word 和图片不会被静默解析。请复制其中的文本后粘贴。" });
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
    dispatchEditor({ type: "INVALIDATE_SOURCE" });
    setStatus("idle");
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
        body: JSON.stringify({
          job_description: jobDescription,
          resume_text: resumeText,
          configuration: planConfigurationPayload(configuration),
        }),
      });
      dispatchEditor({
        type: "LOAD_SERVER_PLAN",
        plan: normalizePlanResponse(nextPlan),
      });
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
          plan_family_id: plan?.plan_family_id || null,
          latest_plan_revision_id: plan?.plan_revision_id || null,
        }),
      });
      automaticRestoreStarted.current = true;
      setDraftId(draft.draft_id);
      setDraftDurability(draft.durability);
      storeDraftId(draft.draft_id);
      setStatus(plan ? "ready" : "idle");
      setNotice({ tone: "success", text: `草稿已${draft.durability === "postgres" ? "持久保存" : "进程内临时保存"}；它不会跨浏览器或跨设备同步。` });
    } catch (error) {
      setStatus("error");
      setNotice({ tone: "error", text: error.message });
    }
  }

  const restoreDraft = useCallback(async () => {
    const storedId = draftId || getStoredDraftId();
    if (!storedId) {
      setNotice({ tone: "info", text: "当前浏览器没有可恢复的匿名草稿。" });
      return;
    }
    setStatus("restoring");
    setNotice({ tone: "info", text: "正在恢复当前浏览器保存的资料。" });
    try {
      const draft = await requestJson(`/api/interview-drafts/${encodeURIComponent(storedId)}`);
      let restoredPlan = null;
      if (draft.plan_status === "active" && draft.plan_family_id && draft.latest_plan_revision_id) {
        const revision = await requestJson(
          `/api/interview-plans/${encodeURIComponent(draft.plan_family_id)}/revisions/${encodeURIComponent(draft.latest_plan_revision_id)}`,
        );
        restoredPlan = normalizePlanResponse(revision, {
          job_tags: draft.job_tags || [],
        });
      }

      setDraftId(draft.draft_id);
      setDraftDurability(draft.durability);
      setJobDescription(draft.job_description || "");
      setResumeText(draft.resume_text || "");
      if (restoredPlan) {
        dispatchEditor({
          type: "LOAD_SERVER_PLAN",
          plan: restoredPlan,
        });
      } else {
        dispatchEditor({ type: "INVALIDATE_SOURCE" });
      }
      setInvalid({ jd: false, resume: false });
      setFileNames({ jd: "来自匿名草稿", resume: "来自匿名草稿" });
      setStatus(draft.plan_status === "active" ? "ready" : "idle");
      setActiveDocument("jd");
      setInspectorView("readiness");
      setNotice(
        draft.plan_status === "stale"
          ? { tone: "warning", text: "草稿已恢复，但源文档已经变更。请重新生成计划后再开始面试。" }
          : { tone: "success", text: "草稿和对应的同一份计划修订已恢复。" },
      );
    } catch (error) {
      if ([404, 410].includes(error.status)) {
        clearStoredDraftId();
        setDraftId("");
        setDraftDurability("");
      }
      setStatus("error");
      setNotice({ tone: "error", text: `草稿恢复失败：${error.message}` });
    }
  }, [draftId]);

  useEffect(() => {
    if (automaticRestoreStarted.current || !draftId) return;
    automaticRestoreStarted.current = true;
    void restoreDraft();
  }, [draftId, restoreDraft]);

  function requestRestoreDraft(event) {
    const storedId = draftId || getStoredDraftId();
    if (!storedId) {
      restoreDraft();
      return;
    }
    const dirtyQuestionCount = Object.keys(editor.localDrafts).length;
    const hasWorkspaceContent = Boolean(
      jobDescription.trim()
      || resumeText.trim()
      || plan
      || dirtyQuestionCount
      || !configurationIsDefault,
    );
    if (!hasWorkspaceContent) {
      restoreDraft();
      return;
    }
    const details = [];
    if (jobDescription.trim() || resumeText.trim()) {
      details.push("当前画布中的岗位 JD 和候选人经历会被替换");
    }
    if (plan) {
      details.push(`当前 R${plan.revision} 计划画布会被草稿关联的修订替换`);
    }
    if (dirtyQuestionCount) {
      details.push(`${dirtyQuestionCount} 道未保存的本地题目修改会被清除`);
    }
    if (!configurationIsDefault) {
      details.push("若草稿关联有效计划修订，当前未保存的计划配置会被其替换");
    }
    details.push("已保存的匿名草稿不会被删除，恢复失败也不会清空当前画布");
    openConfirmation({
      title: "用已保存草稿替换当前画布？",
      description: "恢复会读取当前浏览器关联的匿名草稿并替换当前资料；草稿关联有效计划修订时，也会替换配置和本地计划编辑。",
      details,
      confirmLabel: "确认恢复草稿",
      onConfirm: async () => {
        closeConfirmation({ restoreFocus: false });
        await restoreDraft();
      },
    }, event.currentTarget);
  }

  function clearWorkspace() {
    setJobDescription("");
    setResumeText("");
    setConfiguration(createPlanConfiguration());
    dispatchEditor({ type: "INVALIDATE_SOURCE" });
    setInvalid({ jd: false, resume: false });
    setFileNames({ jd: "未导入文件", resume: "未导入文件" });
    setStatus("idle");
    setActiveDocument("jd");
    setInspectorView("readiness");
    setNotice({ tone: "info", text: "当前画布已清空；此前保存的匿名草稿仍可恢复。" });
  }

  function requestClearWorkspace(event) {
    const dirtyQuestionCount = Object.keys(editor.localDrafts).length;
    const details = [
      "岗位 JD、候选人经历和当前计划画布会被清空",
      "计划配置会恢复为默认值",
    ];
    if (dirtyQuestionCount) {
      details.push(`${dirtyQuestionCount} 道未保存的本地题目修改会被清除`);
    }
    details.push("已保存的匿名草稿不会被删除，之后仍可恢复");
    openConfirmation({
      title: "清空当前画布？",
      description: "该操作只清空当前画布，不会删除浏览器已保存的匿名草稿。",
      details,
      confirmLabel: "确认清空画布",
      tone: "danger",
      onConfirm: () => {
        closeConfirmation();
        clearWorkspace();
      },
    }, event.currentTarget);
  }

  function changeConfiguration(field, value) {
    const next = updatePlanConfiguration(configuration, field, value);
    setConfiguration(next);
    if (plan) {
      const matches = configurationMatchesSnapshot(next, configurationSnapshot);
      setNotice(
        matches
          ? {
              tone: "success",
              text: "配置已恢复为当前 revision 的已保存值，可以继续开始面试。",
            }
          : {
              tone: "warning",
              text: "生成配置已修改。当前 revision 保留旧配置；请明确重新生成后再开始。",
            },
      );
    } else {
      setNotice({
        tone: "info",
        text: "生成配置已更新，将在生成计划时由后端验证并冻结到 revision。",
      });
    }
  }

  function requestId(prefix) {
    const generated = window.crypto?.randomUUID?.();
    return generated || prefix + "-" + Date.now() + "-" + Math.random().toString(16).slice(2);
  }

  function draftsWithout(questionId) {
    const next = { ...editor.localDrafts };
    delete next[questionId];
    return next;
  }

  async function loadHistoryFor(targetPlan = plan) {
    if (!targetPlan?.plan_family_id) return [];
    dispatchEditor({ type: "HISTORY_LOADING" });
    try {
      const payload = await requestJson(
        "/api/interview-plans/" +
          encodeURIComponent(targetPlan.plan_family_id) +
          "/revisions",
      );
      const history = payload.revisions || [];
      dispatchEditor({ type: "HISTORY_SUCCESS", history });
      return history;
    } catch (error) {
      dispatchEditor({ type: "HISTORY_FAILURE", message: error.message });
      return [];
    }
  }

  async function performRevisionOperation({
    kind,
    questionId = null,
    send,
    successText,
    localDrafts = editor.localDrafts,
  }) {
    const operationRequestId = requestId(kind);
    dispatchEditor({
      type: "OPERATION_PENDING",
      kind,
      questionId,
      requestId: operationRequestId,
    });
    try {
      const response = await send(operationRequestId);
      const nextPlan = normalizePlanResponse(response, plan);
      dispatchEditor({
        type: "OPERATION_SUCCESS",
        plan: nextPlan,
        localDrafts,
      });
      setStatus("ready");
      setNotice({ tone: "success", text: successText });
      if (historyOpen) await loadHistoryFor(nextPlan);
      return true;
    } catch (error) {
      const conflict =
        error.status === 409 ||
        error.payload?.code === "plan_revision_conflict";
      if (conflict) {
        dispatchEditor({
          type: "OPERATION_CONFLICT",
          currentRevision: error.payload?.current_revision || null,
          message: "服务端已经保存了更新版本。你的本地输入仍保留。",
        });
        setNotice({
          tone: "warning",
          text: "检测到版本冲突。请先查看服务端版本或复制本地内容，不会自动覆盖。",
        });
        setStatus("error");
      } else {
        dispatchEditor({
          type: "OPERATION_FAILURE",
          message: error.message,
          status: error.status,
          code: error.payload?.detail?.code || error.payload?.code,
        });
        setNotice({
          tone: "error",
          text: "计划操作失败，本地输入已保留：" + error.message,
        });
        setStatus("error");
      }
      return false;
    }
  }

  function patchPlan(operations, operationRequestId) {
    return requestJson(
      "/api/interview-plans/" + encodeURIComponent(plan.plan_family_id),
      {
        method: "PATCH",
        body: JSON.stringify({
          expected_revision: plan.revision,
          request_id: operationRequestId,
          operations,
        }),
      },
    );
  }

  async function saveQuestion(questionId) {
    const question = questions.find((item) => item.question_id === questionId);
    if (!question) return;
    const draft = questionDraft(editor, question);
    const operations = [];
    if (draft.question_text !== question.question_text) {
      operations.push({
        op: "edit_question_text",
        question_id: questionId,
        question_text: draft.question_text,
      });
    }
    if (draft.focus !== question.focus) {
      operations.push({
        op: "edit_focus",
        question_id: questionId,
        focus: draft.focus,
      });
    }
    if (!operations.length) {
      dispatchEditor({ type: "DISCARD_LOCAL_QUESTION", questionId });
      return;
    }
    await performRevisionOperation({
      kind: "edit_question",
      questionId,
      send: (operationRequestId) => patchPlan(operations, operationRequestId),
      successText: "题目修改已保存为新的计划修订。",
      localDrafts: draftsWithout(questionId),
    });
  }

  async function moveQuestion(questionId, toPosition) {
    await performRevisionOperation({
      kind: "move_question",
      questionId,
      send: (operationRequestId) =>
        patchPlan(
          [{ op: "move_question", question_id: questionId, to_position: toPosition }],
          operationRequestId,
        ),
      successText: "题目顺序已保存。",
    });
  }

  async function regenerateQuestion(questionId, trigger = null, confirmed = false) {
    if (editor.localDrafts[questionId] && !confirmed) {
      openConfirmation({
        title: "放弃本地修改并替换这道题？",
        description: "当前题目还有未保存输入。换题成功后会使用服务端返回的新题目身份和内容。",
        confirmLabel: "放弃修改并换题",
        onConfirm: async () => {
          closeConfirmation({ restoreFocus: false });
          await regenerateQuestion(questionId, null, true);
        },
      }, trigger);
      return;
    }
    await performRevisionOperation({
      kind: "regenerate_question",
      questionId,
      send: (operationRequestId) =>
        requestJson(
          "/api/interview-plans/" +
            encodeURIComponent(plan.plan_family_id) +
            "/questions/" +
            encodeURIComponent(questionId) +
            "/regenerate",
          {
            method: "POST",
            body: JSON.stringify({
              expected_revision: plan.revision,
              request_id: operationRequestId,
            }),
          },
        ),
      successText: "替换题目已由服务端生成并保存。",
      localDrafts: draftsWithout(questionId),
    });
  }

  function confirmDeleteQuestion(questionId, index, trigger) {
    openConfirmation({
      title: "删除第 " + (index + 1) + " 题？",
      description: "删除会创建新的计划修订，其他题目的稳定 ID 和内容不会改变。",
      confirmLabel: "删除并保存",
      tone: "danger",
      onConfirm: async () => {
        closeConfirmation({ restoreFocus: false });
        await performRevisionOperation({
          kind: "delete_question",
          questionId,
          send: (operationRequestId) =>
            patchPlan(
              [{ op: "delete_question", question_id: questionId }],
              operationRequestId,
            ),
          successText: "题目已删除，其他题目身份保持不变。",
          localDrafts: draftsWithout(questionId),
        });
      },
    }, trigger);
  }

  async function addCustomQuestion(event) {
    event.preventDefault();
    const questionText = customQuestion.question_text.trim();
    const focus = customQuestion.focus.trim();
    if (!questionText || !focus) {
      setNotice({ tone: "error", text: "自定义题需要同时填写问题内容和考察重点。" });
      return;
    }
    const difficulty =
      plan?.plan?.configuration_snapshot?.difficulty || "intermediate";
    const succeeded = await performRevisionOperation({
      kind: "add_custom_question",
      send: (operationRequestId) =>
        patchPlan(
          [
            {
              op: "add_custom_question",
              question_text: questionText,
              focus,
              question_type: "technical",
              difficulty,
              expected_minutes: 6,
              expected_followups: 0,
            },
          ],
          operationRequestId,
        ),
      successText: "自定义题已保存；它明确标记为未绑定知识证据。",
    });
    if (succeeded) {
      setCustomQuestion({ question_text: "", focus: "" });
      setCustomOpen(false);
    }
  }

  function confirmRegenerateAll(event) {
    const adjusted = questions.filter((question) =>
      ["edited", "custom", "regenerated"].includes(question.origin),
    );
    const dirtyCount = Object.keys(editor.localDrafts).length;
    const details = [];
    if (adjusted.length) {
      details.push(adjusted.length + " 道已手工调整或换过的题将被替换");
    }
    if (dirtyCount) {
      details.push(dirtyCount + " 道尚未保存的本地输入将被清除");
    }
    const configurationChanges = describeConfigurationChanges(
      configuration,
      configurationSnapshot,
    );
    if (configurationChanges.length) {
      details.push("新 revision 将采用：" + configurationChanges.join("、"));
    }
    if (!details.length) details.push("当前整份计划将由服务端重新生成");
    openConfirmation({
      title: configurationStale ? "使用新配置重新生成计划？" : "重新生成整份计划？",
      description: "成功后服务端会验证配置并返回唯一权威的新 revision；当前计划仍保留在历史版本中。",
      details,
      confirmLabel: configurationStale ? "确认采用新配置" : "确认重新生成",
      onConfirm: async () => {
        closeConfirmation({ restoreFocus: false });
        await performRevisionOperation({
          kind: "regenerate_all",
          send: (operationRequestId) =>
            requestJson(
              "/api/interview-plans/" +
                encodeURIComponent(plan.plan_family_id) +
                "/regenerate",
              {
                method: "POST",
                body: JSON.stringify({
                  expected_revision: plan.revision,
                  request_id: operationRequestId,
                  confirmed: true,
                  configuration: planConfigurationPayload(configuration),
                }),
              },
            ),
          successText: configurationStale
            ? "新配置已由服务端验证并冻结到最新 revision。"
            : "整份计划已重新生成并保存。",
          localDrafts: {},
        });
      },
    }, event?.currentTarget);
  }

  function confirmRestoreRevision(revision, trigger) {
    openConfirmation({
      title: "恢复到 R" + revision.revision + "？",
      description: "恢复不会删除历史，而是以该版本内容创建一个新的最新 revision。",
      details: [
        revision.question_count + " 道题",
        "创建原因：" + revision.created_reason,
      ],
      confirmLabel: "确认恢复",
      onConfirm: async () => {
        closeConfirmation({ restoreFocus: false });
        await performRevisionOperation({
          kind: "restore_revision",
          send: (operationRequestId) =>
            patchPlan(
              [
                {
                  op: "restore_revision",
                  target_revision_id: revision.plan_revision_id,
                },
              ],
              operationRequestId,
            ),
          successText: "历史内容已恢复为新的最新修订。",
          localDrafts: {},
        });
      },
    }, trigger);
  }

  async function viewServerVersion() {
    let revisionId = editor.conflict?.currentRevision?.plan_revision_id;
    if (!revisionId) {
      const history = await loadHistoryFor();
      revisionId = history[0]?.plan_revision_id;
    }
    if (!revisionId) {
      setNotice({ tone: "error", text: "无法定位服务端最新 revision。" });
      return;
    }
    try {
      const response = await requestJson(
        "/api/interview-plans/" +
          encodeURIComponent(plan.plan_family_id) +
          "/revisions/" +
          encodeURIComponent(revisionId),
      );
      dispatchEditor({
        type: "SERVER_PREVIEW_LOADED",
        plan: normalizePlanResponse(response, plan),
      });
    } catch (error) {
      setNotice({ tone: "error", text: "服务端版本读取失败：" + error.message });
    }
  }

  async function copyLocalInput() {
    const text = copyableLocalDraft(editor);
    if (!text) {
      setNotice({ tone: "info", text: "当前没有可复制的本地题目文本。" });
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setNotice({ tone: "success", text: "本地题目内容已复制，可安全粘贴到其他位置。" });
    } catch {
      setNotice({ tone: "error", text: "浏览器拒绝剪贴板访问，请手动复制题目输入。" });
    }
  }

  function adoptServerPreview() {
    if (!editor.serverPreview) return;
    dispatchEditor({
      type: "LOAD_SERVER_PLAN",
      plan: editor.serverPreview,
      keepHistory: true,
    });
    setStatus("ready");
    setNotice({ tone: "success", text: "已切换到服务端最新版本，本地冲突状态已清除。" });
  }

  async function refreshServerRevision() {
    const history = await loadHistoryFor();
    const latest = history[0];
    if (!latest) return;
    try {
      const response = await requestJson(
        "/api/interview-plans/" +
          encodeURIComponent(plan.plan_family_id) +
          "/revisions/" +
          encodeURIComponent(latest.plan_revision_id),
      );
      dispatchEditor({
        type: "OPERATION_SUCCESS",
        plan: normalizePlanResponse(response, plan),
        localDrafts: editor.localDrafts,
        history: editor.history,
      });
      setStatus("ready");
      setNotice({ tone: "success", text: "已重新载入服务端最新 revision。" });
    } catch (error) {
      setNotice({ tone: "error", text: "重新载入失败：" + error.message });
    }
  }

  async function startInterview() {
    if (!plan || !validateSources()) return;
    if (!isLatestValidPlan(editor) || configurationStale) {
      setNotice({
        tone: "error",
        text: configurationStale
          ? "配置已修改但尚未进入 revision。请先明确重新生成计划。"
          : "只有已保存且无冲突的最新有效 revision 可以开始。请先保存或处理当前计划状态。",
      });
      return;
    }
    setStatus("starting");
    setNotice({ tone: "info", text: "正在创建可恢复的面试会话。" });
    try {
      const requestScope = `session-start:${plan.plan_revision_id}`;
      const session = await requestJson("/api/interviews", {
        method: "POST",
        body: JSON.stringify({
          plan_revision_id: plan.plan_revision_id,
          expected_revision: plan.revision,
          plan_sha256: plan.plan_sha256,
          request_id: stableRequestId(requestScope),
        }),
      });
      clearStableRequestId(requestScope);
      window.location.assign(`/interview?session_id=${encodeURIComponent(session.session_id)}`);
    } catch (error) {
      if (
        error.status === 409
        && error.payload?.code === "session_start_request_conflict"
      ) {
        setNotice({
          tone: "error",
          text: "This start request ID is already bound to a different plan revision. Refresh and retry.",
        });
      } else if (error.status === 409) {
        dispatchEditor({
          type: "OPERATION_CONFLICT",
          currentRevision: error.payload?.current_revision || null,
          message: "启动前服务端 revision 已变化。当前页面不会自动覆盖。",
        });
        setNotice({
          tone: "warning",
          text: "计划已不是服务端最新版本。请查看服务端版本后再开始。",
        });
      } else {
        setNotice({ tone: "error", text: error.message });
      }
      setStatus("error");
    }
  }

  const runtimeStatus = editor.pendingOperation ? "saving" : status;
  const statusLabel = {
    idle: sourcesReady === 2 ? "可以生成计划" : "等待资料",
    generating: "正在建模",
    ready: "蓝图就绪",
    saving: "保存草稿",
    restoring: "恢复草稿",
    starting: "创建会话",
    error: "需要处理",
  }[runtimeStatus];
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
        dispatchEditor({ type: "INVALIDATE_SOURCE" });
        setStatus("idle");
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
        dispatchEditor({ type: "INVALIDATE_SOURCE" });
        setStatus("idle");
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
    <AppShell status={<RuntimeStatus status={runtimeStatus} label={statusLabel} showSpinner={showRuntimeSpinner} />}>

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
              <button className="button start-tool-button" type="button" onClick={requestRestoreDraft} disabled={busy} aria-busy={status === "restoring" || undefined} data-state={status === "restoring" ? "loading" : undefined}>{status === "restoring" ? <SpinnerGap className="start-spinner" size={16} weight="bold" aria-hidden="true" focusable="false" /> : <ArrowCounterClockwise size={16} weight="bold" aria-hidden="true" focusable="false" />}<span>{status === "restoring" ? "正在恢复" : "恢复草稿"}</span></button>
              <button className="button start-tool-button start-tool-danger" type="button" onClick={requestClearWorkspace} disabled={busy || (!jobDescription && !resumeText && !plan && configurationIsDefault)} aria-label="清空当前画布"><Trash size={16} weight="bold" aria-hidden="true" focusable="false" /><span>清空</span></button>
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
            <InspectorStatus status={runtimeStatus} label={statusLabel} />
          </header>
          <div className="start-inspector-tabs" role="tablist" aria-label="工作面板视图">
            <button id="inspector-tab-plan" type="button" role="tab" aria-selected={inspectorView === "plan"} aria-controls="inspector-panel" onClick={() => setInspectorView("plan")}><ListChecks size={16} weight="bold" aria-hidden="true" focusable="false" />计划</button>
            <button id="inspector-tab-evidence" type="button" role="tab" aria-selected={inspectorView === "evidence"} aria-controls="inspector-panel" onClick={() => setInspectorView("evidence")}><Books size={16} weight="bold" aria-hidden="true" focusable="false" />证据</button>
            <button id="inspector-tab-readiness" type="button" role="tab" aria-selected={inspectorView === "readiness"} aria-controls="inspector-panel" onClick={() => setInspectorView("readiness")}><ShieldCheck size={16} weight="bold" aria-hidden="true" focusable="false" />就绪</button>
          </div>

          <div id="inspector-panel" className="start-inspector-content" role="tabpanel" aria-labelledby={`inspector-tab-${inspectorView}`}>
            {inspectorView === "plan" ? (
              <section className="start-plan-panel" aria-label="面试计划">
                <PlanConfigurationPanel
                  configuration={configuration}
                  stale={configurationStale}
                  disabled={busy}
                  onChange={changeConfiguration}
                />
                {plan ? (
                  <>
                    <header className="start-plan-summary">
                      <div className="start-plan-summary-line">
                        <div><span>计划已生成</span><h3>{plan.title}</h3></div>
                        <RevisionState editor={editor} />
                      </div>
                      {jobTags.length ? <div className="start-job-tags" aria-label="岗位标签">{jobTags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}
                    </header>
                    <dl className="start-plan-metrics">
                      <div><dt><ListChecks size={14} weight="bold" aria-hidden="true" focusable="false" />问题</dt><dd>{questions.length || "暂无"}</dd></div>
                      <div><dt><Clock size={14} weight="bold" aria-hidden="true" focusable="false" />时长</dt><dd>{estimatedMinutes}</dd></div>
                      <div><dt><Books size={14} weight="bold" aria-hidden="true" focusable="false" />证据</dt><dd>{evidence.length}</dd></div>
                    </dl>
                    <div className="start-plan-commandbar" aria-label="计划操作">
                      <button
                        type="button"
                        onClick={() => setCustomOpen((value) => !value)}
                        disabled={busy || questions.length >= 10}
                        aria-expanded={customOpen}
                        aria-describedby={questions.length >= 10 ? "plan-capacity-note" : undefined}
                      >
                        <Plus size={15} weight="bold" aria-hidden="true" />
                        添加题目
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          const next = !historyOpen;
                          setHistoryOpen(next);
                          if (next) loadHistoryFor();
                        }}
                        disabled={busy}
                        aria-expanded={historyOpen}
                      >
                        <ArrowCounterClockwise size={15} weight="bold" aria-hidden="true" />
                        历史版本
                      </button>
                      <button
                        type="button"
                        onClick={confirmRegenerateAll}
                        disabled={busy}
                      >
                        <ArrowsClockwise size={15} weight="bold" aria-hidden="true" />
                        全部换题
                      </button>
                    </div>
                    {questions.length >= 10 ? (
                      <p id="plan-capacity-note" className="start-plan-draft-note" role="note" tabIndex="0">
                        已达到 10 题上限，请先删除一道题再添加。
                      </p>
                    ) : null}

                    {editor.conflict ? (
                      <section className="start-plan-state-panel" data-state="conflict" role="alert">
                        <header>
                          <WarningCircle size={18} weight="fill" aria-hidden="true" />
                          <div>
                            <strong>计划版本冲突</strong>
                            <span>
                              {editor.conflict.currentRevision
                                ? "服务端当前为 R" + editor.conflict.currentRevision.revision
                                : "服务端存在更新版本"}
                            </span>
                          </div>
                        </header>
                        <p>{editor.conflict.message}</p>
                        <div>
                          <button type="button" onClick={viewServerVersion}>
                            <Eye size={15} weight="bold" aria-hidden="true" />
                            查看服务端版本
                          </button>
                          <button type="button" onClick={copyLocalInput}>
                            <Copy size={15} weight="bold" aria-hidden="true" />
                            复制我的内容
                          </button>
                        </div>
                        {editor.serverPreview ? (
                          <article className="start-server-preview">
                            <span>服务端预览 · R{editor.serverPreview.revision}</span>
                            <strong>{editor.serverPreview.title}</strong>
                            <p>{editableQuestions(editor.serverPreview).length} 道题；采用前请先复制需要保留的本地内容。</p>
                            <button type="button" onClick={adoptServerPreview}>
                              使用服务端版本
                            </button>
                          </article>
                        ) : null}
                      </section>
                    ) : null}

                    {editor.failure ? (
                      <section className="start-plan-state-panel" data-state="failed" role="alert">
                        <header>
                          <WarningCircle size={18} weight="fill" aria-hidden="true" />
                          <div><strong>计划操作失败</strong><span>本地输入没有丢失</span></div>
                        </header>
                        <p>{editor.failure.message}</p>
                        <button type="button" onClick={refreshServerRevision}>
                          重新载入服务端版本
                        </button>
                      </section>
                    ) : null}

                    {customOpen ? (
                      <form className="start-custom-question" onSubmit={addCustomQuestion}>
                        <header>
                          <div><span>自定义题</span><strong>添加明确的考察任务</strong></div>
                          <button type="button" onClick={() => setCustomOpen(false)} aria-label="关闭自定义题表单">
                            <X size={16} weight="bold" aria-hidden="true" />
                          </button>
                        </header>
                        <label htmlFor="custom-question-text">问题内容</label>
                        <textarea
                          id="custom-question-text"
                          rows={3}
                          value={customQuestion.question_text}
                          onChange={(event) =>
                            setCustomQuestion((value) => ({
                              ...value,
                              question_text: event.target.value,
                            }))
                          }
                          disabled={busy}
                        />
                        <span>{customQuestion.question_text.length.toLocaleString()} 字 · 不会伪造知识 grounding</span>
                        <label htmlFor="custom-question-focus">考察重点</label>
                        <input
                          id="custom-question-focus"
                          type="text"
                          value={customQuestion.focus}
                          onChange={(event) =>
                            setCustomQuestion((value) => ({
                              ...value,
                              focus: event.target.value,
                            }))
                          }
                          disabled={busy}
                        />
                        <button type="submit" disabled={busy}>
                          <Plus size={15} weight="bold" aria-hidden="true" />
                          添加并保存
                        </button>
                      </form>
                    ) : null}

                    {historyOpen ? (
                      <section className="start-plan-history" aria-label="计划历史版本">
                        <header><span>历史版本</span><strong>恢复会创建新的 revision</strong></header>
                        {editor.historyStatus === "loading" ? (
                          <p role="status"><SpinnerGap className="start-spinner" size={15} weight="bold" aria-hidden="true" />正在读取历史版本</p>
                        ) : null}
                        {editor.historyError ? <p role="alert">{editor.historyError}</p> : null}
                        {editor.history.length ? (
                          <ol>
                            {editor.history.map((revision) => (
                              <li key={revision.plan_revision_id}>
                                <div>
                                  <strong>R{revision.revision}</strong>
                                  <span>{revision.title}</span>
                                  <small>{revision.question_count} 题 · {revision.created_reason}</small>
                                </div>
                                <button
                                  type="button"
                                  onClick={(event) => confirmRestoreRevision(revision, event.currentTarget)}
                                  disabled={busy || revision.is_latest}
                                >
                                  {revision.is_latest ? "当前版本" : "恢复"}
                                </button>
                              </li>
                            ))}
                          </ol>
                        ) : null}
                      </section>
                    ) : null}

                    {hasLocalChanges(editor) ? (
                      <p className="start-plan-draft-note" role="status">
                        <PencilSimple size={15} weight="bold" aria-hidden="true" />
                        本地修改尚未全部保存；开始按钮会保持禁用。
                      </p>
                    ) : null}

                    {questions.length ? (
                      <ol className="start-plan-list">
                        {questions.map((question, index) => (
                          <PlanQuestion
                            key={question.question_id}
                            question={question}
                            index={index}
                            total={questions.length}
                            draft={questionDraft(editor, question)}
                            dirty={Boolean(editor.localDrafts[question.question_id])}
                            busy={busy}
                            onDraft={(questionId, field, value) =>
                              dispatchEditor({
                                type: "EDIT_LOCAL_QUESTION",
                                questionId,
                                field,
                                value,
                              })
                            }
                            onSave={saveQuestion}
                            onDiscard={(questionId) =>
                              dispatchEditor({
                                type: "DISCARD_LOCAL_QUESTION",
                                questionId,
                              })
                            }
                            onMove={moveQuestion}
                            onRegenerate={regenerateQuestion}
                            onDelete={confirmDeleteQuestion}
                          />
                        ))}
                      </ol>
                    ) : (
                      <InspectorEmpty icon={ClipboardText} title="计划没有返回可用题目。">
                        请重新生成计划；系统不会使用示例题填充空列表。
                      </InspectorEmpty>
                    )}
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
                      <div className="start-evidence-state" data-tone={knowledgeStatus === "degraded" ? "warning" : "info"} role="status">
                        <span aria-hidden="true">{knowledgeStatus === "degraded" ? <WarningCircle size={18} weight="fill" focusable="false" /> : <Info size={18} weight="bold" focusable="false" />}</span>
                        <div><strong>{knowledgeStatus === "degraded" ? "知识检索已降级" : knowledgeStatus === "keyword" ? "关键词准备完成" : "暂无公开知识证据"}</strong><p>{knowledgeStatus === "degraded" ? "面试仍可继续；系统不会展示不存在的引用。" : knowledgeStatus === "keyword" ? "本次计划仅使用关键词信号；没有可展示的证据引用。" : "计划仍可使用；证据列表保持为空，不会填充示例引用。"}</p></div>
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
                  <ReadinessItem ready={Boolean(draftId)} label="匿名草稿" value={draftDurabilityLabel} />
                  <ReadinessItem ready={Boolean(plan)} label="面试计划" value={plan ? "已生成" : "尚未生成"} />
                </div>
                <div className="start-privacy-note"><ShieldCheck size={17} weight="bold" aria-hidden="true" focusable="false" /><p><strong>仅与当前浏览器关联</strong><span>资料用于当前面试流程；匿名草稿不会跨设备同步。</span></p></div>
              </section>
            ) : null}
          </div>

          <footer className="start-inspector-actions">
            <button className={plan ? "button start-button start-inspector-secondary" : "button start-button button-primary"} type="button" onClick={plan ? confirmRegenerateAll : generatePlan} disabled={busy} aria-busy={status === "generating" || undefined} data-state={status === "generating" ? "loading" : undefined}>
              {status === "generating" ? <SpinnerGap className="start-spinner" size={18} weight="bold" aria-hidden="true" focusable="false" /> : <ListChecks size={18} weight="bold" aria-hidden="true" focusable="false" />}
              <span>{status === "generating" ? "正在生成面试计划" : configurationStale ? "应用配置并重新生成" : plan ? "重新生成计划" : "生成面试计划"}</span>
            </button>
            <button className={plan ? "button start-button button-primary" : "button start-button start-inspector-secondary"} type="button" disabled={!isLatestValidPlan(editor) || configurationStale || busy} onClick={startInterview} aria-busy={status === "starting" || undefined} data-state={status === "starting" ? "loading" : undefined} title={configurationStale ? "配置已变更，请先重新生成计划" : !isLatestValidPlan(editor) && plan ? "请先保存修改或处理冲突" : undefined}>
              {status === "starting" ? <SpinnerGap className="start-spinner" size={18} weight="bold" aria-hidden="true" focusable="false" /> : <ArrowRight size={18} weight="bold" aria-hidden="true" focusable="false" />}
              <span>{status === "starting" ? "正在创建面试" : "开始本次面试"}</span>
            </button>
          </footer>
        </aside>
      </main>

      <footer className="start-status-bar" aria-label="工作区状态">
        <StatusBarItem ready={Boolean(draftId)} label="草稿" value={draftDurabilityLabel} />
        <StatusBarItem ready={Boolean(jobDescription.trim())} label="JD" value={jobDescription.trim() ? "已填写" : "待填写"} />
        <StatusBarItem ready={Boolean(resumeText.trim())} label="经历" value={resumeText.trim() ? "已填写" : "待填写"} />
        <StatusBarItem ready={knowledgeStatus === "completed"} state={knowledgeStatus === "degraded" ? "warning" : knowledgeStatus === "empty" ? "info" : "idle"} label="知识" value={knowledgeStatusLabel} />
        <StatusBarItem ready={runtimeStatus === "ready"} state={runtimeStatus} label="请求" value={statusLabel} current />
      </footer>
      <ConfirmationDialog
        confirmation={confirmation}
        onCancel={closeConfirmation}
        idPrefix="plan-confirm"
      />
    </AppShell>
  );
}
