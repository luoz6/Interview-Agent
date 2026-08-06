import { CheckCircle, Info, WarningCircle, X } from "@phosphor-icons/react";

const ICONS = {
  info: Info,
  success: CheckCircle,
  warning: WarningCircle,
  error: WarningCircle,
};

export function StatusNotice({
  notice,
  tone,
  title,
  children,
  className = "",
  copyClassName = "",
  closeClassName = "",
  closeLabel = "关闭提示",
  onDismiss,
}) {
  const content = children ?? notice?.text;
  if (!content) return null;
  const rawTone = tone ?? notice?.tone ?? "info";
  const normalizedTone = rawTone === "danger" ? "error" : rawTone;
  const resolvedTitle = title ?? notice?.title;
  const NoticeIcon = ICONS[normalizedTone] || Info;

  return (
    <div
      className={`start-notice start-notice-${normalizedTone} ${className}`.trim()}
      role={normalizedTone === "error" ? "alert" : "status"}
      aria-live={normalizedTone === "error" ? "assertive" : "polite"}
      aria-atomic="true"
    >
      <span className="start-notice-icon" aria-hidden="true">
        <NoticeIcon size={18} weight={normalizedTone === "info" ? "bold" : "fill"} />
      </span>
      {resolvedTitle || copyClassName ? (
        <div className={copyClassName || undefined}>
          {resolvedTitle && <strong>{resolvedTitle}</strong>}
          <p>{content}</p>
        </div>
      ) : <p>{content}</p>}
      {onDismiss && (
        <button className={closeClassName || undefined} type="button" onClick={onDismiss} aria-label={closeLabel}>
          <X size={15} weight="bold" aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
