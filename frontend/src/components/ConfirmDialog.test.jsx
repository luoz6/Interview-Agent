import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

describe("ConfirmDialog", () => {
  it("uses the destructive shared button treatment for a danger confirmation", () => {
    render(
      <ConfirmDialog
        open
        title="确认永久删除？"
        confirmLabel="确认永久删除"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "确认永久删除" })).toHaveClass("button-danger");
  });

  it("keeps non-danger confirmations on the primary action treatment", () => {
    render(
      <ConfirmDialog
        open
        tone="warning"
        title="确认继续？"
        confirmLabel="继续"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "继续" })).toHaveClass("button-primary");
  });
});
