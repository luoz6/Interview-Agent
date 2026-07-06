export function getSessionId() {
  return new URLSearchParams(window.location.search).get("session_id");
}

export async function getJson(url) {
  const response = await fetch(url);
  return parseJsonResponse(response);
}

export async function postJson(url, payload = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseJsonResponse(response);
}

export async function parseJsonResponse(response) {
  const body = await safeJson(response);
  if (!response.ok) {
    throw new Error(body.detail || response.statusText || `Request failed with ${response.status}`);
  }
  return body;
}

export async function safeJson(response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

export async function readSse(response, handlers) {
  if (!response.ok) {
    const body = await safeJson(response);
    throw new Error(body.detail || response.statusText || `Request failed with ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const rawEvent of events) {
      const event = parseSseEvent(rawEvent);
      if (event && handlers[event.event]) {
        handlers[event.event](event.data);
      }
    }
  }
}

function parseSseEvent(rawEvent) {
  const event = { event: "message", data: {} };
  for (const line of rawEvent.split("\n")) {
    if (line.startsWith("event:")) {
      event.event = line.slice("event:".length).trim();
    }
    if (line.startsWith("data:")) {
      event.data = JSON.parse(line.slice("data:".length).trim());
    }
  }
  return event;
}

export async function downloadPdf(url, filename) {
  const response = await fetch(url);
  if (!response.ok) {
    const body = await safeJson(response);
    throw new Error(body.detail || response.statusText || "PDF download failed");
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}
