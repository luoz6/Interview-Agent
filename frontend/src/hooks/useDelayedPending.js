import { useEffect, useRef, useState } from "react";

export function useDelayedPendingOperation(status, {
  pendingStates,
  delay = 150,
  minimumVisible = 300,
}) {
  const pending = pendingStates.includes(status);
  const [visibleState, setVisibleState] = useState({ showSpinner: false, operation: null });
  const shownAtRef = useRef(0);

  useEffect(() => {
    let timer;
    if (pending) {
      if (visibleState.showSpinner && visibleState.operation === status) return undefined;
      timer = window.setTimeout(() => {
        shownAtRef.current = performance.now();
        setVisibleState({ showSpinner: true, operation: status });
      }, delay);
    } else if (visibleState.showSpinner) {
      const elapsed = performance.now() - shownAtRef.current;
      timer = window.setTimeout(() => {
        setVisibleState({ showSpinner: false, operation: null });
      }, Math.max(0, minimumVisible - elapsed));
    } else if (visibleState.operation) {
      setVisibleState({ showSpinner: false, operation: null });
    }
    return () => window.clearTimeout(timer);
  }, [delay, minimumVisible, pending, status, visibleState.operation, visibleState.showSpinner]);

  return visibleState;
}
