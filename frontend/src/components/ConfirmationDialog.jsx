import { useRef, useState } from "react";
import { WarningCircle } from "@phosphor-icons/react";

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
  if (!confirmation) return null;
  const titleId = `${idPrefix}-title`;
  const descriptionId = `${idPrefix}-description`;
  return (
    <div className="start-dialog-backdrop" onMouseDown={() => onCancel()}>
      <section
        className="start-confirm-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        data-tone={confirmation.tone || "warning"}
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            onCancel();
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
