import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearStableRequestId,
  getJson,
  HttpError,
  postForm,
  postJson,
  stableRequestId,
} from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("JSON response parsing", () => {
  it("accepts a json-only Response-like success without consuming it twice", async () => {
    const response = {
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({ revision: 1 }),
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(postJson("/api/prep", { source: "test" })).resolves.toEqual({ revision: 1 });
    expect(response.json).toHaveBeenCalledTimes(1);
  });

  it("preserves status and body for a json-only Response-like error", async () => {
    const body = { code: "plan_revision_conflict", current_revision: 2 };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: vi.fn().mockResolvedValue(body),
    }));

    const error = await getJson("/api/interview-plans/example").catch((caught) => caught);
    expect(error).toBeInstanceOf(HttpError);
    expect(error).toMatchObject({ status: 409, body });
  });

  it("prefers text for a standard Response and does not call json", async () => {
    const response = {
      ok: true,
      status: 200,
      headers: { get: vi.fn().mockReturnValue(null) },
      text: vi.fn().mockResolvedValue('{"revision":3}'),
      json: vi.fn(),
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(getJson("/api/interview-plans/example")).resolves.toEqual({ revision: 3 });
    expect(response.text).toHaveBeenCalledTimes(1);
    expect(response.json).not.toHaveBeenCalled();
  });
});

describe("multipart requests", () => {
  it("posts FormData without overriding the browser multipart boundary", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: vi.fn().mockResolvedValue({ document_id: "document-1" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const formData = new FormData();
    formData.append("display_name", "系统设计笔记");

    await expect(postForm("/api/materials", formData)).resolves.toEqual({
      document_id: "document-1",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/materials",
      expect.objectContaining({ method: "POST", body: formData }),
    );
    const options = fetchMock.mock.calls[0][1];
    expect(options.headers).toBeUndefined();
  });
});

describe("stableRequestId", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("reuses one request identity for retries in the same scope", () => {
    const first = stableRequestId("session-start:revision-1");
    const retry = stableRequestId("session-start:revision-1");
    const otherRevision = stableRequestId("session-start:revision-2");

    expect(retry).toBe(first);
    expect(otherRevision).not.toBe(first);
    expect(
      window.sessionStorage.getItem(
        "interview-agent:request-id:session-start:revision-1",
      ),
    ).toBe(first);

    clearStableRequestId("session-start:revision-1");
    expect(stableRequestId("session-start:revision-1")).not.toBe(first);
  });
});
