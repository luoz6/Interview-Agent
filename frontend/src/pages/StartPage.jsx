import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  BookOpenText,
  Briefcase,
  CheckCircle,
  Circle,
  Clock,
  CloudCheck,
  FileText,
  IdentificationCard,
  Info,
  ListChecks,
  SpinnerGap,
  UploadSimple,
  WarningCircle,
} from "@phosphor-icons/react";
import { deleteJson, getJson, patchJson, postJson } from "../api/client";
import { AppShell } from "../components/AppShell";
import { PlanEditor } from "../components/PlanEditor";
import { PrepActivityRail } from "../components/PrepActivityRail";
import { PrepEvidenceContent, PrepInspector } from "../components/PrepInspector";
import { PrepStatusBar } from "../components/PrepStatusBar";
import { StatusNotice } from "../components/StatusNotice";
import { useDelayedPendingOperation } from "../hooks/useDelayedPending";
import { createCommandId } from "../utils/ids";
import "../styles/pages/prep.css";

const DRAFT_KEYS = ["interview-agent:draft-id", "interviewDraftId"];
const MAX_FILE_BYTES = 1024 * 1024;
const MAX_TEXT_LENGTH = 50000;
const LAST_ACTIVE_SESSION_KEY = "interview-agent:last-active-session-id";
const LAST_REPORT_SESSION_KEY = "interview-agent:last-report-session-id";

function pendingStartKey(planId) {
  return `interview-agent:pending-start:${planId}`;
}

function readPendingStart(planId, expectedVersion) {
  if (!planId) return null;
  try {
    const value = JSON.parse(localStorage.getItem(pendingStartKey(planId)) || "null");
    if (value?.command_id && value.expected_plan_version === expectedVersion) return value;
  } catch {
    localStorage.removeItem(pendingStartKey(planId));
  }
  return null;
}

function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      window.clearTimeout(timeout);
      reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

function getStoredDraftId() {
  return DRAFT_KEYS.map((key) => window.localStorage.getItem(key)).find(Boolean) || "";
}

function storeDraftId(value) {
  DRAFT_KEYS.forEach((key) => window.localStorage.setItem(key, value));
}

function clearStoredDraftId() {
  DRAFT_KEYS.forEach((key) => window.localStorage.removeItem(key));
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

function DraftSaveState({ meta, saving }) {
  if (saving) {
    return <span className="start-prep-draft-state"><SpinnerGap className="start-spinner" size={15} weight="bold" aria-hidden="true" />正在保存草稿</span>;
  }
  if (!meta) {
    return <span className="start-prep-draft-state"><Circle size={15} weight="regular" aria-hidden="true" />尚未保存</span>;
  }
  const savedAt = new Date(meta.savedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  return (
    <span className="start-prep-draft-state" data-durability={meta.durability}>
      <CloudCheck size={15} weight="bold" aria-hidden="true" />
      {savedAt} · {meta.durability === "postgres" ? "持久保存" : "进程内临时保存"}
    </span>
  );
}

function SessionResumeCard({ recovery, onContinue, onDismiss }) {
  if (!recovery) return null;
  const isReport = recovery.kind === "report";
  return (
    <section className="start-resume-card" data-state={recovery.state} aria-label={isReport ? "上次面试报告" : "继续上次面试"}>
      <span className="start-resume-icon" aria-hidden="true">{isReport ? <FileText size={18} weight="duotone" /> : <Clock size={18} weight="duotone" />}</span>
      <div>
        <strong>{isReport ? "上次面试已结束" : "继续上次面试"}</strong>
        <p>{recovery.state === "unavailable" ? "暂时无法确认服务端快照；会话引用已保留。" : isReport ? "报告正在生成或已经可以查看。" : "已确认服务端会话仍可继续。"}</p>
      </div>
      <button className="button start-tool-button" type="button" onClick={onContinue}>{recovery.state === "unavailable" ? "重新确认" : isReport ? "查看报告" : "继续面试"}</button>
      <button className="start-resume-dismiss" type="button" onClick={onDismiss} aria-label="隐藏上次会话入口">×</button>
    </section>
  );
}

export function StartPage() {
  const [jobDescription, setJobDescription] = useState("");
  const [resumeText, setResumeText] = useState("");
  const [plan, setPlan] = useState(null);
  const [status, setStatus] = useState("idle");
  const [notice, setNotice] = useState(null);
  const [noticeAction, setNoticeAction] = useState(null);
  const [draftId, setDraftId] = useState(() => getStoredDraftId());
  const [fileNames, setFileNames] = useState({ jd: "未导入文件", resume: "未导入文件" });
  const [invalid, setInvalid] = useState({ jd: false, resume: false });
  const [activeDocument, setActiveDocument] = useState("jd");
  const [activePane, setActivePane] = useState("sources");
  const [activeInspectorTab, setActiveInspectorTab] = useState("readiness");
  const [wideWorkbench, setWideWorkbench] = useState(() => window.matchMedia("(min-width: 1180px)").matches);
  const [focusTarget, setFocusTarget] = useState("");
  const [clearArmed, setClearArmed] = useState(false);
  const [recovery, setRecovery] = useState(null);
  const [launchAttempt, setLaunchAttempt] = useState(0);
  const [draftMeta, setDraftMeta] = useState(null);
  const [planAnnouncement, setPlanAnnouncement] = useState("");
  const [activePlanQuestionId, setActivePlanQuestionId] = useState("");
  const launchControllerRef = useRef(null);
  const lastPersistedSourcesRef = useRef("");

  const questions = plan?.questions || [];
  const enabledQuestions = questions.filter((question) => question.enabled !== false);
  const jobTags = plan?.job_tags || [];
  const busy = ["generating", "saving", "restoring", "starting", "updating", "regenerating"].includes(status);
  const { showSpinner, operation: pendingOperation } = useDelayedPendingOperation(status, {
    pendingStates: ["saving", "restoring", "generating", "starting", "updating", "regenerating"],
    delay: 150,
    minimumVisible: 300,
  });
  const sourcesReady = Number(Boolean(jobDescription.trim())) + Number(Boolean(resumeText.trim()));
  const estimatedMinutes = useMemo(
    () => enabledQuestions.length ? `${enabledQuestions.length * 4}–${enabledQuestions.length * 6} 分钟` : "待生成",
    [enabledQuestions.length],
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
    const media = window.matchMedia("(min-width: 1180px)");
    const sync = () => {
      setWideWorkbench(media.matches);
      if (!media.matches) {
        setActiveDocument((current) => current === "split" ? "jd" : current);
      }
    };
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

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

  useEffect(() => () => launchControllerRef.current?.abort(), []);

  useEffect(() => {
    verifyRecoveryReference();
  }, []);

  useEffect(() => {
    const planId = new URLSearchParams(window.location.search).get("plan_id")?.trim();
    if (!planId) return undefined;
    const controller = new AbortController();
    setStatus("restoring");
    setNotice({ tone: "info", text: "正在打开针对性练习计划。" });
    getJson(`/api/prep-plans/${encodeURIComponent(planId)}`, {
      cache: "no-store",
      signal: controller.signal,
    }).then((restoredPlan) => {
      setPlan(restoredPlan);
      setActivePane("plan");
      setActiveInspectorTab("plan");
      setStatus("ready");
      setNotice({
        tone: "success",
        text: restoredPlan.practice_provenance
          ? "针对性练习计划已打开。三道题可以继续调整，确认后会原样用于下一轮模拟。"
          : "面试计划已打开，可以继续检查和调整。",
      });
    }).catch((error) => {
      if (error.name === "AbortError") return;
      setStatus("error");
      setNotice({
        tone: "error",
        text: [404, 410].includes(error.status)
          ? "练习计划已失效或不存在，请返回报告重新创建。"
          : `练习计划暂时无法打开：${error.message}`,
      });
    });
    return () => controller.abort();
  }, []);

  async function verifyRecoveryReference() {
    const activeSessionId = localStorage.getItem(LAST_ACTIVE_SESSION_KEY);
    if (!activeSessionId) {
      const reportSessionId = localStorage.getItem(LAST_REPORT_SESSION_KEY);
      setRecovery(reportSessionId ? { kind: "report", sessionId: reportSessionId, state: "ready" } : null);
      return;
    }
    setRecovery({ kind: "interview", sessionId: activeSessionId, state: "checking" });
    try {
      const snapshot = await getJson(`/api/interviews/${encodeURIComponent(activeSessionId)}`, { cache: "no-store" });
      if (snapshot.status === "finished") {
        localStorage.removeItem(LAST_ACTIVE_SESSION_KEY);
        localStorage.setItem(LAST_REPORT_SESSION_KEY, activeSessionId);
        setRecovery({ kind: "report", sessionId: activeSessionId, state: "ready" });
      } else {
        setRecovery({ kind: "interview", sessionId: activeSessionId, state: "ready" });
      }
    } catch (error) {
      if ([404, 410].includes(error.status)) {
        localStorage.removeItem(LAST_ACTIVE_SESSION_KEY);
        setRecovery(null);
      } else {
        setRecovery({ kind: "interview", sessionId: activeSessionId, state: "unavailable" });
      }
    }
  }

  function continueRecovery() {
    if (!recovery) return;
    if (recovery.state === "unavailable") {
      verifyRecoveryReference();
      return;
    }
    const path = recovery.kind === "report" ? "/report-processing" : "/interview";
    window.location.assign(`${path}?session_id=${encodeURIComponent(recovery.sessionId)}`);
  }

  function validateSources() {
    setNoticeAction(null);
    const next = {
      jd: !jobDescription.trim(),
      resume: !resumeText.trim(),
    };
    setInvalid(next);
    if (next.jd || next.resume) {
      const missingDocument = next.jd ? "jd" : "resume";
      setActiveDocument(missingDocument);
      setFocusTarget(missingDocument);
      setActivePane("sources");
      setActiveInspectorTab("readiness");
      setNotice({ tone: "error", text: `${next.jd ? "岗位 JD" : "候选人经历"}尚未填写。需要同时提供两份资料，才能建立可信的考察边界。` });
      return false;
    }
    return true;
  }

  async function importFile(file, target, input) {
    if (!file) return;
    setNoticeAction(null);
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (!extension || !["txt", "md"].includes(extension)) {
      setNotice({ tone: "error", text: "仅支持 .txt 或 .md 文件；PDF、Word 和图片不会被静默解析。请复制其中的文本后粘贴到编辑区。" });
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
    setActivePane("sources");
    setActiveInspectorTab("readiness");
    setNotice({ tone: truncated ? "info" : "success", text: truncated ? `${file.name} 已导入，并按上限保留前 50,000 字。` : `${file.name} 已导入。生成计划前仍可继续编辑。` });
    input.value = "";
  }

  async function generatePlan() {
    if (!validateSources()) return;
    setNoticeAction(null);
    setStatus("generating");
    setNotice({ tone: "info", text: "正在提取岗位约束、候选人经历与可用知识证据。" });
    try {
      const nextPlan = await postJson("/api/prep", {
        job_description: jobDescription,
        resume_text: resumeText,
        draft_id: draftId || null,
      });
      setPlan(nextPlan);
      setStatus("ready");
      setActivePane("plan");
      setActiveInspectorTab("plan");
      setNotice({ tone: "success", text: "面试蓝图已生成。请先检查题目与证据路径，再开始面试。" });
    } catch (error) {
      setStatus("error");
      setNotice({ tone: "error", text: error.message });
      setNoticeAction(error.retryable ? { operation: "generate", label: "重试生成" } : null);
    }
  }

  const saveDraft = useCallback(async ({ automatic = false } = {}) => {
    if (!jobDescription.trim() || !resumeText.trim()) {
      if (!automatic) {
        const missingDocument = !jobDescription.trim() ? "jd" : "resume";
        setInvalid({
          jd: !jobDescription.trim(),
          resume: !resumeText.trim(),
        });
        setActiveDocument(missingDocument);
        setFocusTarget(missingDocument);
        setActivePane("sources");
        setActiveInspectorTab("readiness");
        setNotice({
          tone: "error",
          text: `${missingDocument === "jd" ? "岗位 JD" : "候选人经历"}尚未填写。补齐两份资料后才能保存完整草稿。`,
        });
      }
      return;
    }
    const sourceFingerprint = JSON.stringify([jobDescription, resumeText]);
    if (!automatic) setNoticeAction(null);
    setStatus("saving");
    if (!automatic) {
      setNotice({ tone: "info", text: "正在保存当前资料。浏览器只保留恢复标识。" });
    }
    try {
      const draft = await postJson("/api/interview-drafts", {
          draft_id: draftId || null,
          job_description: jobDescription,
          resume_text: resumeText,
          title: plan?.title || null,
          job_tags: plan?.job_tags?.length ? plan.job_tags : null,
      });
      setDraftId(draft.draft_id);
      storeDraftId(draft.draft_id);
      lastPersistedSourcesRef.current = sourceFingerprint;
      setDraftMeta({
        savedAt: draft.updated_at || new Date().toISOString(),
        durability: draft.durability,
        expiresAt: draft.expires_at,
      });
      setStatus(plan ? "ready" : "idle");
      if (!automatic) {
        setNotice({
          tone: "success",
          text: draft.durability === "postgres"
            ? `草稿已持久保存至 ${new Date(draft.expires_at).toLocaleString("zh-CN")}，当前浏览器只保存恢复标识。`
            : "草稿已保存在当前服务进程；服务重启后会失效，浏览器只保存恢复标识。",
        });
      }
    } catch (error) {
      setStatus("error");
      setNotice({
        tone: "error",
        text: automatic
          ? `自动保存未完成，当前文字和恢复标识已保留：${error.message}`
          : error.message,
      });
      setNoticeAction(!automatic && error.retryable ? { operation: "save", label: "重试保存" } : null);
    }
  }, [draftId, jobDescription, plan, resumeText]);

  useEffect(() => {
    const sourceFingerprint = JSON.stringify([jobDescription, resumeText]);
    if (
      !jobDescription.trim()
      || !resumeText.trim()
      || sourceFingerprint === lastPersistedSourcesRef.current
      || busy
    ) {
      return undefined;
    }
    const timeout = window.setTimeout(() => {
      saveDraft({ automatic: true });
    }, 900);
    return () => window.clearTimeout(timeout);
  }, [busy, jobDescription, resumeText, saveDraft]);

  async function restoreDraft() {
    setNoticeAction(null);
    const storedId = draftId || getStoredDraftId();
    if (!storedId) {
      setNotice({ tone: "info", text: "当前浏览器没有可恢复的匿名草稿。" });
      return;
    }
    setStatus("restoring");
    setNotice({ tone: "info", text: "正在恢复当前浏览器保存的资料。" });
    try {
      const draft = await getJson(`/api/interview-drafts/${encodeURIComponent(storedId)}`);
      setDraftId(draft.draft_id);
      setJobDescription(draft.job_description || "");
      setResumeText(draft.resume_text || "");
      lastPersistedSourcesRef.current = JSON.stringify([
        draft.job_description || "",
        draft.resume_text || "",
      ]);
      setDraftMeta({
        savedAt: draft.updated_at || new Date().toISOString(),
        durability: draft.durability,
        expiresAt: draft.expires_at,
      });
      setPlan(null);
      setInvalid({ jd: false, resume: false });
      setFileNames({ jd: "来自匿名草稿", resume: "来自匿名草稿" });
      setStatus("idle");
      setActiveDocument("jd");
      setActivePane("sources");
      setActiveInspectorTab("readiness");
      setNotice({ tone: "success", text: "草稿已恢复。为保证计划与内容一致，请重新生成面试蓝图。" });
    } catch (error) {
      if ([404, 410].includes(error.status)) {
        clearStoredDraftId();
        setDraftId("");
        setDraftMeta(null);
        lastPersistedSourcesRef.current = "";
      }
      setStatus("error");
      setNotice({
        tone: "error",
        text: [404, 410].includes(error.status)
          ? "草稿已失效或被删除，恢复标识已清理。"
          : `草稿暂时无法恢复，恢复标识已保留：${error.message}`,
      });
      setNoticeAction(![404, 410].includes(error.status) && error.retryable ? { operation: "restore", label: "重试恢复" } : null);
    }
  }

  async function deleteSavedDraft() {
    setNoticeAction(null);
    const storedId = draftId || getStoredDraftId();
    if (!storedId) {
      setNotice({ tone: "info", text: "当前没有已保存草稿可删除。" });
      return;
    }
    setStatus("saving");
    try {
      await deleteJson(`/api/interview-drafts/${encodeURIComponent(storedId)}`);
      clearStoredDraftId();
      setDraftId("");
      setDraftMeta(null);
      lastPersistedSourcesRef.current = "";
      setStatus(plan ? "ready" : "idle");
      setNotice({ tone: "success", text: "已保存草稿已从服务端删除；当前画布内容仍保留。" });
    } catch (error) {
      if ([404, 410].includes(error.status)) {
        clearStoredDraftId();
        setDraftId("");
        setDraftMeta(null);
        lastPersistedSourcesRef.current = "";
        setStatus(plan ? "ready" : "idle");
        setNotice({ tone: "info", text: "草稿已经不存在，本地恢复标识已清理。" });
      } else {
        setStatus("error");
        setNotice({ tone: "error", text: `草稿删除未完成，恢复标识仍保留：${error.message}` });
      }
    }
  }

  function clearWorkspace() {
    setNoticeAction(null);
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
    setActivePane("sources");
    setActiveInspectorTab("readiness");
    setClearArmed(false);
    setNotice({ tone: "info", text: "当前画布已清空；此前保存的匿名草稿仍可恢复。" });
  }

  async function updatePlan(operations, successMessage, questionId) {
    if (!plan || busy) return;
    setNoticeAction(null);
    setStatus("updating");
    setActivePlanQuestionId(questionId || "");
    try {
      const updated = await patchJson(
        `/api/prep-plans/${encodeURIComponent(plan.plan_id)}`,
        {
          expected_version: plan.plan_version,
          operations,
        },
      );
      setPlan(updated);
      setStatus("ready");
      setPlanAnnouncement(successMessage);
      setNotice(null);
    } catch (error) {
      if (error.code === "PREP_PLAN_VERSION_CONFLICT") {
        try {
          const latest = await getJson(`/api/prep-plans/${encodeURIComponent(plan.plan_id)}`);
          setPlan(latest);
          setStatus("ready");
          setNotice({ tone: "warning", text: "计划已在其他操作中更新，已载入服务端最新版本。请确认后重试。" });
          setPlanAnnouncement("计划版本发生冲突，已载入服务端最新版本。");
        } catch (refreshError) {
          setStatus("error");
          setNotice({ tone: "error", text: refreshError.message });
        }
      } else {
        if ([404, 410].includes(error.status)) {
          setPlan(null);
          setActivePane("sources");
          setActiveInspectorTab("readiness");
        }
        setStatus("error");
        setNotice({ tone: "error", text: error.message });
      }
    } finally {
      setActivePlanQuestionId("");
    }
  }

  async function regeneratePlanQuestion(questionId, position) {
    if (!plan || busy) return;
    setNoticeAction(null);
    setStatus("regenerating");
    setActivePlanQuestionId(questionId);
    setNotice({ tone: "info", text: `正在为第 ${position} 题生成不重复的替代题，原题会保留到成功写入。` });
    try {
      const updated = await postJson(
        `/api/prep-plans/${encodeURIComponent(plan.plan_id)}/questions/${encodeURIComponent(questionId)}/regenerate`,
        { expected_version: plan.plan_version },
      );
      setPlan(updated);
      setStatus("ready");
      setNotice({ tone: "success", text: `第 ${position} 题已替换；顺序、启用状态和必考标记保持不变。` });
      setPlanAnnouncement(`第 ${position} 题已重新生成。`);
    } catch (error) {
      if (error.code === "PREP_PLAN_VERSION_CONFLICT") {
        try {
          const latest = await getJson(`/api/prep-plans/${encodeURIComponent(plan.plan_id)}`);
          setPlan(latest);
          setStatus("ready");
          setNotice({ tone: "warning", text: "生成期间计划版本已变化，原题未被覆盖；已载入服务端最新版本。" });
          setPlanAnnouncement("单题重生成遇到版本冲突，已载入最新计划。");
        } catch (refreshError) {
          setStatus("error");
          setNotice({ tone: "error", text: refreshError.message });
        }
      } else {
        if ([404, 410].includes(error.status)) {
          setPlan(null);
          setActivePane("sources");
          setActiveInspectorTab("readiness");
        }
        setStatus("error");
        setNotice({ tone: "error", text: `${error.message} 原题没有变化。` });
      }
    } finally {
      setActivePlanQuestionId("");
    }
  }

  async function startInterview() {
    if (!plan || (!plan.practice_provenance && !validateSources())) return;
    setNoticeAction(null);
    if (!plan.plan_id || !Number.isInteger(plan.plan_version)) {
      setStatus("error");
      setNotice({ tone: "error", text: "当前计划缺少权威版本，请重新生成后再开始。" });
      return;
    }
    let pending = readPendingStart(plan.plan_id, plan.plan_version);
    if (!pending) {
      try {
        pending = {
          command_id: createCommandId("start"),
          expected_plan_version: plan.plan_version,
          created_at: new Date().toISOString(),
        };
      } catch (error) {
        setStatus("error");
        setNotice({ tone: "error", text: error.message });
        return;
      }
      localStorage.setItem(pendingStartKey(plan.plan_id), JSON.stringify(pending));
    }
    setStatus("starting");
    setLaunchAttempt(0);
    setNotice({ tone: "info", text: "正在创建可恢复的面试会话；连接中断时会复用同一启动标识。" });
    launchControllerRef.current?.abort();
    const controller = new AbortController();
    launchControllerRef.current = controller;
    try {
      let session;
      const delays = [1500, 3000, 5000];
      for (let attempt = 0; attempt <= delays.length; attempt += 1) {
        setLaunchAttempt(attempt + 1);
        try {
          session = await postJson("/api/interviews", {
            plan_id: plan.plan_id,
            expected_plan_version: pending.expected_plan_version,
            command_id: pending.command_id,
          }, { signal: controller.signal });
          break;
        } catch (error) {
          const errorDetails = error.body?.details || error.body?.detail?.details || {};
          if (error.code === "PREP_PLAN_ALREADY_CONSUMED" && errorDetails.session_id) {
            session = { session_id: errorDetails.session_id };
            break;
          }
          const bootstrapPending = error.code === "INTERVIEW_BOOTSTRAP_PENDING";
          if (!bootstrapPending || attempt >= delays.length) throw error;
          const serverDelay = Number(errorDetails.retry_after_seconds) * 1000;
          const delay = Number.isFinite(serverDelay) && serverDelay > 0 ? serverDelay : delays[attempt];
          setNotice({ tone: "info", text: `会话已创建，初始化仍在恢复；${Math.round(delay / 1000)} 秒后使用同一标识继续。` });
          await wait(delay, controller.signal);
        }
      }
      if (!session?.session_id) throw new Error("服务未返回可恢复的会话标识。");
      localStorage.setItem(LAST_ACTIVE_SESSION_KEY, session.session_id);
      localStorage.removeItem(pendingStartKey(plan.plan_id));
      window.location.assign(`/interview?session_id=${encodeURIComponent(session.session_id)}`);
    } catch (error) {
      setStatus("error");
      const terminal = [404, 410].includes(error.status);
      if (terminal) localStorage.removeItem(pendingStartKey(plan.plan_id));
      setNotice({
        tone: "error",
        text: error.code === "INTERVIEW_BOOTSTRAP_PENDING"
          ? "自动恢复次数已用完。原启动标识仍保留；点击“继续准备面试”可安全重试。"
          : terminal
            ? "当前计划已失效，请重新生成后再开始。"
            : `${error.message} 原启动标识已保留，可安全重试。`,
      });
    } finally {
      if (launchControllerRef.current === controller) launchControllerRef.current = null;
    }
  }

  const statusLabels = {
    idle: sourcesReady === 2 ? "可以生成计划" : "等待资料",
    generating: "正在建模",
    ready: "蓝图就绪",
    saving: "保存草稿",
    restoring: "恢复草稿",
    updating: "保存计划",
    regenerating: "替换题目",
    starting: launchAttempt > 1 ? `恢复会话 · ${launchAttempt}` : "创建会话",
    error: "需要处理",
  };
  const statusLabel = statusLabels[status];
  const visualStatus = showSpinner && pendingOperation ? pendingOperation : status;
  const visualStatusLabel = statusLabels[visualStatus] || statusLabel;

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
        setActivePane("sources");
        setActiveInspectorTab("readiness");
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
        setActivePane("sources");
        setActiveInspectorTab("readiness");
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

  function selectPane(nextPane) {
    setActivePane(nextPane);
    setActiveInspectorTab(nextPane === "sources" ? "readiness" : nextPane);
  }

  function retryNoticeAction() {
    const operation = noticeAction?.operation;
    setNoticeAction(null);
    if (operation === "generate") generatePlan();
    if (operation === "save") saveDraft();
    if (operation === "restore") restoreDraft();
  }

  const knowledgeStatus = plan?.prep_context?.knowledge_status;

  return (
    <AppShell status={<RuntimeStatus status={visualStatus} label={visualStatusLabel} showSpinner={showSpinner} />}>
      <main id="main-content" className="start-app-shell start-prep-app-shell" tabIndex="-1">
        <PrepActivityRail activePane={activePane} onSelect={selectPane} />

        <section className="start-editor-workspace" aria-label="准备编辑器">
          <div className="start-prep-notice-slot">
            <SessionResumeCard recovery={recovery} onContinue={continueRecovery} onDismiss={() => setRecovery(null)} />
            <div className="start-prep-notice-row">
              <StatusNotice key={notice ? `${notice.tone}-${notice.text}` : "empty"} notice={notice} />
              {noticeAction && notice?.tone === "error" && (
                <button className="button start-tool-button start-prep-notice-action" type="button" onClick={retryNoticeAction} disabled={busy}>
                  <ArrowRight size={16} weight="bold" aria-hidden="true" />{noticeAction.label}
                </button>
              )}
            </div>
          </div>

          {activePane === "sources" && (
            <section id="prep-sources-pane" className="start-prep-pane start-prep-sources-pane" aria-labelledby="prep-source-title">
              <header className="start-workspace-head">
                <div className="start-workspace-title">
                  <span className="start-workspace-mark" aria-hidden="true"><FileText size={18} weight="duotone" /></span>
                  <div><h1 id="prep-source-title">准备面试资料</h1><p>编辑岗位约束与候选人事实，建立本轮面试的可信边界。</p></div>
                </div>
                <div className="start-readiness" data-ready={sourcesReady === 2} aria-label={`资料完成度 ${sourcesReady}/2`}><strong>资料</strong><span>{sourcesReady}/2</span></div>
              </header>

              <div className="start-editor-commandbar">
                <div className="start-document-tabs" role="tablist" aria-label="选择资料">
                  <button type="button" role="tab" aria-selected={activeDocument === "jd"} onClick={() => setActiveDocument("jd")}>
                    <Briefcase size={16} weight="bold" aria-hidden="true" />岗位 JD<span className="start-tab-state" data-ready={Boolean(jobDescription.trim())}>{jobDescription.trim() ? "已填写" : "待填写"}</span>
                  </button>
                  <button type="button" role="tab" aria-selected={activeDocument === "resume"} onClick={() => setActiveDocument("resume")}>
                    <IdentificationCard size={16} weight="bold" aria-hidden="true" />候选人经历<span className="start-tab-state" data-ready={Boolean(resumeText.trim())}>{resumeText.trim() ? "已填写" : "待填写"}</span>
                  </button>
                  {wideWorkbench && <button className="start-split-tab" type="button" role="tab" aria-selected={activeDocument === "split"} onClick={() => setActiveDocument("split")}>并排查看</button>}
                </div>
                <DraftSaveState meta={draftMeta} saving={showSpinner && pendingOperation === "saving"} />
              </div>

              <div id="document-workspace" className={`start-document-canvas ${activeDocument === "split" ? "start-document-split" : ""}`} aria-label="源文档编辑区">
                <div data-document="jd" data-active={activeDocument === "jd" || activeDocument === "split"}>{renderDocument("jd")}</div>
                <div data-document="resume" data-active={activeDocument === "resume" || activeDocument === "split"}>{renderDocument("resume")}</div>
              </div>

              <footer className="start-prep-launch-bar">
                <div className="start-prep-launch-copy"><strong>{plan ? "资料已发生变化时请重新生成" : "资料就绪后生成面试蓝图"}</strong><span>系统会同时检查题目范围与可用证据。</span></div>
                <button className="button start-button button-primary start-prep-primary-action" type="button" onClick={generatePlan} disabled={busy} aria-busy={status === "generating" || undefined} data-state={status === "generating" ? "loading" : undefined}>
                  {showSpinner && pendingOperation === "generating" ? <SpinnerGap className="start-spinner" size={18} weight="bold" aria-hidden="true" /> : <ListChecks size={18} weight="bold" aria-hidden="true" />}
                  {status === "generating" ? "正在生成计划" : plan ? "根据当前资料重新生成" : "生成并检查面试计划"}
                </button>
              </footer>
            </section>
          )}

          {activePane === "plan" && (
            <section id="prep-plan-pane" className="start-prep-pane start-prep-plan-pane" aria-labelledby="prep-plan-title">
              <header className="start-workspace-head">
                <div className="start-workspace-title">
                  <span className="start-workspace-mark" aria-hidden="true"><ListChecks size={18} weight="duotone" /></span>
                  <div><h1 id="prep-plan-title">检查面试蓝图</h1><p>核对题目顺序、考察重点和证据来源；开始后使用当前权威版本。</p></div>
                </div>
                <div className="start-readiness" data-ready={Boolean(plan)}><strong>蓝图</strong><span>{plan ? `v${plan.plan_version}` : "—"}</span></div>
              </header>
              <div className="start-prep-scroll-pane">
                {plan ? <>
                  {jobTags.length ? <div className="start-job-tags start-prep-job-tags" aria-label="岗位标签">{jobTags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}
                  <PlanEditor plan={plan} busy={busy} activeQuestionId={activePlanQuestionId} onPatch={updatePlan} onRegenerate={regeneratePlanQuestion} />
                </> : <div className="start-prep-empty"><ListChecks size={28} weight="duotone" aria-hidden="true" /><h2>尚未生成面试计划</h2><p>先在“资料”中填写岗位 JD 与候选人经历，再生成可检查的面试蓝图。</p><button className="button start-tool-button" type="button" onClick={() => selectPane("sources")}>返回填写资料</button></div>}
              </div>
              <footer className="start-prep-launch-bar">
                <div className="start-prep-launch-copy" role="status"><strong>{plan ? "当前计划可以开始" : "等待面试蓝图"}</strong><span>{plan ? `版本 ${plan.plan_version} · ${enabledQuestions.length} 道题 · 约 ${estimatedMinutes}` : "生成后可在此确认版本并开始面试。"}</span></div>
                <button className="button start-button button-primary start-prep-primary-action" type="button" disabled={busy} onClick={plan ? startInterview : () => selectPane("sources")} aria-busy={status === "starting" || undefined} data-state={status === "starting" ? "loading" : undefined}>
                  {showSpinner && pendingOperation === "starting" ? <SpinnerGap className="start-spinner" size={18} weight="bold" aria-hidden="true" /> : plan ? <ArrowRight size={18} weight="bold" aria-hidden="true" /> : <FileText size={18} weight="bold" aria-hidden="true" />}
                  {status === "starting" ? "正在创建面试" : plan ? readPendingStart(plan.plan_id, plan.plan_version) ? "继续准备面试" : "确认版本并开始面试" : "填写面试资料"}
                </button>
              </footer>
            </section>
          )}

          {activePane === "evidence" && (
            <section id="prep-evidence-pane" className="start-prep-pane start-prep-evidence-pane" aria-labelledby="prep-evidence-title">
              <header className="start-workspace-head"><div className="start-workspace-title"><span className="start-workspace-mark" aria-hidden="true"><BookOpenText size={18} weight="duotone" /></span><div><h1 id="prep-evidence-title">核对计划依据</h1><p>区分岗位资料、候选人事实与公开知识证据，不推断服务端未公开的信息。</p></div></div></header>
              <div className="start-prep-scroll-pane start-prep-evidence-canvas"><PrepEvidenceContent plan={plan} /></div>
              <footer className="start-prep-launch-bar">
                <div className="start-prep-launch-copy"><strong>{plan ? "证据状态已与当前蓝图同步" : "等待面试蓝图"}</strong><span>{plan ? "可返回蓝图继续检查题目。" : "生成蓝图后显示公开证据状态。"}</span></div>
                <button className="button start-button button-primary start-prep-primary-action" type="button" disabled={busy} onClick={plan ? startInterview : () => selectPane("sources")}>
                  {plan ? <ArrowRight size={18} weight="bold" aria-hidden="true" /> : <FileText size={18} weight="bold" aria-hidden="true" />}{plan ? "确认版本并开始面试" : "填写面试资料"}
                </button>
              </footer>
            </section>
          )}

          <p className="sr-only" aria-live="polite" aria-atomic="true">{planAnnouncement}</p>
        </section>

        <PrepInspector
          activeTab={activeInspectorTab}
          onTabChange={setActiveInspectorTab}
          status={visualStatus}
          statusLabel={visualStatusLabel}
          showSpinner={showSpinner}
          jobDescription={jobDescription}
          resumeText={resumeText}
          plan={plan}
          enabledQuestions={enabledQuestions}
          estimatedMinutes={estimatedMinutes}
          draftMeta={draftMeta}
          busy={busy}
          draftId={draftId}
          clearArmed={clearArmed}
          onSave={() => saveDraft()}
          onRestore={restoreDraft}
          onDelete={deleteSavedDraft}
          onClear={clearWorkspace}
        />
      </main>
      <PrepStatusBar status={visualStatus} statusLabel={visualStatusLabel} showSpinner={showSpinner} jobDescription={jobDescription} resumeText={resumeText} draftMeta={draftMeta} knowledgeStatus={knowledgeStatus} />
    </AppShell>
  );
}
