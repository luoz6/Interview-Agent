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
  it("fails closed before status is resolved", () => {
    expect(resolveMemoryUiState().state).toBe("LOADING");
    expect(resolveMemoryUiState({ availability: "available", status: {} }).state).toBe("UNAVAILABLE");
  });

  it("uses the frozen priority order", () => {
    expect(resolveMemoryUiState({ availability: "available", status: status({ deletion_fence_active: true, global_enabled: false }) }).state).toBe("DELETION_PROTECTED");
    expect(resolveMemoryUiState({ availability: "available", status: status({ global_enabled: false, consent: { granted: false, allowed_purposes: [] } }) }).state).toBe("PAUSED");
    expect(resolveMemoryUiState({ availability: "available", status: status({ consent: { granted: false, allowed_purposes: [] } }) }).state).toBe("CONSENT_REQUIRED");
  });

  it("requires the complete local consume capability before reporting active", () => {
    expect(resolveMemoryUiState({ availability: "available", status: status() }).state).toBe("ACTIVE");
    expect(resolveMemoryUiState({ availability: "available", status: status({ mode: "read_shadow", local_consumption_enabled: false }) }).state).toBe("AVAILABLE_NOT_USING");
    expect(resolveMemoryUiState({ availability: "available", status: status({ consent: { granted: true, allowed_purposes: ["fact_storage"] } }) }).state).toBe("AVAILABLE_NOT_USING");
  });
});
