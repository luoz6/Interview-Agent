import { useRef, useState } from "react";

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
