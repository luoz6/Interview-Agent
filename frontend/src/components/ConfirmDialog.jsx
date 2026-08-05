import { useEffect, useId, useRef } from "react";
import { WarningCircle, X } from "@phosphor-icons/react";

const FOCUSABLE = [
  "button:not([disabled])",
  "a[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function ConfirmDialog({
  open,
  title,
  description,
  children,
  confirmLabel = "确认",
  cancelLabel = "取消",
  tone = "danger",
  busy = false,
  onConfirm,
  onCancel,
}) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef(null);
  const confirmRef = useRef(null);
  const returnFocusRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    returnFocusRef.current = document.activeElement;
    const frame = window.requestAnimationFrame(() => confirmRef.current?.focus());
    const onKeyDown = (event) => {
      if (event.key === "Escape" && !busy) {
        event.preventDefault();
        onCancel?.();
        return;
      }
      if (event.key !== "Tab") return;
      const nodes = [...(dialogRef.current?.querySelectorAll(FOCUSABLE) || [])];
      if (!nodes.length) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
      returnFocusRef.current?.focus?.();
    };
  }, [busy, onCancel, open]);

  if (!open) return null;
  return (
    <div className="confirm-dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) onCancel?.();
    }}>
      <section
        ref={dialogRef}
        className="confirm-dialog"
        data-tone={tone}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
      >
        <header className="confirm-dialog-head">
          <span className="confirm-dialog-icon" aria-hidden="true"><WarningCircle size={22} weight="fill" /></span>
          <div>
            <h2 id={titleId}>{title}</h2>
            {description && <p id={descriptionId}>{description}</p>}
          </div>
          <button type="button" className="confirm-dialog-close" onClick={onCancel} disabled={busy} aria-label="关闭确认对话框">
            <X size={18} weight="bold" aria-hidden="true" />
          </button>
        </header>
        {children && <div className="confirm-dialog-body">{children}</div>}
        <footer className="confirm-dialog-actions">
          <button type="button" className="button start-button start-inspector-secondary" onClick={onCancel} disabled={busy}>{cancelLabel}</button>
          <button ref={confirmRef} type="button" className="button start-button button-primary" onClick={onConfirm} disabled={busy} aria-busy={busy || undefined}>{confirmLabel}</button>
        </footer>
      </section>
    </div>
  );
}
