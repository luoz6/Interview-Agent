export function Badge({ children, tone = "neutral", className = "" }) {
  return <span className={`badge badge-${tone} ${className}`.trim()}>{children}</span>;
}

export function Notice({ children, tone = "info", role, ...props }) {
  if (!children) return null;
  return <div className={`notice notice-${tone}`} role={role || (tone === "danger" ? "alert" : "status")} {...props}>{children}</div>;
}

export function AssistanceNotice({ announce = true }) {
  return (
    <div
      className="assistance-notice"
      role="status"
      aria-live={announce ? "polite" : "off"}
      data-assistance-notice="basic"
    >
      <strong>智能辅助暂时使用基础模式</strong>
      <span>你已提交的回答仍已保存，可以继续完成面试。</span>
    </div>
  );
}

export function EmptyState({ eyebrow, title, description, action }) {
  return (
    <div className="empty-state">
      {eyebrow && <span className="mono-label">{eyebrow}</span>}
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function Button({ children, variant = "secondary", busy = false, disabled = false, className = "", ...props }) {
  return (
    <button
      className={`button button-${variant} ${className}`.trim()}
      aria-busy={busy || undefined}
      disabled={disabled || busy}
      {...props}
    >
      {busy && <span className="button-loader" aria-hidden="true" />}
      <span>{children}</span>
    </button>
  );
}

export function Metric({ label, value, tone = "neutral", detail }) {
  return (
    <article className={`metric metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </article>
  );
}

export function SectionHeading({ kicker, title, meta }) {
  return (
    <div className="section-heading">
      <div>{kicker && <p className="page-kicker">{kicker}</p>}<h2>{title}</h2></div>
      {meta && <span>{meta}</span>}
    </div>
  );
}

export function Skeleton({ lines = 4 }) {
  return (
    <div className="skeleton" role="status" aria-live="polite" aria-label="正在加载">
      {Array.from({ length: lines }, (_, index) => <span key={index} aria-hidden="true" />)}
    </div>
  );
}
