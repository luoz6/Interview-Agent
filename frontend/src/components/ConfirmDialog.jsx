import { useEffect, useId, useLayoutEffect, useRef } from "react";
import { WarningCircle, X } from "@phosphor-icons/react";

const FOCUSABLE = [
  "button:not([disabled]):not([tabindex='-1'])",
  "a[href]:not([tabindex='-1'])",
  "input:not([disabled]):not([tabindex='-1'])",
  "select:not([disabled]):not([tabindex='-1'])",
  "textarea:not([disabled]):not([tabindex='-1'])",
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
  role = "dialog",
  busy = false,
  onConfirm,
  onCancel,
}) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef(null);
  const cancelRef = useRef(null);
  const returnFocusRef = useRef(null);
  const busyRef = useRef(busy);
  const onCancelRef = useRef(onCancel);

  useEffect(() => {
    busyRef.current = busy;
    onCancelRef.current = onCancel;
  }, [busy, onCancel]);

  useLayoutEffect(() => {
    if (!open) return undefined;
    returnFocusRef.current = document.activeElement;
    cancelRef.current?.focus();
    const onKeyDown = (event) => {
      if (event.key === "Escape" && !busyRef.current) {
        event.preventDefault();
        onCancelRef.current?.();
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
    const onFocusIn = (event) => {
      if (!dialogRef.current?.contains(event.target)) cancelRef.current?.focus();
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("focusin", onFocusIn);
      returnFocusRef.current?.focus?.();
    };
  }, [open]);

  if (!open) return null;
  const confirmVariant = tone === "danger" ? "button-danger" : "button-primary";
  return (
    <div className="confirm-dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget && !busy) onCancel?.();
    }}>
      <section
        ref={dialogRef}
        className="confirm-dialog"
        data-tone={tone}
        role={role}
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
          <button type="button" className="confirm-dialog-close" onClick={onCancel} disabled={busy} tabIndex={-1} aria-label="关闭确认对话框">
            <X size={18} weight="bold" aria-hidden="true" />
          </button>
        </header>
        {children && <div className="confirm-dialog-body">{children}</div>}
        <footer className="confirm-dialog-actions">
          <button ref={cancelRef} type="button" className="button start-button start-inspector-secondary" onClick={onCancel} disabled={busy}>{cancelLabel}</button>
          <button type="button" className={`button start-button ${confirmVariant}`} onClick={onConfirm} disabled={busy} aria-busy={busy || undefined}>{confirmLabel}</button>
        </footer>
      </section>
    </div>
  );
}
