import { beforeEach, describe, expect, it } from "vitest";
import { clearStableRequestId, stableRequestId } from "./client";

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
