import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

afterEach(cleanup);

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

  it("renders reusable inline error feedback inside the dialog", () => {
    render(
      <ConfirmDialog
        open
        title="确认永久删除？"
        description="只有清理完成后才会关闭。"
        errorMessage="当前无法确认永久删除，请稍后重试。"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "确认永久删除？" });
    expect(dialog).toContainElement(screen.getByRole("alert"));
    expect(dialog).toHaveAccessibleDescription(
      "只有清理完成后才会关闭。 当前无法确认永久删除，请稍后重试。",
    );
  });
});
