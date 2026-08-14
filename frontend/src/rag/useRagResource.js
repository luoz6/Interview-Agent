import { useCallback, useEffect, useState } from "react";

export function useRagResource(loader, { enabled = true } = {}) {
  const [state, setState] = useState({ status: enabled ? "loading" : "idle", data: null, error: null });
  const load = useCallback(async (signal) => {
    if (!enabled) return;
    setState((current) => ({ ...current, status: "loading", error: null }));
    try {
      const data = await loader({ signal });
      if (signal?.aborted) return;
      setState({ status: "success", data, error: null });
    } catch (error) {
      if (!signal?.aborted && error?.code !== "REQUEST_ABORTED") setState({ status: "error", data: null, error });
    }
  }, [enabled, loader]);
  const refresh = useCallback(() => load(), [load]);
  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal);
    return () => controller.abort();
  }, [load]);
  return { ...state, refresh };
}
