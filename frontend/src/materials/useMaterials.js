import { useCallback, useEffect, useState } from "react";
import {
  deleteMaterial,
  listMaterials,
  patchMaterial,
  retryMaterial,
  uploadMaterial,
} from "./materialsApi";
import { isMaterialsUnavailable, materialErrorMessage } from "./materialsDisplay";

export function useMaterials() {
  const [items, setItems] = useState([]);
  const [availability, setAvailability] = useState("loading");
  const [ingestAvailable, setIngestAvailable] = useState(true);
  const [busy, setBusy] = useState({});
  const [notice, setNotice] = useState(null);

  const refresh = useCallback(async (signal) => {
    setAvailability("loading");
    try {
      const next = await listMaterials({ signal });
      setItems(next);
      setAvailability("ready");
    } catch (error) {
      if (error?.code === "REQUEST_ABORTED") return;
      if (isMaterialsUnavailable(error)) {
        setAvailability("unavailable");
        return;
      }
      setAvailability("error");
      setNotice({
        tone: "error",
        text: materialErrorMessage(error, "资料列表暂时无法加载，请稍后重试。"),
      });
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  const setItemBusy = useCallback((documentId, action) => {
    setBusy((current) => ({ ...current, [documentId]: action }));
  }, []);

  const clearItemBusy = useCallback((documentId) => {
    setBusy((current) => {
      const next = { ...current };
      delete next[documentId];
      return next;
    });
  }, []);

  const replaceItem = useCallback((replacement) => {
    setItems((current) => current.map((item) => (
      item.documentId === replacement.documentId ? replacement : item
    )));
  }, []);

  const update = useCallback(async (documentId, changes, successText) => {
    setItemBusy(documentId, "update");
    try {
      replaceItem(await patchMaterial(documentId, changes));
      setNotice({ tone: "success", text: successText });
      return { ok: true };
    } catch (error) {
      const message = materialErrorMessage(error);
      setNotice({ tone: "error", text: message });
      return { ok: false, message };
    } finally {
      clearItemBusy(documentId);
    }
  }, [clearItemBusy, replaceItem, setItemBusy]);

  const retry = useCallback(async (documentId) => {
    setItemBusy(documentId, "retry");
    try {
      replaceItem(await retryMaterial(documentId));
      setNotice({ tone: "success", text: "已重新开始处理资料。" });
      return { ok: true };
    } catch (error) {
      if (isMaterialsUnavailable(error)) setIngestAvailable(false);
      const message = isMaterialsUnavailable(error)
        ? "资料上传与重新处理当前未启用。"
        : materialErrorMessage(error);
      setNotice({ tone: "error", text: message });
      return { ok: false, message };
    } finally {
      clearItemBusy(documentId);
    }
  }, [clearItemBusy, replaceItem, setItemBusy]);

  const upload = useCallback(async (file, displayName) => {
    setBusy((current) => ({ ...current, upload: "upload" }));
    try {
      const created = await uploadMaterial({ file, displayName });
      setItems((current) => [created, ...current]);
      setNotice({ tone: "success", text: "资料已上传，正在处理中。" });
      return { ok: true, item: created };
    } catch (error) {
      if (isMaterialsUnavailable(error)) setIngestAvailable(false);
      const message = isMaterialsUnavailable(error)
        ? "资料上传与重新处理当前未启用。"
        : materialErrorMessage(error, "资料上传未完成，请检查文件后重试。");
      setNotice({ tone: "error", text: message });
      return { ok: false, message };
    } finally {
      setBusy((current) => {
        const next = { ...current };
        delete next.upload;
        return next;
      });
    }
  }, []);

  const remove = useCallback(async (documentId) => {
    setItemBusy(documentId, "delete");
    try {
      await deleteMaterial(documentId);
      setItems((current) => current.filter((item) => item.documentId !== documentId));
      setNotice({ tone: "success", text: "资料及其索引已永久删除。" });
      return { ok: true };
    } catch (error) {
      const message = materialErrorMessage(error, "资料删除未完成，请稍后重试。");
      setNotice({ tone: "error", text: message });
      return { ok: false, message };
    } finally {
      clearItemBusy(documentId);
    }
  }, [clearItemBusy, setItemBusy]);

  return {
    items,
    availability,
    ingestAvailable,
    busy,
    notice,
    dismissNotice: () => setNotice(null),
    refresh: () => refresh(),
    upload,
    update,
    retry,
    remove,
  };
}
