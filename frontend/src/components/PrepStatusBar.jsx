import {
  Briefcase,
  CheckCircle,
  Circle,
  CloudCheck,
  IdentificationCard,
  MagnifyingGlass,
  SpinnerGap,
  WarningCircle,
} from "@phosphor-icons/react";

const PENDING_STATES = new Set(["generating", "saving", "restoring", "starting", "updating", "regenerating"]);

const KNOWLEDGE_LABELS = {
  keyword: "关键词准备",
  completed: "证据可用",
  empty: "无公开证据",
  degraded: "检索已降级",
};

export function PrepStatusBar({
  status,
  statusLabel,
  showSpinner,
  jobDescription,
  resumeText,
  draftMeta,
  knowledgeStatus,
}) {
  const pending = PENDING_STATES.has(status);
  const RequestIcon = showSpinner && pending ? SpinnerGap : status === "error" ? WarningCircle : status === "ready" ? CheckCircle : Circle;
  const knowledgeLabel = KNOWLEDGE_LABELS[knowledgeStatus] || "等待计划";
  const knowledgeTone = knowledgeStatus === "degraded" ? "warning" : knowledgeStatus === "completed" ? "ready" : knowledgeStatus === "empty" ? "info" : undefined;
  const savedLabel = draftMeta
    ? `${new Date(draftMeta.savedAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })} ${draftMeta.durability === "postgres" ? "持久保存" : "临时保存"}`
    : "尚未保存";

  return (
    <footer className="start-status-bar start-prep-status-bar" aria-label="准备状态">
      <span className="start-status-current" data-state={status}>
        <RequestIcon className={showSpinner && pending ? "start-spinner" : undefined} size={14} weight="bold" aria-hidden="true" />
        当前请求 <strong>{statusLabel}</strong>
      </span>
      <span data-ready={Boolean(jobDescription.trim())}>
        <Briefcase size={14} weight="bold" aria-hidden="true" />
        岗位 JD <strong>{jobDescription.trim() ? "已填写" : "待填写"}</strong>
      </span>
      <span data-ready={Boolean(resumeText.trim())}>
        <IdentificationCard size={14} weight="bold" aria-hidden="true" />
        候选人经历 <strong>{resumeText.trim() ? "已填写" : "待填写"}</strong>
      </span>
      <span data-ready={Boolean(draftMeta)}>
        <CloudCheck size={14} weight="bold" aria-hidden="true" />
        草稿 <strong>{savedLabel}</strong>
      </span>
      <span data-state={knowledgeTone}>
        <MagnifyingGlass size={14} weight="bold" aria-hidden="true" />
        Knowledge <strong>{knowledgeLabel}</strong>
      </span>
    </footer>
  );
}
