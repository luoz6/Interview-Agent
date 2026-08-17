const configuredBase = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
const DEFAULT_TIMEOUT_MS = 20000;
const volatileRequestIds = new Map();

const statusMessages = {
  400: "请求内容不完整或格式不正确，请检查后重试。",
  401: "当前操作需要重新验证身份。",
  403: "当前环境不允许执行此操作。",
  404: "请求的内容不存在，可能已失效或被删除。",
  409: "内容已在其他位置更新，请加载最新状态后重试。",
  410: "请求的内容已过期，请重新开始。",
  422: "部分内容未通过校验，请检查标记项。",
  429: "请求过于频繁，请稍后再试。",
  500: "服务暂时无法完成请求，请稍后重试。",
  502: "服务连接暂时不可用，请稍后重试。",
  503: "服务正在恢复中，请稍后重试。",
  504: "服务响应超时，请稍后重试。",
};

export class HttpError extends Error {
  constructor(message, {
    status = 0,
    body = {},
    code,
    retryable,
    requestId,
    cause,
  } = {}) {
    super(message, cause ? { cause } : undefined);
    this.name = "HttpError";
    this.status = status;
    this.body = body;
    this.code = code || body?.code || body?.detail?.code || (status ? `HTTP_${status}` : "REQUEST_FAILED");
    this.retryable = retryable ?? (status === 0 || status === 408 || status === 429 || status >= 500);
    this.requestId = requestId || body?.request_id || body?.detail?.request_id || null;
  }
}

export function apiUrl(path) {
  if (/^https?:\/\//.test(path)) return path;
  return `${configuredBase}${path}`;
}

export function stableRequestId(scope) {
  const storageKey = `interview-agent:request-id:${scope}`;
  const volatile = volatileRequestIds.get(storageKey);
  if (volatile) return volatile;
  try {
    const existing = globalThis.sessionStorage?.getItem(storageKey);
    if (existing) return existing;
  } catch {
    // Storage may be unavailable in privacy-restricted browser contexts.
  }
  const generated = globalThis.crypto?.randomUUID?.()
    || `request-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  volatileRequestIds.set(storageKey, generated);
  try {
    globalThis.sessionStorage?.setItem(storageKey, generated);
  } catch {
    // The module-local value still makes retries within this page lifetime safe.
  }
  return generated;
}

export function clearStableRequestId(scope) {
  const storageKey = `interview-agent:request-id:${scope}`;
  volatileRequestIds.delete(storageKey);
  try {
    globalThis.sessionStorage?.removeItem(storageKey);
  } catch {
    // A completed request no longer needs browser-backed replay state.
  }
}

function publicMessage(status, body) {
  const detail = body?.detail;
  const explicitlyPublic = body?.code || (detail && typeof detail === "object" && detail.code);
  if (explicitlyPublic) {
    return body.message || detail?.message || (typeof detail === "string" ? detail : null) || statusMessages[status];
  }
  if (status >= 500) return statusMessages[status] || statusMessages[500];
  return (typeof detail === "string" && detail.trim()) || body?.message || statusMessages[status] || `请求失败（${status}）`;
}

async function readBody(response) {
  if (typeof response.text === "function") {
    const text = await response.text();
    if (!text) return { body: {}, validJson: true };
    try {
      return { body: JSON.parse(text), validJson: true };
    } catch {
      return { body: {}, validJson: false };
    }
  }
  if (typeof response.json === "function") {
    try {
      return { body: await response.json(), validJson: true };
    } catch {
      return { body: {}, validJson: false };
    }
  }
  return { body: {}, validJson: false };
}

async function parseResponse(response) {
  const { body, validJson } = await readBody(response);
  const requestId = response.headers?.get?.("x-request-id") || null;
  if (!response.ok) {
    throw new HttpError(publicMessage(response.status, body), {
      status: response.status,
      body,
      retryable: body?.retryable ?? body?.detail?.retryable,
      requestId,
    });
  }
  if (!validJson) {
    throw new HttpError("服务返回了无法识别的数据，请重新加载后再试。", {
      status: response.status,
      body: {},
      code: "INVALID_RESPONSE",
      retryable: true,
      requestId,
    });
  }
  return body;
}

async function request(path, options = {}) {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, signal, ...fetchOptions } = options;
  const controller = new AbortController();
  let timedOut = false;
  const abortFromCaller = () => controller.abort(signal?.reason);
  if (signal?.aborted) abortFromCaller();
  else signal?.addEventListener("abort", abortFromCaller, { once: true });
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    return await fetch(apiUrl(path), { ...fetchOptions, signal: controller.signal });
  } catch (error) {
    if (timedOut) {
      throw new HttpError("请求等待时间过长，请检查网络后重试。", {
        code: "REQUEST_TIMEOUT",
        retryable: true,
        cause: error,
      });
    }
    if (signal?.aborted || error?.name === "AbortError") {
      throw new HttpError("请求已取消。", {
        code: "REQUEST_ABORTED",
        retryable: false,
        cause: error,
      });
    }
    throw new HttpError("无法连接服务，请检查网络或确认服务已经启动。", {
      code: navigator.onLine === false ? "OFFLINE" : "CONNECTION_FAILED",
      retryable: true,
      cause: error,
    });
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener("abort", abortFromCaller);
  }
}

export async function getJson(path, options = {}) {
  return parseResponse(await request(path, options));
}

export async function postJson(path, payload = {}, options = {}) {
  return parseResponse(await request(path, {
    ...options,
    method: "POST",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    body: JSON.stringify(payload),
  }));
}

export async function postForm(path, formData, options = {}) {
  return parseResponse(await request(path, {
    ...options,
    method: "POST",
    body: formData,
  }));
}

export async function patchJson(path, payload = {}, options = {}) {
  return parseResponse(await request(path, {
    ...options,
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    body: JSON.stringify(payload),
  }));
}

export async function putJson(path, payload = {}, options = {}) {
  return parseResponse(await request(path, {
    ...options,
    method: "PUT",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    body: JSON.stringify(payload),
  }));
}

export async function deleteJson(path, options = {}) {
  return parseResponse(await request(path, { ...options, method: "DELETE" }));
}

export async function postSse(path, payload, handlers, options = {}) {
  const response = await request(path, {
    ...options,
    timeoutMs: options.timeoutMs || 60000,
    method: "POST",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    body: JSON.stringify(payload),
  });
  return readSse(response, handlers);
}

export async function readSse(response, handlers = {}) {
  if (!response.ok) {
    const { body } = await readBody(response);
    throw new HttpError(publicMessage(response.status, body) || "流式请求失败", {
      status: response.status,
      body,
      requestId: response.headers.get("x-request-id"),
    });
  }
  if (!response.body) {
    throw new HttpError("当前浏览器不支持流式响应。", {
      code: "STREAM_UNSUPPORTED",
      retryable: false,
    });
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminal = null;
  let lastEventId = null;

  const dispatch = (block) => {
    const event = { type: "message", data: {}, id: null };
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event.type = line.slice(6).trim();
      if (line.startsWith("id:")) event.id = line.slice(3).trim();
      if (line.startsWith("data:")) {
        const raw = line.slice(5).trim();
        try {
          event.data = JSON.parse(raw);
        } catch {
          event.data = { detail: raw };
        }
      }
    }
    lastEventId = event.id || lastEventId;
    handlers[event.type]?.(event.data, event.id);
    if (["done", "error", "conflict", "reconnect"].includes(event.type)) terminal = event;
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    blocks.filter(Boolean).forEach(dispatch);
  }
  if (buffer.trim()) dispatch(buffer);
  if (!terminal) {
    const error = new HttpError("流式响应在完成前中断，请重新连接。", {
      code: "STREAM_INTERRUPTED",
      retryable: true,
    });
    error.lastEventId = lastEventId;
    throw error;
  }
  return terminal;
}

export async function downloadFile(path, filename) {
  const response = await request(path, { timeoutMs: 60000 });
  if (!response.ok) {
    const { body } = await readBody(response);
    throw new HttpError(publicMessage(response.status, body) || "下载失败", {
      status: response.status,
      body,
      requestId: response.headers.get("x-request-id"),
    });
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}
