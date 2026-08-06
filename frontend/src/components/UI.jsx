import { AsyncState } from "./AsyncState";

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
  return <AsyncState eyebrow={eyebrow} title={title} description={description} action={action} />;
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
