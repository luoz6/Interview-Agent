import { afterEach, describe, expect, it, vi } from "vitest";
import { retryMaterial, uploadMaterial } from "./materialsApi";

const DOCUMENT_ID = "11111111-1111-4111-8111-111111111111";

function material(status) {
  return {
    document_id: DOCUMENT_ID,
    display_name: "Redis notes",
    media_type: "text/plain",
    size_bytes: 12,
    status,
    enabled: status !== "disabled",
    allowed_usage: ["question"],
    created_at: "2026-08-15T08:00:00Z",
    updated_at: "2026-08-15T08:00:00Z",
    error_code: status === "failed" ? "processing_failed" : null,
  };
}

function response(payload) {
  return Promise.resolve({
    ok: true,
    status: 200,
    headers: { get: () => null },
    json: async () => payload,
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("materials mutation response contract", () => {
  it.each(["ready", "failed"])("accepts terminal %s responses for upload and retry", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => response(material(status))));

    const uploaded = await uploadMaterial({
      file: new File(["Redis"], "redis.txt", { type: "text/plain" }),
      displayName: "Redis notes",
    });
    const retried = await retryMaterial(DOCUMENT_ID);

    expect(uploaded.status).toBe(status);
    expect(retried.status).toBe(status);
  });

  it("keeps unexpected processing responses readable for compatibility", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => response(material("processing"))));

    const uploaded = await uploadMaterial({
      file: new File(["Redis"], "redis.txt", { type: "text/plain" }),
      displayName: "Redis notes",
    });
    const retried = await retryMaterial(DOCUMENT_ID);

    expect(uploaded.status).toBe("processing");
    expect(retried.status).toBe("processing");
  });

  it.each(["disabled", "deleting"])("rejects non-terminal mutation status %s", async (status) => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => response(material(status))));

    await expect(uploadMaterial({
      file: new File(["Redis"], "redis.txt", { type: "text/plain" }),
      displayName: "Redis notes",
    })).rejects.toThrow("Invalid materials mutation response");
    await expect(retryMaterial(DOCUMENT_ID)).rejects.toThrow("Invalid materials mutation response");
  });
});
