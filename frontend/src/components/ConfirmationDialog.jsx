import { useEffect, useRef, useState } from "react";
import { WarningCircle } from "@phosphor-icons/react";

const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "a[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function scheduleFocus(callback) {
  if (typeof window.requestAnimationFrame === "function") {
    window.requestAnimationFrame(callback);
    return;
  }
  window.setTimeout(callback, 0);
}

export function useConfirmationDialog() {
  const [confirmation, setConfirmation] = useState(null);
  const triggerRef = useRef(null);

  function openConfirmation(
    nextConfirmation,
    trigger = typeof document === "undefined" ? null : document.activeElement,
  ) {
    triggerRef.current = typeof HTMLElement !== "undefined" && trigger instanceof HTMLElement
      ? trigger
      : null;
    setConfirmation(nextConfirmation);
  }

  function closeConfirmation({ restoreFocus = true } = {}) {
    const trigger = triggerRef.current;
    triggerRef.current = null;
    setConfirmation(null);
    if (!restoreFocus) return;
    scheduleFocus(() => {
      if (trigger?.isConnected && !trigger.disabled) trigger.focus();
    });
  }

  return { confirmation, openConfirmation, closeConfirmation };
}

export function ConfirmationDialog({ confirmation, onCancel, idPrefix = "confirmation" }) {
  const dialogRef = useRef(null);

  useEffect(() => {
    if (!confirmation) return undefined;
    const dialog = dialogRef.current;
    if (!dialog) return undefined;
    const keepFocusInside = (event) => {
      if (dialog.contains(event.target)) return;
      const firstFocusable = dialog.querySelector(FOCUSABLE_SELECTOR);
      (firstFocusable || dialog).focus();
    };
    document.addEventListener("focusin", keepFocusInside);
    return () => document.removeEventListener("focusin", keepFocusInside);
  }, [confirmation]);

  if (!confirmation) return null;
  const titleId = `${idPrefix}-title`;
  const descriptionId = `${idPrefix}-description`;
  return (
    <div className="start-dialog-backdrop" onMouseDown={() => onCancel()}>
      <section
        ref={dialogRef}
        className="start-confirm-dialog"
        role="dialog"
        tabIndex="-1"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        data-tone={confirmation.tone || "warning"}
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            onCancel();
            return;
          }
          if (event.key === "Tab") {
            const focusable = [...event.currentTarget.querySelectorAll(FOCUSABLE_SELECTOR)];
            if (!focusable.length) {
              event.preventDefault();
              event.currentTarget.focus();
              return;
            }
            const first = focusable[0];
            const last = focusable[focusable.length - 1];
            if (event.shiftKey && (document.activeElement === first || !event.currentTarget.contains(document.activeElement))) {
              event.preventDefault();
              last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
              event.preventDefault();
              first.focus();
            }
          }
        }}
      >
        <header>
          <span aria-hidden="true">
            <WarningCircle size={22} weight="fill" />
          </span>
          <div>
            <span>需要确认</span>
            <h2 id={titleId}>{confirmation.title}</h2>
          </div>
        </header>
        <p id={descriptionId}>{confirmation.description}</p>
        {confirmation.details?.length ? (
          <ul>
            {confirmation.details.map((detail) => (
              <li key={detail}>{detail}</li>
            ))}
          </ul>
        ) : null}
        <footer>
          <button type="button" className="start-dialog-secondary" onClick={() => onCancel()} autoFocus>
            取消
          </button>
          <button
            type="button"
            className="start-dialog-primary"
            onClick={confirmation.onConfirm}
          >
            {confirmation.confirmLabel || "确认"}
          </button>
        </footer>
      </section>
    </div>
  );
}
