const configuredBase = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");

export class HttpError extends Error {
  constructor(message, { status = 0, body = {} } = {}) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.body = body;
  }
}

export function apiUrl(path) {
  if (/^https?:\/\//.test(path)) return path;
  return `${configuredBase}${path}`;
}

async function safeJson(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

async function parseResponse(response) {
  const body = await safeJson(response);
  if (!response.ok) {
    throw new HttpError(
      body.detail || response.statusText || `请求失败（${response.status}）`,
      { status: response.status, body },
    );
  }
  return body;
}

export async function getJson(path, options = {}) {
  const response = await fetch(apiUrl(path), options);
  return parseResponse(response);
}

export async function postJson(path, payload = {}, options = {}) {
  const response = await fetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    body: JSON.stringify(payload),
    ...options,
  });
  return parseResponse(response);
}

export async function postSse(path, payload, handlers) {
  const response = await fetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return readSse(response, handlers);
}

export async function readSse(response, handlers = {}) {
  if (!response.ok) {
    const body = await safeJson(response);
    throw new HttpError(body.detail || "流式请求失败", {
      status: response.status,
      body,
    });
  }
  if (!response.body) throw new Error("当前浏览器不支持流式响应");

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
    if (["done", "error", "conflict", "reconnect"].includes(event.type)) {
      terminal = event;
    }
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
    const error = new Error("流式响应在结束事件前中断");
    error.lastEventId = lastEventId;
    throw error;
  }
  return terminal;
}

export async function downloadFile(path, filename) {
  const response = await fetch(apiUrl(path));
  if (!response.ok) {
    const body = await safeJson(response);
    throw new HttpError(body.detail || "下载失败", {
      status: response.status,
      body,
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
