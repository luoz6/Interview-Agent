import { useCallback, useEffect, useRef, useState } from "react";
import { deleteJson, getJson, postJson, putJson } from "../api/client";
import { memoryErrorMessage } from "./memoryErrors";
import { resolveMemoryUiState } from "./memoryStatus";

const API = "/api/runtime/principal-memory";
const mutationOptions = { headers: { "X-Local-Memory-Action": "1" } };

export function useMemoryCenter() {
  const [status, setStatus] = useState(null);
  const [capabilities, setCapabilities] = useState(null);
  const [facts, setFacts] = useState([]);
  const [summary, setSummary] = useState({});
  const [nextCursor, setNextCursor] = useState(null);
  const [availability, setAvailability] = useState("loading");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState(null);
  const noticeTimer = useRef(null);

  const announce = useCallback((tone, message) => {
    setNotice({ tone, message });
    window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(null), 5200);
  }, []);

  const loadStatus = useCallback(async () => {
    const next = await getJson(`${API}/status`, { cache: "no-store" });
    setStatus(next);
    return next;
  }, []);

  const loadFacts = useCallback(async ({ cursor = null, append = false } = {}) => {
    const query = new URLSearchParams({ limit: "50" });
    if (cursor) query.set("cursor", cursor);
    const next = await getJson(`${API}/facts?${query}`, { cache: "no-store" });
    setFacts((current) => append ? [...current, ...(next.items || [])] : (next.items || []));
    setSummary(next.summary || {});
    setNextCursor(next.next_cursor || null);
    return next;
  }, []);

  const refresh = useCallback(async () => {
    setAvailability("loading");
    try {
      const [, nextCapabilities] = await Promise.all([
        loadStatus(),
        getJson(`${API}/capabilities`, { cache: "no-store" }),
        loadFacts(),
      ]);
      setCapabilities(nextCapabilities);
      setAvailability("available");
    } catch (error) {
      setStatus(null);
      setCapabilities(null);
      setFacts([]);
      setSummary({});
      setNextCursor(null);
      setAvailability("unavailable");
      announce("error", memoryErrorMessage(error));
    }
  }, [announce, loadFacts, loadStatus]);

  useEffect(() => {
    refresh();
    return () => window.clearTimeout(noticeTimer.current);
  }, [refresh]);

  const run = useCallback(async (name, action, {
    status: reloadStatus = false,
    facts: reloadFacts = false,
    success,
    announceError = true,
  } = {}) => {
    setBusy(name);
    try {
      const result = await action();
      if (reloadStatus) await loadStatus();
      if (reloadFacts) await loadFacts();
      const successMessage = typeof success === "function" ? success(result) : success;
      if (successMessage) announce("success", successMessage);
      return { ok: true, result };
    } catch (error) {
      if (announceError) announce("error", memoryErrorMessage(error));
      if (reloadStatus) await loadStatus().catch(() => {});
      if (reloadFacts) await loadFacts().catch(() => {});
      return { ok: false, error };
    } finally {
      setBusy("");
    }
  }, [announce, loadFacts, loadStatus]);

  return {
    status,
    capabilities,
    facts,
    summary,
    nextCursor,
    availability,
    uiState: resolveMemoryUiState({ status, availability }),
    busy,
    notice,
    refresh,
    loadMore: () => run("load-more", () => loadFacts({ cursor: nextCursor, append: true })),
    toggle: () => run("toggle", () => postJson(`${API}/${status?.global_enabled ? "disable" : "enable"}`, {}, mutationOptions), { status: true, success: status?.global_enabled ? "长期记忆已暂停，已有信息仍会保留。" : "长期记忆已重新启用。" }),
    saveConsent: (purposes) => run("consent", () => putJson(`${API}/consent`, { allowed_purposes: purposes }, mutationOptions), { status: true, success: "使用范围已保存。" }),
    revokeConsent: () => run("revoke-consent", () => deleteJson(`${API}/consent`, mutationOptions), { status: true, success: "已撤回全部许可，已有信息仍会保留。" }),
    declareFact: (factType, key, value) => run("declare", () => postJson(`${API}/facts`, { fact_type: factType, normalized_value: { [key]: value } }, mutationOptions), { facts: true, success: "信息已加入你的记忆。" }),
    actFact: (item, action) => run(`fact-${item.safe_ref}`, () => postJson(`${API}/facts/${item.safe_ref}/${action}`, { expected_version: item.version }, mutationOptions), { facts: true, success: action === "confirm" ? "这条信息已确认，之后可按你的许可使用。" : action === "reject" ? "已拒绝这条待确认信息。" : "已撤回这条信息。" }),
    correctFact: (item, key, value) => run(`edit-${item.safe_ref}`, () => putJson(`${API}/facts/${item.safe_ref}`, { expected_version: item.version, normalized_value: { [key]: value } }, mutationOptions), { facts: true, success: "更正已保存，旧版本会保留在历史记录中。" }),
    exportMemory: () => run("export", async () => {
      const data = await postJson(`${API}/export`, {}, mutationOptions);
      const url = URL.createObjectURL(new Blob([JSON.stringify(data.payload, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "interview-agent-memory-export.json";
      link.click();
      URL.revokeObjectURL(url);
    }, { success: "安全导出已生成。" }),
    deleteMemory: () => run("delete", () => deleteJson(API, mutationOptions), {
      status: true,
      facts: true,
      announceError: false,
      success: (result) => result?.status === "completed" && result?.residue_count === 0
        ? "记忆已删除。"
        : null,
    }),
  };
}
