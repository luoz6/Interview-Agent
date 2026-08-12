import { describe, expect, it } from "vitest";
import { resolveMemoryUiState } from "./memoryStatus";

function status(overrides = {}) {
  return {
    schema_version: "principal-memory-local-status-v1",
    mode: "local_consume",
    global_enabled: true,
    local_consumption_enabled: true,
    deletion_fence_active: false,
    consent: { granted: true, allowed_purposes: ["fact_storage", "local_consume"] },
    ...overrides,
  };
}

describe("resolveMemoryUiState", () => {
  it.each([
    ["LOADING", undefined, "loading"],
    ["UNAVAILABLE", status(), "unavailable"],
    ["DELETION_PROTECTED", status({ deletion_fence_active: true }), "available"],
    ["PAUSED", status({ global_enabled: false }), "available"],
    ["CONSENT_REQUIRED", status({ consent: { granted: false, allowed_purposes: [] } }), "available"],
    ["AVAILABLE_NOT_USING", status({ local_consumption_enabled: false }), "available"],
    ["ACTIVE", status(), "available"],
  ])("resolves the frozen %s state", (expected, input, availability) => {
    expect(resolveMemoryUiState({ status: input, availability }).state).toBe(expected);
  });

  it.each([
    ["LOADING", status({ deletion_fence_active: true }), "loading"],
    ["UNAVAILABLE", {}, "available"],
    ["DELETION_PROTECTED", status({ deletion_fence_active: true, global_enabled: false, consent: { granted: false, allowed_purposes: [] } }), "available"],
    ["PAUSED", status({ global_enabled: false, consent: { granted: false, allowed_purposes: [] }, local_consumption_enabled: false }), "available"],
    ["CONSENT_REQUIRED", status({ consent: { granted: false, allowed_purposes: [] }, local_consumption_enabled: false }), "available"],
  ])("keeps %s ahead of all lower-priority states", (expected, input, availability) => {
    expect(resolveMemoryUiState({ status: input, availability }).state).toBe(expected);
  });

  it.each([
    [status({ mode: "read_shadow", local_consumption_enabled: false }), "shadow mode"],
    [status({ consent: { granted: true, allowed_purposes: ["fact_storage"] } }), "missing local-consume permission"],
    [status({ local_consumption_enabled: false }), "disabled local consumption"],
  ])("fails closed to available-not-using for %s", (input) => {
    expect(resolveMemoryUiState({ availability: "available", status: input }).state).toBe("AVAILABLE_NOT_USING");
  });

  it.each([
    [{ ...status(), schema_version: "principal-memory-local-status-v2" }, "unknown schema"],
    [{ ...status(), mode: "future_mode" }, "unknown mode"],
    [{ ...status(), global_enabled: undefined }, "missing global flag"],
    [{ ...status(), local_consumption_enabled: undefined }, "missing local capability"],
    [{ ...status(), deletion_fence_active: undefined }, "missing deletion fence"],
    [{ ...status(), consent: undefined }, "missing consent"],
    [{ ...status(), consent: { granted: true } }, "missing purposes"],
  ])("treats %s as unavailable instead of guessing", (input) => {
    expect(resolveMemoryUiState({ availability: "available", status: input }).state).toBe("UNAVAILABLE");
  });

  it("handles contradictory but structurally valid combinations by frozen priority", () => {
    expect(resolveMemoryUiState({
      availability: "available",
      status: status({
        global_enabled: false,
        local_consumption_enabled: true,
        consent: { granted: true, allowed_purposes: ["local_consume"] },
      }),
    }).state).toBe("PAUSED");
    expect(resolveMemoryUiState({
      availability: "available",
      status: status({
        consent: { granted: false, allowed_purposes: ["local_consume"] },
      }),
    }).state).toBe("CONSENT_REQUIRED");
  });
});
